"""Audit logic shared by the audit product's sources: SQL tokenizing, redaction, masking and classification.

Pure Python (standard library only, Python 3.10), no Spark: the notebooks call these
functions on the driver, and the tests call them directly. Rules are numbered as in the
functional design of intent ``20261125-starrocks-query-audit`` (``BR-n``).

The StarRocks bronze contract (``BRONZE_COLUMNS``) and the silver columns
(``SILVER_EVENT_COLUMNS``, ``TABLE_ACCESS_COLUMNS``) live here too, so every notebook
uses the same definitions.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Iterator, Mapping, Optional

# --- Contracts ------------------------------------------------------------------------

# Bronze table written by bronze/bronze_ingestion.py from the StarRocks staging table (Application Design AD-1).
BRONZE_COLUMNS = (
    ("query_id", "STRING"),
    ("event_time", "TIMESTAMP"),
    ("payload", "STRING"),
    ("copied_at", "TIMESTAMP"),
    ("copy_task", "STRING"),
)

SILVER_EVENT_COLUMNS = (
    ("event_key", "STRING"),
    ("event_type", "STRING"),
    ("event_time", "TIMESTAMP"),
    ("event_date", "DATE"),
    ("user", "STRING"),
    ("authorized_user", "STRING"),
    ("actor_type", "STRING"),
    ("client_ip", "STRING"),
    ("fe_host", "STRING"),
    ("catalog", "STRING"),
    ("database", "STRING"),
    ("data_product", "STRING"),
    ("environment", "STRING"),
    ("statement_category", "STRING"),
    ("query_type", "STRING"),
    ("is_query", "BOOLEAN"),
    ("state", "STRING"),
    ("failed", "BOOLEAN"),
    ("error_code", "STRING"),
    ("query_time_ms", "BIGINT"),
    ("scan_rows", "BIGINT"),
    ("scan_bytes", "BIGINT"),
    ("return_rows", "BIGINT"),
    ("cpu_cost_ns", "BIGINT"),
    ("mem_cost_bytes", "BIGINT"),
    ("digest", "STRING"),
    ("sql_masked", "STRING"),
    ("sql_text", "STRING"),
    ("payload", "STRING"),
    ("copy_task", "STRING"),
    ("first_copied_at", "TIMESTAMP"),
    ("loaded_at", "TIMESTAMP"),
)

TABLE_ACCESS_COLUMNS = (
    ("event_key", "STRING"),
    ("event_time", "TIMESTAMP"),
    ("event_date", "DATE"),
    ("user", "STRING"),
    ("actor_type", "STRING"),
    ("statement_category", "STRING"),
    ("ref_catalog", "STRING"),
    ("ref_database", "STRING"),
    ("ref_table", "STRING"),
    ("data_product", "STRING"),
    ("environment", "STRING"),
)

EVENT_KEY_COLUMNS = ("event_key", "event_date")
TABLE_ACCESS_KEY_COLUMNS = ("event_key", "ref_catalog", "ref_database", "ref_table", "event_date")

CATEGORIES = ("SELECT", "DML", "DDL", "ADMIN", "METADATA", "SESSION", "OTHER")
EVENT_TYPES = ("query", "connection", "disconnection", "unparsed")
ACTOR_TYPES = ("person", "product_writer", "platform_service", "unknown")
SLOW_QUERY_MS = 60_000  # BR-42
REDACTED = "'***'"
DEFAULT_CATALOG = "default_catalog"
# The StarRocks staging table the AuditLoader plugin writes to (platform); read by the bronze task.
STAGING_TABLE = "starrocks_audit_db__.starrocks_audit_tbl__"

# BR-12: how a payload marks a login (connection) or a logout (disconnection). AuditLoader 5.0.0 on
# StarRocks 4.1.7 writes queryType "connection" or "disconnection" (paired platform intent, probe P-1).
CONNECTION_MARKERS = {"eventtype": ("CONNECTION",), "type": ("CONNECTION",), "querytype": ("CONNECTION",)}
DISCONNECTION_MARKERS = {"eventtype": ("DISCONNECTION",), "type": ("DISCONNECTION",), "querytype": ("DISCONNECTION",)}


# --- Tokens ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """One lexical token: its kind and its exact text."""

    kind: str
    text: str


@dataclass(frozen=True)
class TableRef:
    """A referenced table, lowercase; ``database`` is ``None`` when unknown."""

    catalog: str
    database: Optional[str]
    table: str


@dataclass(frozen=True)
class Scope:
    """A data product and environment; either may be ``None``."""

    data_product: Optional[str] = None
    environment: Optional[str] = None


NON_CODE = frozenset({"space", "line_comment", "block_comment", "hint"})
OPERATORS = ("<=>", ">=", "<=", "<>", "!=", "||", "&&", "::", "->")


def _scan_quoted(sql: str, start: int, quote: str, backslash: bool = True) -> int:
    """End index (exclusive) of the quoted text starting at ``start``; the end of ``sql`` when unterminated."""
    index, length = start + 1, len(sql)
    while index < length:
        char = sql[index]
        if backslash and char == "\\":
            index += 2
            continue
        if char == quote:
            if index + 1 < length and sql[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return length


def _scan_number(sql: str, start: int) -> int:
    """End index (exclusive) of the number starting at ``start`` (decimal, exponent or ``0x`` hex)."""
    length = len(sql)
    if sql.startswith(("0x", "0X"), start) and start + 2 < length and sql[start + 2] in "0123456789abcdefABCDEF":
        index = start + 2
        while index < length and sql[index] in "0123456789abcdefABCDEF":
            index += 1
        return index
    index = start
    while index < length and sql[index].isdigit():
        index += 1
    if index < length and sql[index] == "." and index + 1 < length and sql[index + 1].isdigit():
        index += 1
        while index < length and sql[index].isdigit():
            index += 1
    if index < length and sql[index] in "eE":
        exponent = index + 1
        if exponent < length and sql[exponent] in "+-":
            exponent += 1
        if exponent < length and sql[exponent].isdigit():
            index = exponent
            while index < length and sql[index].isdigit():
                index += 1
    return index


def tokenize(sql: str) -> list:
    """Split SQL text into tokens, losslessly: joining the tokens' text returns ``sql``.

    Kinds: ``space``, ``line_comment``, ``block_comment``, ``hint`` (``/*+ ... */``), ``string``
    (single or double quotes), ``quoted_ident`` (backticks), ``number``, ``word``, ``punct``.
    An unterminated string, comment or identifier runs to the end of the text.

    Args:
        sql: Any text.

    Returns:
        The tokens, in order.
    """
    tokens = []
    index, length = 0, len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            end = index + 1
            while end < length and sql[end].isspace():
                end += 1
            kind = "space"
        elif (sql.startswith("--", index) and (index + 2 >= length or sql[index + 2].isspace())) or char == "#":
            end = sql.find("\n", index)
            end = length if end < 0 else end
            kind = "line_comment"
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = length if end < 0 else end + 2
            kind = "hint" if sql.startswith("/*+", index) else "block_comment"
        elif char in "'\"":
            end, kind = _scan_quoted(sql, index, char), "string"
        elif char == "`":
            end, kind = _scan_quoted(sql, index, "`", backslash=False), "quoted_ident"
        elif char.isdigit() or (char == "." and index + 1 < length and sql[index + 1].isdigit()):
            end, kind = _scan_number(sql, index), "number"
        elif char.isalpha() or char == "_":
            end = index + 1
            while end < length and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            kind = "word"
        else:
            operator = next((op for op in OPERATORS if sql.startswith(op, index)), None)
            end, kind = index + (len(operator) if operator else 1), "punct"
        tokens.append(Token(kind, sql[index:end]))
        index = end
    return tokens


def _code(tokens: Iterable) -> list:
    """The tokens that are code: no whitespace, comments or hints."""
    return [token for token in tokens if token.kind not in NON_CODE]


def _upper(token: Optional[Token]) -> str:
    """A word token's text in upper case; other tokens' text unchanged; ``""`` for ``None``."""
    if token is None:
        return ""
    return token.text.upper() if token.kind == "word" else token.text


def _unquote(text: str) -> str:
    """The text inside one pair of quotes or backticks, if it has them."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"`":
        return text[1:-1]
    return text


# --- Credentials (BR-20) --------------------------------------------------------------

SECRET_KEY = re.compile(r"password|secret|access[._-]?key|token|credential|private[._-]?key", re.IGNORECASE)


def redact_credentials(sql: Optional[str]) -> Optional[str]:
    """Replace credentials in SQL text with ``'***'`` (BR-20); everything else is unchanged.

    Redacted: the string after ``IDENTIFIED BY``, ``IDENTIFIED WITH <plugin> BY|AS`` and
    ``IDENTIFIED BY PASSWORD``; the argument of ``PASSWORD(...)``; the value after ``=`` in
    ``SET PASSWORD`` statements; and the value of any ``key = value`` pair whose key looks
    secret (password, secret, access key, token, credential, private key).

    Args:
        sql: SQL text, or ``None``.

    Returns:
        The redacted text, or ``None``.
    """
    if sql is None:
        return None
    tokens = tokenize(sql)
    positions = [index for index, token in enumerate(tokens) if token.kind not in NON_CODE]
    words = [_upper(tokens[index]) for index in positions]
    set_password = len(words) >= 2 and words[0] == "SET" and words[1] == "PASSWORD"
    for place, index in enumerate(positions):
        if tokens[index].kind != "string":
            continue
        previous = words[place - 1] if place >= 1 else ""
        before = words[place - 2] if place >= 2 else ""
        key = tokens[positions[place - 2]] if place >= 2 else None
        identified = "IDENTIFIED" in words[max(0, place - 4) : place] and previous in ("BY", "AS", "PASSWORD")
        password_call = previous == "(" and before == "PASSWORD"
        secret_value = previous == "=" and (set_password or (key is not None and key.kind in ("word", "string", "quoted_ident") and bool(SECRET_KEY.search(_unquote(key.text)))))
        if identified or password_call or secret_value:
            tokens[index] = Token("string", REDACTED)
    return "".join(token.text for token in tokens)


# --- Masking (BR-1 to BR-4) -----------------------------------------------------------

NO_SPACE_BEFORE = frozenset({",", ")", ".", ";"})
NO_SPACE_AFTER = frozenset({"(", "."})
LIST_TOKENS = frozenset({"?", ",", "-", "+"})


def _collapse_in_lists(parts: list) -> list:
    """``IN ( ? , ? , ... )`` made only of masked literals becomes ``IN ( ? )`` (BR-2)."""
    result, index = [], 0
    while index < len(parts):
        if parts[index].upper() == "IN" and index + 1 < len(parts) and parts[index + 1] == "(":
            close = index + 2
            while close < len(parts) and parts[close] in LIST_TOKENS:
                close += 1
            inner = parts[index + 2 : close]
            if close < len(parts) and parts[close] == ")" and "?" in inner:
                result += [parts[index], "(", "?", ")"]
                index = close + 1
                continue
        result.append(parts[index])
        index += 1
    return result


def _join(parts: list) -> str:
    """Join significant tokens with single spaces, except around ``( ) , . ;``."""
    text, previous = "", None
    for part in parts:
        if previous is None:
            text = part
        elif part in NO_SPACE_BEFORE or previous in NO_SPACE_AFTER:
            text += part
        else:
            text += " " + part
        previous = part
    return text


def mask_sql(sql: Optional[str]) -> Optional[str]:
    """Mask the literals of SQL text (BR-1 to BR-4).

    Strings and numbers become ``?``, literal-only ``IN`` lists become ``IN (?)``, comments are
    removed, hints become ``/*+ ? */``, whitespace collapses to single spaces. Identifiers and
    keywords are kept as written.

    Args:
        sql: SQL text, or ``None``.

    Returns:
        The masked text, ``""`` for blank text, or ``None``.
    """
    if sql is None:
        return None
    parts = []
    for token in tokenize(sql):
        if token.kind in ("space", "line_comment", "block_comment"):
            continue
        if token.kind == "hint":
            parts.append("/*+ ? */")
        elif token.kind in ("string", "number"):
            parts.append("?")
        else:
            parts.append(token.text)
    return _join(_collapse_in_lists(parts))


# --- Categories (BR-5) ----------------------------------------------------------------

SELECT_WORDS = frozenset({"SELECT", "VALUES", "TABLE"})
DML_WORDS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE", "LOAD", "TRUNCATE", "EXPORT"})
DDL_WORDS = frozenset({"CREATE", "ALTER", "DROP", "RENAME", "REFRESH", "CANCEL", "ANALYZE", "SUBMIT", "RECOVER"})
ADMIN_WORDS = frozenset({"GRANT", "REVOKE", "ADMIN", "KILL", "INSTALL", "UNINSTALL"})
METADATA_WORDS = frozenset({"SHOW", "DESC", "DESCRIBE", "EXPLAIN"})
SESSION_WORDS = frozenset({"SET", "USE"})


def _skip_balanced(code: list, index: int) -> int:
    """Index after the parenthesized group opening at ``code[index]``."""
    depth = 0
    while index < len(code):
        if code[index].text == "(":
            depth += 1
        elif code[index].text == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return index


def _with_clause(code: list, index: int) -> tuple:
    """For a ``WITH`` at ``code[index]``: the CTE names and the index of the main statement."""
    names = []
    index += 1
    if _upper(code[index] if index < len(code) else None) == "RECURSIVE":
        index += 1
    while index < len(code):
        names.append(_unquote(code[index].text).lower())
        index += 1
        if index < len(code) and code[index].text == "(":
            index = _skip_balanced(code, index)
        if _upper(code[index] if index < len(code) else None) == "AS":
            index += 1
        if index < len(code) and code[index].text == "(":
            index = _skip_balanced(code, index)
        if index < len(code) and code[index].text == ",":
            index += 1
            continue
        break
    return names, index


def _statement_start(code: list) -> tuple:
    """The CTE names and the index of the statement's first word, past ``(`` and ``WITH ...``."""
    index = 0
    while index < len(code) and code[index].text == "(":
        index += 1
    if _upper(code[index] if index < len(code) else None) == "WITH":
        names, index = _with_clause(code, index)
        while index < len(code) and code[index].text == "(":
            index += 1
        return names, index
    return [], index


def statement_category(sql: Optional[str]) -> Optional[str]:
    """The statement's category (BR-5): SELECT, DML, DDL, ADMIN, METADATA, SESSION or OTHER.

    Args:
        sql: SQL text, or ``None``.

    Returns:
        The category, or ``None`` for ``None``.
    """
    if sql is None:
        return None
    code = _code(tokenize(sql))
    _, index = _statement_start(code)
    if index >= len(code) or code[index].kind != "word":
        return "OTHER"
    first = _upper(code[index])
    second = _upper(code[index + 1]) if index + 1 < len(code) else ""
    if first in ADMIN_WORDS or (first in ("CREATE", "ALTER", "DROP") and second in ("USER", "ROLE")):
        return "ADMIN"
    if (first == "SET" and second in ("PASSWORD", "ROLE", "DEFAULT")) or (first == "ALTER" and second == "SYSTEM"):
        return "ADMIN"
    for category, words in (("METADATA", METADATA_WORDS), ("SESSION", SESSION_WORDS), ("SELECT", SELECT_WORDS), ("DML", DML_WORDS), ("DDL", DDL_WORDS)):
        if first in words:
            return category
    return "OTHER"


# --- Actors (BR-6) --------------------------------------------------------------------

PLATFORM_USERS = frozenset({"root", "admin", "%"})
PRODUCT_WRITER = re.compile(r"srv-.+-writer")


def normalize_user(user: Any) -> Optional[str]:
    """A StarRocks user name without quotes, ``@host`` suffix or case; ``None`` when empty."""
    if user is None:
        return None
    text = str(user).strip()
    if "@" in text:
        text = text.split("@", 1)[0]
    text = text.strip().strip("'\"`").strip().lower()
    return text or None


def actor_type(user: Any) -> str:
    """``person``, ``product_writer``, ``platform_service`` or ``unknown`` (BR-6)."""
    name = normalize_user(user)
    if name is None:
        return "unknown"
    if PRODUCT_WRITER.fullmatch(name):
        return "product_writer"
    if name in PLATFORM_USERS or name.startswith("srv-"):
        return "platform_service"
    return "person"


# --- Scope (BR-7, BR-8) ---------------------------------------------------------------

ENVIRONMENT_SUFFIXES = (("_lhuat", "uat"), ("_lhdev", "dev"), ("_lh", "prod"))
CATALOG_ENVIRONMENTS = {"lakehouse": "prod", "lakehouse_uat": "uat", "lakehouse_dev": "dev"}


def _name(value: Any) -> str:
    return _unquote(str(value).strip()).lower() if value is not None else ""


def infer_scope(catalog: Any, database: Any) -> Scope:
    """The data product and environment a catalog and database name (BR-7).

    Args:
        catalog: A catalog name, or ``None``.
        database: A database (namespace or MV database) name, or ``None``.

    Returns:
        ``Scope(product, environment)`` from a ``<product>_lh``/``_lhuat``/``_lhdev`` database;
        otherwise ``Scope(None, environment)`` from the catalog, or ``Scope()``.
    """
    name = _name(database)
    for suffix, environment in ENVIRONMENT_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return Scope(name[: -len(suffix)], environment)
    return Scope(None, CATALOG_ENVIRONMENTS.get(_name(catalog)))


def _single_or_multiple(values: list) -> Optional[str]:
    distinct = sorted({value for value in values if value})
    if not distinct:
        return None
    return distinct[0] if len(distinct) == 1 else "multiple"


def event_scope(session: Scope, tables: Iterable) -> Scope:
    """An event's data product and environment (BR-8), from its session scope and its tables' scopes."""
    if session.data_product:
        return session
    owned = [scope for scope in tables if scope.data_product]
    if owned:
        return Scope(_single_or_multiple([scope.data_product for scope in owned]), _single_or_multiple([scope.environment for scope in owned]))
    return Scope(None, session.environment)


# --- Referenced tables (BR-9) ---------------------------------------------------------

TABLE_TRIGGERS = frozenset({"FROM", "JOIN", "INTO", "UPDATE", "TABLE", "OVERWRITE"})
GROUP_OPENERS = frozenset({"IN", "EXISTS", "FROM", "JOIN", "AS", "ON", "AND", "OR", "NOT", "WHERE", "SELECT", "UNION", "ALL", "ANY", "SOME", "VALUES", "LATERAL", "INTO", "TABLE", "WITH"})
TABLE_CATEGORIES = frozenset({"SELECT", "DML", "DDL"})
# After FROM or JOIN, a name followed by "(" is a table function; after INTO, TABLE, ... it is a column list.
FUNCTION_TRIGGERS = frozenset({"FROM", "JOIN"})
ALIAS_STOPS = frozenset(
    {
        "WHERE",
        "JOIN",
        "LEFT",
        "RIGHT",
        "INNER",
        "OUTER",
        "CROSS",
        "FULL",
        "SEMI",
        "ANTI",
        "ON",
        "USING",
        "GROUP",
        "ORDER",
        "LIMIT",
        "UNION",
        "EXCEPT",
        "INTERSECT",
        "HAVING",
        "WINDOW",
        "LATERAL",
        "SET",
        "VALUES",
        "SELECT",
        "PARTITION",
        "TABLET",
        "FOR",
    }
)


def _read_name(code: list, index: int) -> tuple:
    """A dotted name of words or backtick identifiers at ``code[index]``: (lowercase parts, next index)."""
    parts = []
    while index < len(code) and code[index].kind in ("word", "quoted_ident"):
        parts.append(_unquote(code[index].text).lower())
        index += 1
        if index < len(code) and code[index].text == "." and index + 1 < len(code) and code[index + 1].kind in ("word", "quoted_ident"):
            index += 1
            continue
        break
    return parts, index


def _qualify(parts: list, catalog: str, database: Optional[str]) -> TableRef:
    parts = parts[-3:]
    if len(parts) == 3:
        return TableRef(parts[0], parts[1], parts[2])
    if len(parts) == 2:
        return TableRef(catalog, parts[0], parts[1])
    return TableRef(catalog, database, parts[0])


def _skip_alias(code: list, index: int) -> int:
    """Index after an optional ``[AS] alias`` at ``code[index]``."""
    if _upper(code[index] if index < len(code) else None) == "AS":
        index += 1
    if index < len(code) and code[index].kind in ("word", "quoted_ident") and _upper(code[index]) not in ALIAS_STOPS:
        index += 1
    return index


def referenced_tables(sql: Optional[str], default_catalog: Any = None, default_database: Any = None) -> list:
    """The tables a SELECT, DML or DDL statement names, qualified and lowercase (BR-9); best effort.

    Args:
        sql: SQL text, or ``None``.
        default_catalog: The session catalog; ``default_catalog`` when unknown.
        default_database: The session database, or ``None``.

    Returns:
        Distinct ``TableRef`` values in order of first appearance; ``[]`` for other statements.
    """
    if sql is None or statement_category(sql) not in TABLE_CATEGORIES:
        return []
    code = _code(tokenize(sql))
    ctes, _ = _statement_start(code)
    catalog = _name(default_catalog) or DEFAULT_CATALOG
    database = _name(default_database) or None
    found, stack = [], []
    for index, token in enumerate(code):
        if token.text == "(":
            previous = code[index - 1] if index else None
            following = _upper(code[index + 1]) if index + 1 < len(code) else ""
            is_function = previous is not None and previous.kind == "word" and _upper(previous) not in GROUP_OPENERS and following not in ("SELECT", "WITH")
            stack.append("function" if is_function else "group")
            continue
        if token.text == ")":
            if stack:
                stack.pop()
            continue
        word = _upper(token)
        trigger = word in TABLE_TRIGGERS or (word == "VIEW" and index and _upper(code[index - 1]) == "MATERIALIZED")
        if not trigger or (stack and stack[-1] == "function"):
            continue
        start = index + 1
        following = _upper(code[start] if start < len(code) else None)
        if following in TABLE_TRIGGERS or following == "MATERIALIZED":
            continue  # INSERT OVERWRITE TABLE t: the next trigger reads the name
        if following == "IF":
            start += 1
            while start < len(code) and _upper(code[start]) in ("NOT", "EXISTS"):
                start += 1
        while True:
            parts, end = _read_name(code, start)
            if not parts or (word in FUNCTION_TRIGGERS and end < len(code) and code[end].text == "("):
                break
            if not (len(parts) == 1 and parts[0] in ctes):
                ref = _qualify(parts, catalog, database)
                if ref not in found:
                    found.append(ref)
            after = _skip_alias(code, end)
            if word != "FROM" or after >= len(code) or code[after].text != ",":
                break
            start = after + 1  # FROM t1 a, t2 b
    return found


# --- Payloads and keys (BR-12 to BR-15, BR-31) ----------------------------------------


def canonical_json(payload: Any) -> str:
    """The payload as canonical JSON (sorted keys, compact); non-JSON text unchanged."""
    if not isinstance(payload, str):
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    try:
        value = json.loads(payload)
    except ValueError:
        return payload
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_key(query_id: Any, payload: Any) -> str:
    """The event's key (BR-31): the trimmed query ID, else ``sha256:`` + the hash of the canonical payload."""
    if query_id is not None and str(query_id).strip():
        return str(query_id).strip()
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _field_name(name: str) -> str:
    return name.lower().replace("_", "")


def normalized_fields(payload: Mapping) -> dict:
    """The payload's keys normalized for lookup (BR-15): lowercase, ``_`` removed."""
    return {_field_name(str(key)): value for key, value in payload.items()}


def payload_field(payload: Mapping, name: str) -> Any:
    """A payload value by name, ignoring case and ``_`` (BR-15); ``None`` when absent."""
    return normalized_fields(payload).get(_field_name(name))


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_int(value: Any) -> Optional[int]:
    """An integer value, or ``None`` when it isn't one (BR-15)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    return int(text) if re.fullmatch(r"[+-]?\d{1,19}", text) else None


def to_bool(value: Any) -> Optional[bool]:
    """A boolean value, or ``None`` when it isn't one."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower() if value is not None else ""
    return {"true": True, "1": True, "false": False, "0": False}.get(text)


def utc_naive(value: Any) -> Optional[datetime]:
    """A UTC wall-clock ``datetime`` without time zone, from a datetime, epoch number or text; ``None`` otherwise."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return utc_naive(parsed)


def _is_marked(fields: Mapping, marker_table: Mapping) -> bool:
    for name, markers in marker_table.items():
        value = fields.get(name)
        if value is not None and str(value).strip().upper() in markers:
            return True
    return False


def _redacted_payload(data: Mapping) -> str:
    """The payload with its statement redacted, as canonical JSON (BR-22)."""
    copy = {key: (redact_credentials(value) if _field_name(str(key)) == "stmt" and isinstance(value, str) else value) for key, value in data.items()}
    return canonical_json(copy)


def enrich_payload(query_id: Any, event_time: Any, payload: Any, copied_at: Any, copy_task: Any) -> dict:
    """One bronze row as one silver event row (BR-5 to BR-22); never raises for any payload.

    Args:
        query_id: Bronze ``query_id``.
        event_time: Bronze ``event_time``.
        payload: Bronze ``payload`` (JSON text).
        copied_at: Bronze ``copied_at``.
        copy_task: Bronze ``copy_task``.

    Returns:
        A dict with every ``SILVER_EVENT_COLUMNS`` name (``loaded_at`` left ``None``) plus
        ``referenced_tables``, a list of ``TableRef`` for ``table_access_rows``.
    """
    row = {name: None for name, _ in SILVER_EVENT_COLUMNS}
    try:
        data = json.loads(payload) if isinstance(payload, str) else None
    except ValueError:
        data = None
    fields = normalized_fields(data) if isinstance(data, dict) else {}
    statement = fields.get("stmt") if isinstance(fields.get("stmt"), str) else None
    payload_time = utc_naive(fields.get("timestamp"))
    query = _text(query_id) or _text(fields.get("queryid"))
    unparsed = not isinstance(data, dict) or (payload_time is None and statement is None and query is None)
    when = payload_time or utc_naive(event_time) or utc_naive(copied_at) or datetime(1970, 1, 1)
    row.update(
        event_key=event_key(query, payload if isinstance(payload, str) else canonical_json(payload)),
        event_time=when,
        event_date=when.date(),
        payload=_redacted_payload(data) if isinstance(data, dict) else (payload if isinstance(payload, str) else canonical_json(payload)),
        copy_task=_text(copy_task),
        first_copied_at=utc_naive(copied_at),
        actor_type="unknown",
        failed=False,
        referenced_tables=[],
    )
    if unparsed:
        row["event_type"] = "unparsed"
        return row
    event_type = "connection" if _is_marked(fields, CONNECTION_MARKERS) else "disconnection" if _is_marked(fields, DISCONNECTION_MARKERS) else "query"
    sql_text = redact_credentials(statement)
    category = statement_category(sql_text) if event_type == "query" and sql_text is not None else None
    catalog, database = _text(fields.get("catalog")), _text(fields.get("db"))
    tables = referenced_tables(sql_text, catalog, database) if category else []
    scope = event_scope(infer_scope(catalog, database), [infer_scope(table.catalog, table.database) for table in tables])
    state = _text(fields.get("state"))
    row.update(
        event_type=event_type,
        user=normalize_user(fields.get("user")),
        authorized_user=normalize_user(fields.get("authorizeduser")),
        actor_type=actor_type(fields.get("user")),
        client_ip=_text(fields.get("clientip")),
        fe_host=_text(fields.get("feip")),
        catalog=catalog,
        database=database,
        data_product=scope.data_product,
        environment=scope.environment,
        statement_category=category,
        query_type=_text(fields.get("querytype")),
        is_query=to_bool(fields.get("isquery")),
        state=state,
        failed=(state or "").upper() == "ERR",
        error_code=_text(fields.get("errorcode")),
        query_time_ms=to_int(fields.get("querytime")),
        scan_rows=to_int(fields.get("scanrows")),
        scan_bytes=to_int(fields.get("scanbytes")),
        return_rows=to_int(fields.get("returnrows")),
        cpu_cost_ns=to_int(fields.get("cpucostns")),
        mem_cost_bytes=to_int(fields.get("memcostbytes")),
        digest=_text(fields.get("digest")),
        sql_text=sql_text,
        sql_masked=mask_sql(sql_text),
        referenced_tables=tables,
    )
    return row


def _json_value(value: Any) -> Any:
    """A staging value as a JSON-compatible value (datetimes as ``YYYY-MM-DD HH:MM:SS[.ffffff]`` text)."""
    if isinstance(value, datetime):
        return utc_naive(value).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return value


def staging_to_bronze(row: Mapping, copied_at: datetime, loader: str) -> tuple:
    """One StarRocks staging row as one bronze row (``BRONZE_COLUMNS`` order), statement redacted (BR-20, BR-23).

    Args:
        row: Column name to value, as the staging table returned them.
        copied_at: When the bronze task read the row (UTC).
        loader: The bronze row's ``copy_task`` value.

    Returns:
        ``(query_id, event_time, payload, copied_at, copy_task)``; the payload is canonical JSON, so the same
        staging row always gives the same payload.
    """
    data = {}
    for key, value in row.items():
        value = _json_value(value)
        if _field_name(str(key)) == "stmt" and isinstance(value, str):
            value = redact_credentials(value)
        data[str(key)] = value
    fields = normalized_fields(data)
    event_time = utc_naive(fields.get("timestamp")) or copied_at
    return (_text(fields.get("queryid")), event_time, canonical_json(data), copied_at, loader)


def table_access_rows(events: Iterable) -> list:
    """One table-access row per event and referenced table (``TABLE_ACCESS_COLUMNS``)."""
    rows = []
    for event in events:
        for table in event.get("referenced_tables") or []:
            scope = infer_scope(table.catalog, table.database)
            rows.append(
                {
                    "event_key": event["event_key"],
                    "event_time": event["event_time"],
                    "event_date": event["event_date"],
                    "user": event["user"],
                    "actor_type": event["actor_type"],
                    "statement_category": event["statement_category"],
                    "ref_catalog": table.catalog,
                    "ref_database": table.database,
                    "ref_table": table.table,
                    "data_product": scope.data_product,
                    "environment": scope.environment,
                }
            )
    return rows


def deduplicate(rows: Iterable, key_columns: Iterable, order_column: Optional[str] = None) -> list:
    """One row per key, in order of first appearance; with ``order_column``, the row with its smallest value."""
    keys = tuple(key_columns)
    chosen = {}
    for row in rows:
        key = tuple(row.get(name) for name in keys)
        current = chosen.get(key)
        if current is None:
            chosen[key] = row
        elif order_column and row.get(order_column) is not None and (current.get(order_column) is None or row[order_column] < current[order_column]):
            chosen[key] = {**row}
    return list(chosen.values())


def as_tuple(row: Mapping, columns: Iterable) -> tuple:
    """The row's values in the order of ``columns`` (``(name, type)`` pairs)."""
    return tuple(row.get(name) for name, _ in columns)


def ddl_columns(columns: Iterable) -> str:
    """``\\`name\\` TYPE, ...`` for a column list, for Spark DDL and ``createDataFrame`` schemas."""
    return ", ".join(f"`{name}` {kind}" for name, kind in columns)


def chunked(items: Iterable, size: int) -> Iterator:
    """Lists of at most ``size`` items, in order."""
    if size < 1:
        raise ValueError("size must be at least 1")
    chunk = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


__all__ = [
    "ACTOR_TYPES",
    "BRONZE_COLUMNS",
    "CATEGORIES",
    "EVENT_KEY_COLUMNS",
    "EVENT_TYPES",
    "SILVER_EVENT_COLUMNS",
    "SLOW_QUERY_MS",
    "TABLE_ACCESS_COLUMNS",
    "TABLE_ACCESS_KEY_COLUMNS",
    "Scope",
    "TableRef",
    "Token",
    "actor_type",
    "STAGING_TABLE",
    "as_tuple",
    "staging_to_bronze",
    "canonical_json",
    "chunked",
    "ddl_columns",
    "deduplicate",
    "enrich_payload",
    "event_key",
    "event_scope",
    "infer_scope",
    "mask_sql",
    "normalize_user",
    "payload_field",
    "redact_credentials",
    "referenced_tables",
    "statement_category",
    "table_access_rows",
    "to_bool",
    "to_int",
    "tokenize",
    "utc_naive",
]
