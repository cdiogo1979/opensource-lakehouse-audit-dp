"""The starrocks gold views (C4): the platform's file rules and each view's intent (BR-40 to BR-43)."""

import re
from pathlib import Path

import pytest

from notebooks.common.audit_sql import SLOW_QUERY_MS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VIEWS = REPOSITORY_ROOT / "materialized_views" / "starrocks"
NAMES = ["mv_starrocks_login_day", "mv_starrocks_product_day", "mv_starrocks_slow_failed", "mv_starrocks_table_day", "mv_starrocks_user_day"]
SILVER = {"starrocks_audit_event", "starrocks_audit_table_access"}


def text(name):
    return (VIEWS / f"{name}.sql").read_text(encoding="utf-8")


def select_list(sql):
    """The column expressions between SELECT and FROM, lowercased and without backticks."""
    body = sql.split("\nSELECT\n", 1)[1].split("\nFROM ", 1)[0]
    return [item.strip().replace("`", "").lower() for item in body.split(",\n")]


def test_exactly_the_planned_views_exist():
    assert sorted(path.stem for path in VIEWS.glob("*.sql")) == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_platform_file_rules(name):
    sql = text(name)
    assert re.match(r"CREATE MATERIALIZED VIEW \$\{mv_database\}\." + name + r"\nCOMMENT '[^';]+'\n", sql)
    assert ";" not in sql
    assert set(re.findall(r"\$\{[^}]*\}", sql)) == {"${mv_database}", "${catalog}", "${namespace}"}
    assert re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name) and len(name) <= 60


@pytest.mark.parametrize("name", NAMES)
def test_hourly_refresh_after_the_silver_load_partitioned_by_day(name):
    sql = text(name)
    assert "\nPARTITION BY event_date\n" in sql
    assert "\nREFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)\n" in sql


@pytest.mark.parametrize("name", NAMES)
def test_views_read_silver_only_and_never_expose_full_sql(name):
    sql = text(name)
    sources = set(re.findall(r"\$\{catalog\}\.\$\{namespace\}\.([a-z_]+)", sql))
    assert sources and sources <= SILVER
    assert "_brz" not in sql
    columns = select_list(sql)
    assert not any(re.search(r"\b(sql_text|payload)\b", column) for column in columns)


def test_slow_failed_keeps_30_days_of_masked_detail():
    sql = text("mv_starrocks_slow_failed")
    assert '\nPROPERTIES ("partition_ttl" = "30 DAY")\n' in sql
    assert f"(failed OR query_time_ms >= {SLOW_QUERY_MS})" in sql and "event_type = 'query'" in sql
    assert {"sql_masked", "digest", "event_key as query_id", "query_time_ms"} <= set(select_list(sql))


@pytest.mark.parametrize(
    "name, grain, source, condition",
    [
        ("mv_starrocks_user_day", ["event_date", "user", "actor_type", "environment"], "starrocks_audit_event", "statement_category NOT IN ('METADATA', 'SESSION')"),
        ("mv_starrocks_product_day", ["event_date", "data_product", "environment", "statement_category"], "starrocks_audit_event", "data_product IS NOT NULL"),
        ("mv_starrocks_table_day", ["event_date", "ref_catalog", "ref_database", "ref_table", "data_product", "environment"], "starrocks_audit_table_access", None),
        ("mv_starrocks_login_day", ["event_date", "user", "actor_type", "client_ip"], "starrocks_audit_event", "event_type = 'connection'"),
    ],
)
def test_aggregate_grain_and_filters(name, grain, source, condition):
    sql = text(name)
    assert select_list(sql)[: len(grain)] == grain
    assert sql.rstrip().endswith("GROUP BY " + ", ".join(f"`{column}`" if column == "user" else column for column in grain))
    assert f"${{catalog}}.${{namespace}}.{source}\n" in sql
    if condition:
        assert condition in sql
