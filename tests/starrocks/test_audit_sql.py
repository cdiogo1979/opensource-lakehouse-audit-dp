"""Example-based tests for notebooks/common/audit_sql.py (business rules BR-1 to BR-34)."""

import json
from datetime import date, datetime, timezone

import pytest

from notebooks.common import audit_sql as a
from notebooks.common.audit_sql import Scope, TableRef

# --- Tokenizer --------------------------------------------------------------------------


def test_tokenizer_kinds_and_lossless_join():
    sql = "SELECT `a b`, 'x''y', \"q\", 0x1F, 1.5e3 /*+ SET_VAR(a=1) */ -- note\n/* c */ FROM t # end"
    tokens = a.tokenize(sql)
    assert "".join(token.text for token in tokens) == sql
    kinds = {token.text: token.kind for token in tokens}
    assert kinds["`a b`"] == "quoted_ident"
    assert kinds["'x''y'"] == "string" and kinds['"q"'] == "string"
    assert kinds["0x1F"] == "number" and kinds["1.5e3"] == "number"
    assert kinds["/*+ SET_VAR(a=1) */"] == "hint" and kinds["/* c */"] == "block_comment"
    assert kinds["-- note"] == "line_comment" and kinds["# end"] == "line_comment"


def test_unterminated_string_runs_to_the_end():
    assert a.tokenize("SELECT 'abc")[-1] == a.Token("string", "'abc")
    assert a.mask_sql("SELECT 'abc") == "SELECT ?"


def test_operators_stay_whole():
    assert [t.text for t in a.tokenize("a>=1") if t.kind == "punct"] == [">="]
    assert a.mask_sql("a <> 2 AND b != 3") == "a <> ? AND b != ?"


# --- Masking (BR-1 to BR-4) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, masked",
    [
        ("SELECT * FROM t WHERE name = 'Ana' AND id = 42", "SELECT * FROM t WHERE name = ? AND id = ?"),
        ("select a from t1 where d = DATE '2026-01-01'", "select a from t1 where d = DATE ?"),
        ("SELECT * FROM t WHERE id IN (1, 2, 3) AND s IN ('a','b')", "SELECT * FROM t WHERE id IN (?) AND s IN (?)"),
        ("SELECT * FROM t WHERE id IN (SELECT id FROM u WHERE x = 1)", "SELECT * FROM t WHERE id IN (SELECT id FROM u WHERE x = ?)"),
        ("SELECT /*+ SET_VAR(query_timeout=10) */ 1 -- who\n/* secret note */ FROM `q4_2026`", "SELECT /*+ ? */ ? FROM `q4_2026`"),
        ("SELECT x FROM t WHERE h = 0xFF AND v = -5", "SELECT x FROM t WHERE h = ? AND v = - ?"),
        ("  SELECT\n\n  1  ", "SELECT ?"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_mask_sql(sql, masked):
    assert a.mask_sql(sql) == masked


def test_mask_sql_keeps_none():
    assert a.mask_sql(None) is None


# --- Categories (BR-5) --------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, category",
    [
        ("select 1", "SELECT"),
        ("  /* c */ (SELECT 1)", "SELECT"),
        ("WITH x AS (SELECT 1), y (a) AS (SELECT 2) SELECT * FROM x", "SELECT"),
        ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "DML"),
        ("VALUES (1)", "SELECT"),
        ("INSERT INTO t VALUES (1)", "DML"),
        ("insert overwrite t select 1", "DML"),
        ("DELETE FROM t", "DML"),
        ("TRUNCATE TABLE t", "DML"),
        ("CREATE TABLE t (a INT)", "DDL"),
        ("REFRESH MATERIALIZED VIEW db.mv", "DDL"),
        ("SUBMIT TASK AS INSERT INTO t SELECT 1", "DDL"),
        ("CREATE USER 'joe' IDENTIFIED BY 'x'", "ADMIN"),
        ("DROP ROLE r", "ADMIN"),
        ("GRANT SELECT ON ALL TABLES IN DATABASE d TO ROLE r", "ADMIN"),
        ("SET PASSWORD FOR 'joe' = PASSWORD('x')", "ADMIN"),
        ("SET ROLE ALL", "ADMIN"),
        ("ALTER SYSTEM ADD BACKEND 'h:9050'", "ADMIN"),
        ("KILL 12", "ADMIN"),
        ("SHOW TABLES FROM d", "METADATA"),
        ("desc t", "METADATA"),
        ("EXPLAIN SELECT 1", "METADATA"),
        ("SET query_timeout = 300", "SESSION"),
        ("USE lakehouse.default_lh", "SESSION"),
        ("BEGIN", "OTHER"),
        ("", "OTHER"),
        ("-- only a comment", "OTHER"),
    ],
)
def test_statement_category(sql, category):
    assert a.statement_category(sql) == category


def test_statement_category_none():
    assert a.statement_category(None) is None


# --- Actors (BR-6) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "user, kind",
    [
        ("maria-owner", "person"),
        ("'Joe-Developer'@'%'", "person"),
        ("srv-default-writer", "product_writer"),
        ("'srv-sales-writer'@'172.18.0.4'", "product_writer"),
        ("srv-starrocks", "platform_service"),
        ("root", "platform_service"),
        ("ADMIN", "platform_service"),
        ("%", "platform_service"),
        ("", "unknown"),
        (None, "unknown"),
        ("''@'%'", "unknown"),
    ],
)
def test_actor_type(user, kind):
    assert a.actor_type(user) == kind


def test_normalize_user():
    assert a.normalize_user("'Maria-Owner'@'%'") == "maria-owner"
    assert a.normalize_user(" root ") == "root"
    assert a.normalize_user(None) is None


# --- Scope (BR-7, BR-8) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "catalog, database, scope",
    [
        ("lakehouse", "sales_lh", Scope("sales", "prod")),
        ("lakehouse_uat", "sales_lhuat", Scope("sales", "uat")),
        ("lakehouse_dev", "sales_lhdev", Scope("sales", "dev")),
        ("default_catalog", "default_lhuat", Scope("default", "uat")),
        (None, "`Sales_EU_lh`", Scope("sales_eu", "prod")),
        ("lakehouse_dev", "sales_lh", Scope("sales", "prod")),
        ("lakehouse_uat", "information_schema", Scope(None, "uat")),
        ("default_catalog", "_lh", Scope(None, None)),
        (None, None, Scope(None, None)),
    ],
)
def test_infer_scope(catalog, database, scope):
    assert a.infer_scope(catalog, database) == scope


def test_event_scope_order():
    sales, default_uat = Scope("sales", "prod"), Scope("default", "uat")
    assert a.event_scope(Scope("audit", "dev"), [sales]) == Scope("audit", "dev")
    assert a.event_scope(Scope(None, "uat"), [sales, sales]) == Scope("sales", "prod")
    assert a.event_scope(Scope(None, None), [sales, Scope("sales", "uat")]) == Scope("sales", "multiple")
    assert a.event_scope(Scope(None, None), [sales, default_uat]) == Scope("multiple", "multiple")
    assert a.event_scope(Scope(None, None), [sales, Scope("default", "prod")]) == Scope("multiple", "prod")
    assert a.event_scope(Scope(None, "dev"), [Scope(None, "prod")]) == Scope(None, "dev")
    assert a.event_scope(Scope(None, None), []) == Scope(None, None)


# --- Referenced tables (BR-9) -------------------------------------------------------------


def test_referenced_tables_qualifies_and_skips_ctes_functions_and_subqueries():
    sql = "WITH x AS (SELECT id FROM orders) SELECT EXTRACT(YEAR FROM d) FROM x, lakehouse_uat.default_lhuat.`Items` i JOIN sales_lh.customers c ON 1 = 1"
    assert a.referenced_tables(sql, "lakehouse", "default_lh") == [
        TableRef("lakehouse", "default_lh", "orders"),
        TableRef("lakehouse_uat", "default_lhuat", "items"),
        TableRef("lakehouse", "sales_lh", "customers"),
    ]


def test_referenced_tables_for_dml_and_ddl():
    assert a.referenced_tables("INSERT INTO t (a, b) SELECT a, b FROM s", None, None) == [TableRef("default_catalog", None, "t"), TableRef("default_catalog", None, "s")]
    assert a.referenced_tables("INSERT OVERWRITE TABLE d.t SELECT 1", "lakehouse", None) == [TableRef("lakehouse", "d", "t")]
    assert a.referenced_tables("CREATE TABLE IF NOT EXISTS d.t (a INT)", "lakehouse", None) == [TableRef("lakehouse", "d", "t")]
    assert a.referenced_tables("DROP TABLE IF EXISTS d.t", "lakehouse", None) == [TableRef("lakehouse", "d", "t")]
    assert a.referenced_tables("REFRESH MATERIALIZED VIEW default_lh.mv_x", None, None) == [TableRef("default_catalog", "default_lh", "mv_x")]
    assert a.referenced_tables("UPDATE t SET a = 1", "c", "d") == [TableRef("c", "d", "t")]
    assert a.referenced_tables("SELECT * FROM TABLE(generate_series(1, 3))", "c", "d") == []
    assert a.referenced_tables("SELECT * FROM (SELECT * FROM inner_t) s", "c", "d") == [TableRef("c", "d", "inner_t")]


def test_referenced_tables_only_for_select_dml_ddl():
    assert a.referenced_tables("SHOW TABLES FROM d", "c", "d") == []
    assert a.referenced_tables("GRANT SELECT ON TABLE d.t TO ROLE r", "c", "d") == []
    assert a.referenced_tables(None) == []


# --- Credentials (BR-20) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, redacted",
    [
        ("CREATE USER 'joe'@'%' IDENTIFIED BY 'S3cret!'", "CREATE USER 'joe'@'%' IDENTIFIED BY '***'"),
        ("ALTER USER joe IDENTIFIED WITH mysql_native_password BY 'pw'", "ALTER USER joe IDENTIFIED WITH mysql_native_password BY '***'"),
        ("CREATE USER joe IDENTIFIED BY PASSWORD '*ABC'", "CREATE USER joe IDENTIFIED BY PASSWORD '***'"),
        ("SET PASSWORD FOR 'joe' = PASSWORD('pw')", "SET PASSWORD FOR 'joe' = PASSWORD('***')"),
        ("SET PASSWORD = 'pw'", "SET PASSWORD = '***'"),
        (
            'CREATE EXTERNAL CATALOG c PROPERTIES ("aws.s3.access_key" = "AK", "aws.s3.secret_key"="SK", "aws.s3.region" = "eu")',
            'CREATE EXTERNAL CATALOG c PROPERTIES ("aws.s3.access_key" = \'***\', "aws.s3.secret_key"=\'***\', "aws.s3.region" = "eu")',
        ),
        ("SELECT * FROM t WHERE name = 'Ana' -- keep", "SELECT * FROM t WHERE name = 'Ana' -- keep"),
    ],
)
def test_redact_credentials(sql, redacted):
    assert a.redact_credentials(sql) == redacted


def test_redact_credentials_none():
    assert a.redact_credentials(None) is None


# --- Keys and payloads (BR-12 to BR-15, BR-31) --------------------------------------------


def test_event_key_prefers_query_id_and_hashes_canonical_payload():
    assert a.event_key(" q1 ", "{}") == "q1"
    first = a.event_key(None, '{"b": 1, "a": 2}')
    assert first.startswith("sha256:") and first == a.event_key("", '{"a":2,"b":1}')
    assert a.event_key(None, "not json") == a.event_key(None, "not json")


def test_payload_field_ignores_case_and_underscores():
    payload = {"queryId": "q", "SCAN_BYTES": 3}
    assert a.payload_field(payload, "query_id") == "q"
    assert a.payload_field(payload, "scanBytes") == 3
    assert a.payload_field(payload, "missing") is None


@pytest.mark.parametrize("value, number", [(5, 5), ("12", 12), ("-3", -3), (2.0, 2), (2.5, None), ("x", None), (True, None), (None, None), ("1" * 25, None)])
def test_to_int(value, number):
    assert a.to_int(value) == number


def test_utc_naive():
    assert a.utc_naive("2026-11-25 10:00:00.123") == datetime(2026, 11, 25, 10, 0, 0, 123000)
    assert a.utc_naive("2026-11-25T10:00:00Z") == datetime(2026, 11, 25, 10)
    assert a.utc_naive(datetime(2026, 11, 25, 11, tzinfo=timezone.utc)) == datetime(2026, 11, 25, 11)
    assert a.utc_naive(1764064800000) == datetime(2025, 11, 25, 10)
    assert a.utc_naive("yesterday") is None and a.utc_naive(None) is None


def _payload(**fields):
    base = {
        "queryId": "q-1",
        "timestamp": "2026-11-25 10:00:00",
        "user": "'maria-owner'@'%'",
        "authorizedUser": "maria-owner",
        "clientIp": "10.0.0.5",
        "feIp": "172.18.0.5",
        "catalog": "lakehouse",
        "db": "sales_lh",
        "state": "EOF",
        "errorCode": "",
        "queryTime": 1200,
        "scanRows": "10",
        "scanBytes": 2048,
        "returnRows": 3,
        "cpuCostNs": 5,
        "memCostBytes": 6,
        "isQuery": "true",
        "queryType": "query",
        "digest": "d1",
        "stmt": "SELECT * FROM orders o JOIN default_lh.items i ON o.id = i.id WHERE o.note = 'private'",
    }
    base.update(fields)
    return json.dumps(base)


def test_enrich_payload_query():
    copied = datetime(2026, 11, 25, 11, 5)
    row = a.enrich_payload("q-1", datetime(2026, 11, 25, 10), _payload(), copied, "audit_copy_prod")
    assert row["event_key"] == "q-1" and row["event_type"] == "query"
    assert row["event_time"] == datetime(2026, 11, 25, 10) and row["event_date"] == date(2026, 11, 25)
    assert row["user"] == "maria-owner" and row["actor_type"] == "person"
    assert row["statement_category"] == "SELECT" and row["failed"] is False and row["is_query"] is True
    assert row["scan_rows"] == 10 and row["scan_bytes"] == 2048 and row["error_code"] is None
    assert row["data_product"] == "sales" and row["environment"] == "prod"
    assert row["sql_masked"] == "SELECT * FROM orders o JOIN default_lh.items i ON o.id = i.id WHERE o.note = ?"
    assert [t.table for t in row["referenced_tables"]] == ["orders", "items"]
    assert row["copy_task"] == "audit_copy_prod" and row["first_copied_at"] == copied and row["loaded_at"] is None
    assert set(name for name, _ in a.SILVER_EVENT_COLUMNS) <= set(row)


def test_enrich_payload_redacts_statement_and_payload():
    row = a.enrich_payload(None, datetime(2026, 11, 25), _payload(stmt="CREATE USER 'x' IDENTIFIED BY 'Hunter2Pass'"), datetime(2026, 11, 25), "t")
    assert row["statement_category"] == "ADMIN"
    assert "Hunter2Pass" not in row["sql_text"] and "Hunter2Pass" not in row["payload"] and "Hunter2Pass" not in row["sql_masked"]
    assert json.loads(row["payload"])["stmt"] == "CREATE USER 'x' IDENTIFIED BY '***'"


def test_enrich_payload_failed_and_slow_values():
    row = a.enrich_payload("q", None, _payload(state="err", queryTime="61000", errorCode=1064), None, "t")
    assert row["failed"] is True and row["query_time_ms"] == 61000 and row["error_code"] == "1064"


def test_enrich_payload_connection_without_query_id():
    payload = json.dumps({"eventType": "CONNECTION", "timestamp": "2026-11-25 09:00:00", "user": "joe", "clientIp": "10.0.0.9", "state": "ERR"})
    row = a.enrich_payload(None, datetime(2026, 11, 25, 9), payload, datetime(2026, 11, 25, 10), "t")
    assert row["event_type"] == "connection" and row["event_key"].startswith("sha256:")
    assert row["statement_category"] is None and row["failed"] is True and row["referenced_tables"] == []


@pytest.mark.parametrize("payload", ["not json", "[1, 2]", "{}", '{"user": "joe"}', None])
def test_enrich_payload_unparsed(payload):
    when = datetime(2026, 11, 25, 8)
    row = a.enrich_payload(None, when, payload, when, "t")
    assert row["event_type"] == "unparsed" and row["actor_type"] == "unknown" and row["failed"] is False
    assert row["event_time"] == when and row["event_date"] == when.date() and row["event_key"].startswith("sha256:")
    assert row["user"] is None and row["sql_text"] is None


def test_enrich_payload_uses_bronze_time_when_payload_has_none():
    row = a.enrich_payload("q", datetime(2026, 1, 2, 3), _payload(timestamp=None), None, "t")
    assert row["event_type"] == "query" and row["event_time"] == datetime(2026, 1, 2, 3)


# --- Table access and dedup (BR-31, BR-32) ------------------------------------------------


def test_table_access_rows_and_dedup():
    first = a.enrich_payload("q-1", None, _payload(), datetime(2026, 11, 25, 11), "t")
    second = a.enrich_payload("q-1", None, _payload(), datetime(2026, 11, 25, 10), "t")
    events = a.deduplicate([first, second], a.EVENT_KEY_COLUMNS, "first_copied_at")
    assert len(events) == 1 and events[0]["first_copied_at"] == datetime(2026, 11, 25, 10)
    access = a.table_access_rows(events)
    assert [(r["ref_database"], r["ref_table"], r["data_product"]) for r in access] == [("sales_lh", "orders", "sales"), ("default_lh", "items", "default")]
    assert a.deduplicate(access + access, a.TABLE_ACCESS_KEY_COLUMNS) == access


def test_helpers():
    assert list(a.chunked(range(5), 2)) == [[0, 1], [2, 3], [4]]
    with pytest.raises(ValueError):
        list(a.chunked([], 0))
    assert a.ddl_columns(a.BRONZE_COLUMNS).startswith("`query_id` STRING, `event_time` TIMESTAMP")
    assert a.as_tuple({"query_id": "q"}, a.BRONZE_COLUMNS) == ("q", None, None, None, None)


def test_enrich_payload_logout_is_a_disconnection_not_a_login():
    """AuditLoader 5.0.0 writes queryType connection (login) and disconnection (logout); logouts never count as logins."""
    login = json.dumps({"queryType": "connection", "timestamp": "2026-11-26 07:46:02", "user": "'root'@'%'", "state": "OK"})
    logout = json.dumps({"queryType": "disconnection", "timestamp": "2026-11-26 07:46:09", "user": "'root'@'%'", "state": "OK"})
    assert a.enrich_payload(None, None, login, None, "t")["event_type"] == "connection"
    row = a.enrich_payload(None, None, logout, None, "t")
    assert row["event_type"] == "disconnection" and row["statement_category"] is None and row["referenced_tables"] == []
    assert a.enrich_payload("q", None, json.dumps({"queryType": "slow_query", "timestamp": "2026-11-26 07:46:09", "stmt": "SELECT 1"}), None, "t")["event_type"] == "query"
