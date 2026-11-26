"""Spark smoke test of the starrocks notebooks: real Spark 3.5 and Iceberg, a local Hadoop catalog, no platform services.

Runs the bronze deploy, the bronze pull (from a fake StarRocks connection holding the sample generator's rows),
silver three times (first load, nothing new, duplicates only) and the optional sample writer, then prints the
counts as one JSON record (SMOKE_RESULTS). Run it in the platform's Spark runtime image, with no network:

    mkdir -p /tmp/starrocks-smoke
    docker run --rm --network none --hostname localhost -e SPARK_LOCAL_IP=127.0.0.1 -e SPARK_LOCAL_HOSTNAME=localhost \
        -v "$PWD":/repo:ro -v /tmp/starrocks-smoke:/work --entrypoint /opt/spark/bin/spark-submit \
        local/lakehouse-spark-runtime:3.5.9_1.10.2 --master 'local[2]' /repo/scripts/spark_smoke_starrocks.py

Expected: deploy created then exists; silver 600 events with 600 distinct keys after all three runs;
512 table accesses; no fake password in bronze or silver.
"""

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/repo")
from pyspark.sql import SparkSession  # noqa: E402

from notebooks.common.config_loader import EnvironmentConfig  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = "/repo/notebooks/workload/starrocks"
deploy, bronze, silver, ops = (load(f"{base}/{p}") for p in ("deploy/deploy_bronze_table.py", "bronze/bronze_ingestion.py", "silver/silver_processing.py", "ops/ops_generate_sample_events.py"))

spark = (
    SparkSession.builder.master("local[2]")
    .appName("starrocks-smoke")
    .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
    .config("spark.sql.catalog.lakehouse_dev", "org.apache.iceberg.spark.SparkCatalog")
    .config("spark.sql.catalog.lakehouse_dev.type", "hadoop")
    .config("spark.sql.catalog.lakehouse_dev.warehouse", "file:/work/wh/lakehouse_dev")
    .config("spark.sql.defaultCatalog", "lakehouse_dev")
    .config("spark.ui.enabled", "false")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("ERROR")
config = EnvironmentConfig(
    environment="dev", settings={"storage_path": "file:/work/wh/", "catalog_name": "lakehouse_dev", "database_name": "audit_lhdev", "checkpoint_path": "s3ckpt://bucket-audit-dev/checkpoints"}
)
silver.checkpoint_location = lambda config: "file:/work/checkpoints/starrocks/starrocks_audit_event"  # s3ckpt:// is the platform's filesystem
spark.sql("CREATE NAMESPACE IF NOT EXISTS lakehouse_dev.audit_lhdev")
results = {}

# 1. Deploy bronze: created, then exists; the real schema passes the contract check.
results["deploy_first"] = deploy.ensure_bronze_table(spark, config)
results["deploy_again"] = deploy.ensure_bronze_table(spark, config)
spec = spark.sql("DESCRIBE TABLE EXTENDED lakehouse_dev.audit_lhdev.starrocks_audit_event_fe_brz").collect()
results["bronze_partitioning"] = [r["data_type"] for r in spec if r["col_name"].startswith("Part 0")]
props = {r["col_name"]: r["data_type"] for r in spec}
results["bronze_location"] = props.get("Location")


# 2. Bronze pull from a fake StarRocks connection holding realistic staging rows (the sample generator's).
class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.description = None

    def execute(self, sql, params):
        since = datetime.strptime(params[0], "%Y-%m-%d %H:%M:%S") if params else None
        rows = [r for r in self.rows if since is None or r["timestamp"] >= since]
        names = sorted({k for r in rows for k in r})
        self.description = [(n,) for n in names]
        self.pending = [tuple(r.get(n) for n in names) for r in sorted(rows, key=lambda r: r["timestamp"])]

    def fetchmany(self, size):
        batch, self.pending = self.pending[:size], self.pending[size:]
        return batch

    def close(self):
        pass


class Connection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return Cursor(self.rows)

    def close(self):
        pass


events = ops.generate_events(600, 5, datetime(2026, 11, 25, 9))
staging = [{**e, "timestamp": datetime.strptime(e["timestamp"], "%Y-%m-%d %H:%M:%S.%f")} for e in events]
results["bronze_run1"] = bronze.ingest(spark, config, Connection(staging), 30, datetime(2026, 11, 25, 10, 20), size=250)
results["bronze_run2_lookback"] = bronze.ingest(spark, config, Connection(staging), 30, datetime(2026, 11, 25, 11, 20), size=250)
results["bronze_rows"] = spark.table("lakehouse_dev.audit_lhdev.starrocks_audit_event_fe_brz").count()

# 3. Silver: stream, merge; a second run adds nothing; a third after more bronze duplicates adds nothing either.
results["silver_run1"] = {k: v for k, v in silver.process(spark, config).items() if k in ("batches", "rows_read", "events", "unparsed", "table_accesses")}
results["silver_run2"] = {k: v for k, v in silver.process(spark, config).items() if k in ("batches", "rows_read", "events")}
bronze.ingest(spark, config, Connection(staging), 30, datetime(2026, 11, 25, 12, 20))
results["silver_run3_after_duplicates"] = {k: v for k, v in silver.process(spark, config).items() if k in ("batches", "rows_read", "events")}
# The outer session caches tables it created; refresh before reading what the micro-batches wrote.
for name in ("starrocks_audit_event", "starrocks_audit_table_access", "starrocks_audit_event_fe_brz"):
    spark.sql(f"REFRESH TABLE lakehouse_dev.audit_lhdev.{name}")
ev = spark.table("lakehouse_dev.audit_lhdev.starrocks_audit_event")
results["silver_events"] = ev.count()
results["silver_distinct_keys"] = ev.select("event_key").distinct().count()
results["expected_events"] = len(events)
results["table_access_rows"] = spark.table("lakehouse_dev.audit_lhdev.starrocks_audit_table_access").count()
results["fake_passwords_in_silver"] = ev.filter("sql_text LIKE '%FakePass-%' OR payload LIKE '%FakePass-%' OR sql_masked LIKE '%FakePass-%'").count()
results["fake_passwords_in_bronze"] = spark.table("lakehouse_dev.audit_lhdev.starrocks_audit_event_fe_brz").filter("payload LIKE '%FakePass-%'").count()
results["by_type"] = {r["event_type"]: r["n"] for r in ev.groupBy("event_type").count().withColumnRenamed("count", "n").collect()}
results["by_category"] = {str(r["statement_category"]): r["n"] for r in ev.groupBy("statement_category").count().withColumnRenamed("count", "n").collect()}
spec = spark.sql("DESCRIBE TABLE EXTENDED lakehouse_dev.audit_lhdev.starrocks_audit_event").collect()
results["silver_partitioning"] = [r["data_type"] for r in spec if r["col_name"].startswith("Part 0")] or [r["col_name"] for r in spec if r["col_name"] == "event_date" and r["data_type"] == "date"][:1]
results["silver_schema_matches"] = [f.name for f in ev.schema.fields] == [n for n, _ in silver.SILVER_EVENT_COLUMNS]
results["sample_row"] = ev.filter("event_type = 'query' AND statement_category = 'SELECT'").select("event_key", "user", "actor_type", "data_product", "environment", "sql_masked").first()

# 4. The optional sample writer appends through writeTo().append() with the DDL schema.
results["ops_rows_written"] = ops.write_sample_events(spark, config, ops.bronze_rows(ops.generate_events(10, 9, datetime(2026, 11, 25, 9)), datetime(2026, 11, 25, 10), 9))

print("SMOKE_RESULTS " + json.dumps(results, default=str, indent=1, sort_keys=True))
spark.stop()
