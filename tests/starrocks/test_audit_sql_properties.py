"""Hypothesis properties of notebooks/common/audit_sql.py (functional design, Testable Properties; PBT-02, -03, -07, -08)."""

import json
import os
import string

from hypothesis import given, seed, settings, strategies as st

from notebooks.common import audit_sql as a

HYPOTHESIS_SEED = int(os.environ.get("HYPOTHESIS_SEED", "0"))
PROPERTY = settings(max_examples=150, print_blob=True, deadline=None)

KEYWORDS = {
    "select",
    "from",
    "where",
    "and",
    "or",
    "not",
    "in",
    "insert",
    "into",
    "values",
    "update",
    "set",
    "delete",
    "create",
    "table",
    "user",
    "identified",
    "by",
    "as",
    "with",
    "join",
    "on",
    "show",
    "use",
    "desc",
    "describe",
    "explain",
    "is",
    "null",
    "like",
    "between",
    "case",
    "when",
    "then",
    "else",
    "end",
    "group",
    "order",
    "limit",
    "having",
    "union",
    "all",
    "exists",
    "date",
    "overwrite",
    "if",
    "drop",
    "alter",
    "grant",
    "to",
    "role",
    "password",
    "x",
    "left",
    "right",
    "inner",
    "outer",
    "cross",
    "full",
    "using",
    "partition",
    "for",
    "lateral",
    "semi",
    "anti",
    "window",
    "except",
    "intersect",
    "tablet",
    "materialized",
    "view",
    "refresh",
    "recursive",
    "distinct",
    "asc",
    "true",
    "false",
    "interval",
    "cast",
    "admin",
    "kill",
    "truncate",
    "merge",
    "load",
}

# --- Generators (PBT-07) -------------------------------------------------------------------

# Identifiers: lowercase letters and _, never a keyword and never secret-looking (so redaction leaves them alone).
identifiers = st.from_regex(r"[a-z][a-z_]{0,9}", fullmatch=True).filter(lambda s: s not in KEYWORDS and not a.SECRET_KEY.search(s))
# Literal contents carry a mixed-case marker that can't occur in lowercase identifiers or uppercase keywords.
string_values = st.text(alphabet=string.ascii_letters + string.digits + " -_:/.@", min_size=4, max_size=12).map(lambda s: "Qz" + s)
secrets = st.text(alphabet=string.ascii_letters + string.digits + "!#%&*+-", min_size=6, max_size=14).map(lambda s: "Sk" + s + "9")
big_numbers = st.integers(min_value=1_000_000, max_value=10**12)
products = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
environments = st.sampled_from(["prod", "uat", "dev"])
CATALOG = {"prod": "lakehouse", "uat": "lakehouse_uat", "dev": "lakehouse_dev"}
SUFFIX = {"prod": "_lh", "uat": "_lhuat", "dev": "_lhdev"}
comment_text = st.text(alphabet=string.ascii_letters + string.digits + " ,.'\"", max_size=20)


@st.composite
def qualified_tables(draw):
    """``catalog.namespace.table`` in product style, with its parts."""
    product, environment, table = draw(products), draw(environments), draw(identifiers)
    return CATALOG[environment], product + SUFFIX[environment], table


@st.composite
def select_statements(draw):
    """A SELECT with string, number and IN-list literals; returns (sql, literals, identifiers, tables)."""
    tables = draw(st.lists(qualified_tables(), min_size=1, max_size=3, unique=True))
    columns = draw(st.lists(identifiers, min_size=3, max_size=3))
    text_value, number, listed = draw(string_values), draw(big_numbers), draw(st.lists(big_numbers, min_size=1, max_size=4))
    keyword = draw(st.sampled_from(["SELECT", "select", "Select"]))
    sources = ", ".join(".".join(parts) for parts in tables)
    sql = f"{keyword} {columns[0]}, {columns[1]} FROM {sources} WHERE {columns[2]} = '{text_value}' AND {columns[0]} > {number} AND {columns[1]} IN ({', '.join(map(str, listed))})"
    if draw(st.booleans()):
        sql += f" /* {draw(comment_text).replace('*/', '')} */"
    literals = [text_value, str(number), *map(str, listed)]
    return sql, literals, columns, tables


@st.composite
def credential_statements(draw):
    """A statement carrying a secret in one of BR-20's places; returns (sql, secret)."""
    user, secret = draw(identifiers), draw(secrets)
    template = draw(
        st.sampled_from(
            [
                "CREATE USER '{u}'@'%' IDENTIFIED BY '{s}'",
                "ALTER USER {u} IDENTIFIED WITH mysql_native_password BY '{s}'",
                "SET PASSWORD FOR '{u}' = PASSWORD('{s}')",
                'CREATE EXTERNAL CATALOG {u} PROPERTIES ("aws.s3.secret_key" = "{s}", "aws.s3.region" = "eu-west-1")',
                "CREATE STORAGE VOLUME {u} PROPERTIES ('aws.s3.access_key' = '{s}')",
            ]
        )
    )
    return template.format(u=user, s=secret), secret


audit_payloads = st.fixed_dictionaries(
    {"timestamp": st.sampled_from(["2026-11-25 10:00:00", "2026-11-25T10:00:00Z"]), "user": identifiers, "stmt": st.text(max_size=40)},
    optional={"state": st.sampled_from(["EOF", "OK", "ERR", None]), "queryTime": st.one_of(st.integers(), st.text(max_size=5)), "db": st.text(max_size=8)},
)
json_values = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=10)), lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=6), inner, max_size=3), max_leaves=8
)
any_payloads = st.one_of(st.text(max_size=60), json_values.map(json.dumps), audit_payloads.map(json.dumps), st.none())

# --- Round trips (PBT-02) ------------------------------------------------------------------


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.text())
def test_tokenizer_round_trip(sql):
    assert "".join(token.text for token in a.tokenize(sql)) == sql


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(products, environments, st.booleans())
def test_scope_round_trip(product, environment, mv_database):
    catalog = "default_catalog" if mv_database else CATALOG[environment]
    assert a.infer_scope(catalog, product + SUFFIX[environment]) == a.Scope(product, environment)


# --- Invariants (PBT-03) -------------------------------------------------------------------


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(select_statements())
def test_masking_removes_every_literal_and_keeps_identifiers(statement):
    sql, literals, columns, tables = statement
    masked = a.mask_sql(sql)
    for literal in literals:
        assert literal not in masked
    for name in [*columns, *(part for parts in tables for part in parts)]:
        assert name in masked


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(select_statements(), st.data())
def test_statements_differing_only_in_literals_mask_alike(statement, data):
    sql, literals, _, _ = statement
    other = sql
    for literal in literals:
        replacement = data.draw(string_values) if literal.startswith("Qz") else str(data.draw(big_numbers))
        other = other.replace(literal, replacement, 1)
    assert a.mask_sql(other) == a.mask_sql(sql)


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(credential_statements())
def test_redaction_removes_secrets_everywhere(statement):
    sql, secret = statement
    redacted = a.redact_credentials(sql)
    assert secret not in redacted and "'***'" in redacted
    assert secret not in a.mask_sql(redacted)
    assert "eu-west-1" in redacted or "eu-west-1" not in sql


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(select_statements())
def test_redaction_leaves_other_text_unchanged(statement):
    sql = statement[0]
    assert a.redact_credentials(sql) == sql


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.one_of(select_statements().map(lambda s: s[0]), credential_statements().map(lambda s: s[0])), comment_text, st.sampled_from(["", " ", "\n\t "]))
def test_category_ignores_leading_comments_whitespace_case_masking_and_redaction(sql, comment, space):
    expected = a.statement_category(sql)
    assert expected in a.CATEGORIES
    variants = [
        f"{space}/* {comment.replace('*/', '')} */{space}{sql}",
        f"-- {comment.splitlines()[0] if comment.splitlines() else ''}\n{sql}",
        sql.lower(),
        a.mask_sql(sql),
        a.redact_credentials(sql),
    ]
    for variant in variants:
        assert a.statement_category(variant) == expected


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(identifiers, st.sampled_from(["{n}", "'{n}'", "'{n}'@'%'", "{n}@localhost", " `{n}` "]), st.booleans())
def test_actor_type_ignores_quoting_host_and_case(name, form, upper):
    value = form.format(n=name.upper() if upper else name)
    assert a.actor_type(value) == a.actor_type(name)
    writer = f"srv-{name}-writer"
    assert a.actor_type(form.format(n=writer.upper() if upper else writer)) == "product_writer"


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(select_statements())
def test_referenced_tables_found_and_stable_under_masking_and_redaction(statement):
    sql, _, _, tables = statement
    found = a.referenced_tables(sql, "default_catalog", None)
    assert [(ref.catalog, ref.database, ref.table) for ref in found] == list(dict.fromkeys(tables))
    assert a.referenced_tables(a.mask_sql(sql), "default_catalog", None) == found
    assert a.referenced_tables(a.redact_credentials(sql), "default_catalog", None) == found


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.dictionaries(st.from_regex(r"[a-z]{1,8}", fullmatch=True).filter(lambda k: k != "queryid"), json_values, max_size=6), st.randoms(use_true_random=False), st.sampled_from([None, 0, 2]))
def test_event_key_ignores_key_order_and_spacing(payload, random, indent):
    keys = list(payload)
    random.shuffle(keys)
    reordered = json.dumps({key: payload[key] for key in keys}, indent=indent)
    assert a.event_key(None, reordered) == a.event_key(None, json.dumps(payload))


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.one_of(st.none(), st.text(max_size=8)), any_payloads)
def test_enrich_payload_never_raises_and_is_always_keyed(query_id, payload):
    row = a.enrich_payload(query_id, None, payload, None, "t")
    assert row["event_type"] in a.EVENT_TYPES
    assert row["actor_type"] in a.ACTOR_TYPES
    assert row["event_key"] and row["event_date"] == row["event_time"].date()
    assert row["statement_category"] in (None, *a.CATEGORIES)


# --- Idempotence (advisory PBT-04) ---------------------------------------------------------


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.one_of(st.text(), select_statements().map(lambda s: s[0])))
def test_masking_is_idempotent(sql):
    once = a.mask_sql(sql)
    assert a.mask_sql(once) == once


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.one_of(st.text(), credential_statements().map(lambda s: s[0])))
def test_redaction_is_idempotent(sql):
    once = a.redact_credentials(sql)
    assert a.redact_credentials(once) == once


@seed(HYPOTHESIS_SEED)
@PROPERTY
@given(st.lists(st.tuples(st.sampled_from(["k1", "k2", "k3"]), st.integers(0, 5)), max_size=12))
def test_deduplicate_is_idempotent_and_keeps_the_earliest(pairs):
    rows = [{"event_key": key, "event_date": 1, "first_copied_at": order} for key, order in pairs]
    once = a.deduplicate(rows, a.EVENT_KEY_COLUMNS, "first_copied_at")
    assert a.deduplicate(once, a.EVENT_KEY_COLUMNS, "first_copied_at") == once
    assert a.deduplicate(rows + rows, a.EVENT_KEY_COLUMNS, "first_copied_at") == once
    for row in once:
        assert row["first_copied_at"] == min(order for key, order in pairs if key == row["event_key"])
