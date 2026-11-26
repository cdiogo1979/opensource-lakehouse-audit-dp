"""The bronze ingestion notebook: watermark, staging query, chunked pull, redaction, with fakes for Spark and StarRocks."""

import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import bronze_ingestion as bronze
from notebooks.common import audit_sql
from notebooks.common.config_loader import EnvironmentConfig

SETTINGS = {"storage_path": "s3a://bucket-audit-dev/", "catalog_name": "lakehouse_dev", "database_name": "audit_lhdev"}
CONFIG = EnvironmentConfig(environment="dev", settings=SETTINGS)
NOW = datetime(2026, 11, 25, 11, 0, 30)
COLUMNS = ("queryId", "timestamp", "user", "db", "state", "scanBytes", "stmt")


def staging_rows(count, start_minute=0):
    return [(f"q{index}", datetime(2026, 11, 25, 10, start_minute + index), "'joe'@'%'", "sales_lh", "EOF", Decimal(10 * index), f"SELECT * FROM orders WHERE id = {index}") for index in range(count)]


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = [(name,) for name in COLUMNS]
        self.closed = False

    def execute(self, sql, parameters):
        self.connection.executed.append((sql, parameters))
        self.pending = list(self.connection.rows)

    def fetchmany(self, size):
        batch, self.pending = self.pending[:size], self.pending[size:]
        return batch

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows):
        self.rows, self.executed, self.closed = rows, [], False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class FakeSpark:
    def __init__(self, watermark=None, exists=True):
        self.watermark, self.appended, self.conf_set = watermark, [], {}
        self.catalog = SimpleNamespace(tableExists=lambda name: exists)
        self.conf = SimpleNamespace(set=self.conf_set.__setitem__)
        self.queries = []

    def sql(self, statement):
        self.queries.append(statement)
        return SimpleNamespace(collect=lambda: [{"watermark": self.watermark}])

    def createDataFrame(self, rows, schema):
        assert schema == audit_sql.ddl_columns(audit_sql.BRONZE_COLUMNS)
        return SimpleNamespace(writeTo=lambda table: SimpleNamespace(append=lambda: self.appended.append((table, rows))))


@pytest.mark.parametrize("value, minutes", [("0", 0), ("30", 30), ("10080", 10080)])
def test_lookback_argument(value, minutes):
    assert bronze.check_arguments({"lookback_minutes": value}) == minutes


@pytest.mark.parametrize("value", ["-1", "10081", "030", "1.5", "", None, 30])
def test_bad_lookback_is_refused(value):
    with pytest.raises(ValueError, match="lookback_minutes"):
        bronze.check_arguments({"lookback_minutes": value})


def test_task_arguments_declared_with_a_working_value():
    assert bronze.check_arguments(bronze.TASK_ARGS_VALUES) == 30
    assert bronze.TASK_ARGS_DEFINITION == {"lookback_minutes": {"default": "30"}}


def test_staging_table_comes_from_config_and_is_checked():
    assert bronze.staging_table(CONFIG) == "starrocks_audit_db__.starrocks_audit_tbl__"
    assert bronze.staging_table(EnvironmentConfig(environment="dev", settings={**SETTINGS, "starrocks_audit_table": "audit_db.audit_tbl"})) == "audit_db.audit_tbl"
    for bad in ["x", "a.b; DROP TABLE t", "a.b.c", "a .b"]:
        with pytest.raises(ValueError, match="starrocks_audit_table"):
            bronze.staging_table(EnvironmentConfig(environment="dev", settings={**SETTINGS, "starrocks_audit_table": bad}))


def test_pull_query_is_parameterized():
    assert bronze.pull_query("d.t", None) == ("SELECT * FROM d.t ORDER BY `timestamp`", ())
    assert bronze.pull_query("d.t", datetime(2026, 11, 25, 9, 30, 15)) == ("SELECT * FROM d.t WHERE `timestamp` >= %s ORDER BY `timestamp`", ("2026-11-25 09:30:15",))


def test_first_run_pulls_everything_in_chunks():
    spark, connection = FakeSpark(watermark=None), FakeConnection(staging_rows(5))
    counts = bronze.ingest(spark, CONFIG, connection, 30, NOW, size=2)
    assert connection.executed == [("SELECT * FROM starrocks_audit_db__.starrocks_audit_tbl__ ORDER BY `timestamp`", ())]
    assert [len(rows) for _, rows in spark.appended] == [2, 2, 1]
    assert {table for table, _ in spark.appended} == {"lakehouse_dev.audit_lhdev.starrocks_audit_event_fe_brz"}
    assert counts == {"watermark": None, "since": None, "rows": 5, "chunks": 3, "first_event": "2026-11-25T10:00:00", "last_event": "2026-11-25T10:04:00"}


def test_later_runs_start_at_the_watermark_minus_the_lookback():
    spark, connection = FakeSpark(watermark=datetime(2026, 11, 25, 10, 45)), FakeConnection(staging_rows(1, 20))
    counts = bronze.ingest(spark, CONFIG, connection, 30, NOW)
    assert connection.executed[0][1] == ("2026-11-25 10:15:00",)
    assert counts["since"] == "2026-11-25T10:15:00" and counts["rows"] == 1
    assert spark.queries == ["SELECT max(event_time) AS watermark FROM lakehouse_dev.audit_lhdev.starrocks_audit_event_fe_brz"]


def test_rows_follow_the_contract_and_are_redacted():
    rows = staging_rows(1)
    rows[0] = rows[0][:-1] + ("CREATE USER 'x' IDENTIFIED BY 'Secret-123'",)
    spark = FakeSpark()
    bronze.ingest(spark, CONFIG, FakeConnection(rows), 30, NOW)
    ((_, (row,)),) = spark.appended
    query_id, event_time, payload, copied_at, loader = row
    assert (query_id, event_time, copied_at, loader) == ("q0", datetime(2026, 11, 25, 10), NOW, "bronze_ingestion")
    data = json.loads(payload)
    assert data["stmt"] == "CREATE USER 'x' IDENTIFIED BY '***'" and data["timestamp"] == "2026-11-25 10:00:00" and data["scanBytes"] == 0
    assert "Secret-123" not in payload
    silver_row = audit_sql.enrich_payload(*row)
    assert silver_row["event_key"] == "q0" and silver_row["statement_category"] == "ADMIN" and silver_row["user"] == "joe"


def test_the_same_staging_row_always_gives_the_same_payload():
    first = audit_sql.staging_to_bronze(dict(zip(COLUMNS, staging_rows(1)[0])), NOW, "bronze_ingestion")
    again = audit_sql.staging_to_bronze(dict(reversed(list(zip(COLUMNS, staging_rows(1)[0])))), datetime(2026, 11, 26), "bronze_ingestion")
    assert first[2] == again[2] and first[0] == again[0]


def test_missing_bronze_table_is_reported():
    with pytest.raises(RuntimeError, match="run the starrocks_deploy workflow first"):
        bronze.ingest(FakeSpark(exists=False), CONFIG, FakeConnection([]), 30, NOW)


def test_process_closes_the_connection_even_after_a_failure():
    connection = FakeConnection(staging_rows(2))
    record = bronze.process(FakeSpark(), CONFIG, 30, connect=lambda: connection)
    assert connection.closed and record["rows"] == 2 and record["source"] == "starrocks_audit_db__.starrocks_audit_tbl__"
    failing = FakeConnection([])
    with pytest.raises(RuntimeError):
        bronze.process(FakeSpark(exists=False), CONFIG, 30, connect=lambda: failing)
    assert failing.closed
