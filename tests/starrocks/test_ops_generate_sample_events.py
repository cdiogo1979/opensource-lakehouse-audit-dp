"""The dev sample-events notebook (C5): arguments, determinism, coverage, and that silver can read every row."""

import json
from collections import Counter
from datetime import datetime
from types import SimpleNamespace

import pytest

import ops_generate_sample_events as ops
from notebooks.common import audit_sql
from notebooks.common.config_loader import EnvironmentConfig

START = datetime(2026, 11, 25, 9)
DEV = EnvironmentConfig(environment="dev", settings={"storage_path": "s3a://bucket-audit-dev/", "catalog_name": "lakehouse_dev", "database_name": "audit_lhdev"})


def test_task_arguments_are_declared_with_working_values():
    assert ops.TASK_ARGS_DEFINITION == {"events": {"default": "200"}, "seed": {"default": "1"}}
    assert ops.check_arguments(ops.TASK_ARGS_VALUES, "dev") == (200, 1)


@pytest.mark.parametrize(
    "arguments, environment, message",
    [
        ({"events": "10", "seed": "1"}, "uat", "dev only"),
        ({"events": "10", "seed": "1"}, "prod", "dev only"),
        ({"events": "0", "seed": "1"}, "dev", "events must be"),
        ({"events": "100001", "seed": "1"}, "dev", "events must be"),
        ({"events": "ten", "seed": "1"}, "dev", "events must be"),
        ({"events": "10", "seed": "x"}, "dev", "seed must be"),
    ],
)
def test_bad_arguments_and_other_environments_are_refused(arguments, environment, message):
    with pytest.raises(ValueError, match=message):
        ops.check_arguments(arguments, environment)


def test_generation_is_deterministic_by_seed():
    assert ops.generate_events(50, 7, START) == ops.generate_events(50, 7, START)
    assert ops.generate_events(50, 7, START) != ops.generate_events(50, 8, START)


def test_generated_events_cover_the_cases_silver_handles():
    events = ops.generate_events(1500, 3, START)
    rows = ops.bronze_rows(events, datetime(2026, 11, 25, 10, 5), 3)
    enriched = [audit_sql.enrich_payload(query_id, when, payload, copied, task) for query_id, when, payload, copied, task in rows]
    categories = Counter(row["statement_category"] for row in enriched if row["event_type"] == "query")
    assert set(categories) == {"SELECT", "DML", "DDL", "ADMIN", "METADATA", "SESSION"}
    assert {row["actor_type"] for row in enriched} == {"person", "product_writer", "platform_service"}
    assert {row["event_type"] for row in enriched} == {"query", "connection"}
    assert {row["environment"] for row in enriched if row["event_type"] == "query"} == {"prod", "uat", "dev"}
    assert {row["data_product"] for row in enriched if row["event_type"] == "query"} == set(ops.PRODUCTS)
    assert any(row["failed"] and row["event_type"] == "connection" for row in enriched)
    assert any(row["failed"] and row["event_type"] == "query" for row in enriched)
    assert any((row["query_time_ms"] or 0) >= audit_sql.SLOW_QUERY_MS for row in enriched)
    assert any(row["event_type"] == "query" and row["event_key"].startswith("sha256:") for row in enriched)
    assert all(START <= row["event_time"] < datetime(2026, 11, 25, 10) for row in enriched)
    duplicates = len(rows) - len({row[2] for row in rows})
    assert 0.1 * len(events) < duplicates < 0.3 * len(events)
    for row in enriched:
        assert "FakePass-" not in (row["sql_text"] or "") and "FakePass-" not in row["payload"]
    assert any("FakePass-" in payload for _, _, payload, _, _ in rows)  # bronze sample carries them, as an unredacted copy would


def test_bronze_rows_follow_the_contract():
    rows = ops.bronze_rows(ops.generate_events(5, 1, START), datetime(2026, 11, 25, 10), 1)
    for row in rows:
        assert len(row) == len(audit_sql.BRONZE_COLUMNS)
        query_id, when, payload, copied, task = row
        assert isinstance(when, datetime) and isinstance(copied, datetime) and task == "ops_generate_sample_events"
        assert json.loads(payload).get("queryId") == query_id


class FakeSpark:
    def __init__(self, exists=True):
        self.appended, self.conf_set = [], {}
        self.catalog = SimpleNamespace(tableExists=lambda name: exists)
        self.conf = SimpleNamespace(set=self.conf_set.__setitem__)

    def createDataFrame(self, rows, schema):
        writer = SimpleNamespace(append=lambda: self.appended.append((self.target, rows, schema)))

        def write_to(table):
            self.target = table
            return writer

        return SimpleNamespace(writeTo=write_to)


def test_run_appends_to_the_dev_bronze_table():
    spark = FakeSpark()
    record = ops.run(spark, DEV, 20, 1, datetime(2026, 11, 25, 10, 42))
    ((table, rows, schema),) = spark.appended
    assert table == "lakehouse_dev.audit_lhdev.starrocks_audit_event_fe_brz" and schema == audit_sql.ddl_columns(audit_sql.BRONZE_COLUMNS)
    assert record["rows_written"] == len(rows) >= 20 and record["start"] == "2026-11-25T09:00:00"
    assert spark.conf_set == {"spark.sql.session.timeZone": "UTC"}


def test_missing_bronze_table_is_reported():
    with pytest.raises(RuntimeError, match="deploy_bronze_table.py first"):
        ops.write_sample_events(FakeSpark(exists=False), DEV, [])
