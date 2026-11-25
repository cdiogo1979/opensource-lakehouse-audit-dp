"""The silver processing notebook (C3): names, DDL, insert-only merge, chunked enrichment and dedup, with a fake Spark."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

import silver_processing as silver
from notebooks.common.audit_sql import SILVER_EVENT_COLUMNS, TABLE_ACCESS_COLUMNS
from notebooks.common.config_loader import EnvironmentConfig

SETTINGS = {"storage_path": "s3a://bucket-audit/", "catalog_name": "lakehouse", "database_name": "audit_lh", "checkpoint_path": "s3ckpt://bucket-audit/checkpoints"}
CONFIG = EnvironmentConfig(environment="prod", settings=SETTINGS)
LOADED = datetime(2026, 11, 25, 11, 20)


class FakeSession:
    """Records SQL and models insert-only MERGE over in-memory tables keyed like silver."""

    def __init__(self, bronze_exists=True):
        self.statements = []
        self.tables = {"lakehouse.audit_lh.starrocks_audit_event": {}, "lakehouse.audit_lh.starrocks_audit_table_access": {}}
        self.views = {}
        self.catalog = SimpleNamespace(tableExists=lambda name: bronze_exists)
        self.conf = SimpleNamespace(set=lambda key, value: self.statements.append(f"conf {key}={value}"))

    def createDataFrame(self, rows, schema):
        names = [part.split("`")[1] for part in schema.split(", ")]
        frame = SimpleNamespace(rows=[dict(zip(names, row)) for row in rows])
        frame.createOrReplaceTempView = lambda view: self.views.__setitem__(view, frame.rows)
        return frame

    def sql(self, statement):
        self.statements.append(statement)
        if statement.startswith("MERGE INTO "):
            target, view = statement.split()[2], statement.split()[5]
            keys = [part.split("`")[1] for part in statement.split(" ON ")[1].split(" WHEN ")[0].split(" AND ")]
            table = self.tables[target]
            for row in self.views[view]:
                table.setdefault(tuple(row[key] for key in keys), row)


def bronze_row(query_id, copied_hour, **payload):
    body = {
        "queryId": query_id,
        "timestamp": "2026-11-25 10:00:00",
        "user": "maria-owner",
        "db": "sales_lh",
        "catalog": "lakehouse",
        "state": "EOF",
        "stmt": "SELECT * FROM orders JOIN items ON 1 = 1",
    }
    body.update(payload)
    return {"query_id": query_id, "event_time": datetime(2026, 11, 25, 10), "payload": json.dumps(body), "copied_at": datetime(2026, 11, 25, copied_hour), "copy_task": "audit_copy_prod"}


def test_names_locations_and_checkpoint():
    assert silver.targets(CONFIG) == {
        "environment": "prod",
        "source": "lakehouse.audit_lh.starrocks_audit_event_fe_brz",
        "events": "lakehouse.audit_lh.starrocks_audit_event",
        "events_location": "s3a://bucket-audit/lakehouse/audit_lh/starrocks_audit_event",
        "table_access": "lakehouse.audit_lh.starrocks_audit_table_access",
        "table_access_location": "s3a://bucket-audit/lakehouse/audit_lh/starrocks_audit_table_access",
        "checkpoint": "s3ckpt://bucket-audit/checkpoints/starrocks/starrocks_audit_event",
    }


@pytest.mark.parametrize("value", [None, "", "s3a://bucket-audit/checkpoints", "s3ckpt://", 5])
def test_checkpoint_must_be_an_s3ckpt_path(value):
    with pytest.raises(ValueError, match="checkpoint_path"):
        silver.checkpoint_location(EnvironmentConfig(environment="prod", settings={**SETTINGS, "checkpoint_path": value}))


def test_silver_ddl_is_partitioned_by_event_date():
    sql = silver.create_table_sql(CONFIG, silver.EVENT_TABLE, SILVER_EVENT_COLUMNS)
    assert sql.startswith("CREATE TABLE IF NOT EXISTS lakehouse.audit_lh.starrocks_audit_event (`event_key` STRING, ")
    assert "PARTITIONED BY (event_date)" in sql and "LOCATION 's3a://bucket-audit/lakehouse/audit_lh/starrocks_audit_event'" in sql
    session = FakeSession()
    silver.ensure_silver_tables(session, CONFIG)
    assert [s.split(" (")[0] for s in session.statements] == [
        "CREATE TABLE IF NOT EXISTS lakehouse.audit_lh.starrocks_audit_event",
        "CREATE TABLE IF NOT EXISTS lakehouse.audit_lh.starrocks_audit_table_access",
    ]


def test_merge_is_insert_only():
    sql = silver.merge_sql("c.d.t", "v", ("event_key", "event_date"))
    assert sql == "MERGE INTO c.d.t t USING v s ON t.`event_key` = s.`event_key` AND t.`event_date` = s.`event_date` WHEN NOT MATCHED THEN INSERT *"
    assert "UPDATE" not in sql and "DELETE" not in sql


def test_missing_bronze_stops_the_run():
    with pytest.raises(RuntimeError, match="run the starrocks_deploy workflow first"):
        silver.check_bronze(FakeSession(bronze_exists=False), CONFIG)


def test_duplicates_across_chunks_and_replays_insert_once():
    session = FakeSession()
    rows = [bronze_row("q1", 11), bronze_row("q2", 11), bronze_row("q1", 10), bronze_row(None, 11, eventType="CONNECTION", stmt=None), bronze_row(None, 12, eventType="CONNECTION", stmt=None)]
    counts = silver.new_counts()
    silver.merge_batch_rows(session, CONFIG, iter(rows), counts, LOADED, size=2)
    events = session.tables["lakehouse.audit_lh.starrocks_audit_event"]
    assert counts["rows_read"] == 5 and len(events) == 3
    keys = sorted(key[0] for key in events)
    assert keys[:2] == ["q1", "q2"] and keys[2].startswith("sha256:")
    accesses = session.tables["lakehouse.audit_lh.starrocks_audit_table_access"]
    assert sorted((key[0], key[3]) for key in accesses) == [("q1", "items"), ("q1", "orders"), ("q2", "items"), ("q2", "orders")]
    before = {name: dict(table) for name, table in session.tables.items()}
    silver.merge_batch_rows(session, CONFIG, iter(rows), silver.new_counts(), LOADED)  # a replay inserts nothing
    assert session.tables == before


def test_rows_carry_every_silver_column_and_loaded_at():
    session = FakeSession()
    silver.merge_chunk(session, CONFIG, list(next(silver.enriched_chunks([bronze_row("q1", 11), bronze_row("q1", 10)]))), LOADED)
    (row,) = session.views["starrocks_audit_event_updates"]
    assert list(row) == [name for name, _ in SILVER_EVENT_COLUMNS]
    assert row["loaded_at"] == LOADED and row["first_copied_at"] == datetime(2026, 11, 25, 10)
    assert list(session.views["starrocks_audit_table_access_updates"][0]) == [name for name, _ in TABLE_ACCESS_COLUMNS]


def test_unparsed_rows_are_counted_and_kept():
    session = FakeSession()
    counts = silver.new_counts()
    silver.merge_batch_rows(session, CONFIG, [{"query_id": None, "event_time": datetime(2026, 11, 25), "payload": "garbage", "copied_at": datetime(2026, 11, 25), "copy_task": "t"}], counts, LOADED)
    assert counts["unparsed"] == 1 and counts["table_accesses"] == 0
    (row,) = session.tables["lakehouse.audit_lh.starrocks_audit_event"].values()
    assert row["event_type"] == "unparsed" and row["payload"] == "garbage"


class FakeStream:
    """readStream ... start() chain that runs foreachBatch once over given rows."""

    def __init__(self, session, rows):
        self.session, self.rows, self.options = session, rows, {}

    def format(self, name):
        self.options["format"] = name
        return self

    def load(self, source):
        self.options["source"] = source
        return self

    @property
    def writeStream(self):
        return self

    def foreachBatch(self, function):
        self.function = function
        return self

    def option(self, key, value):
        self.options[key] = value
        return self

    def trigger(self, **kwargs):
        self.options["trigger"] = kwargs
        return self

    def start(self):
        batch = SimpleNamespace(sparkSession=self.session, select=lambda *columns: SimpleNamespace(toLocalIterator=lambda: iter(self.rows)))
        self.function(batch, 0)
        return SimpleNamespace(awaitTermination=lambda: None)


def test_process_streams_with_the_checkpoint_and_logs_counts():
    session = FakeSession()
    stream = FakeStream(session, [bronze_row("q1", 11)])
    session.readStream = stream
    record = silver.process(session, CONFIG)
    assert stream.options == {
        "format": "iceberg",
        "source": "lakehouse.audit_lh.starrocks_audit_event_fe_brz",
        "checkpointLocation": "s3ckpt://bucket-audit/checkpoints/starrocks/starrocks_audit_event",
        "trigger": {"availableNow": True},
    }
    assert session.statements[0] == "conf spark.sql.session.timeZone=UTC"
    assert record["batches"] == 1 and record["events"] == 1 and record["table_accesses"] == 2
