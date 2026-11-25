# %% [markdown]
# # StarRocks audit: silver processing
#
# This Jupytext `py:percent` notebook turns the StarRocks audit rows that
# `bronze/bronze_ingestion.py` pulls into bronze (`starrocks_audit_event_fe_brz`) into two silver
# tables in the environment's namespace:
#
# - `starrocks_audit_event`: one row per audit event (query, connection, or
#   `unparsed` when the payload can't be read), with the full SQL text
#   (credentials redacted), the masked SQL text (literals as `?`), the statement
#   category, the actor type (person, product writer, platform service), and the
#   data product and environment the event belongs to;
# - `starrocks_audit_table_access`: one row per event and table it names.
#
# Changes come from Spark Structured Streaming on the bronze Iceberg table
# (`trigger(availableNow=True)`), remembered in the checkpoint
# `<checkpoint_path>/starrocks/starrocks_audit_event`. Each micro-batch is read on
# the driver in chunks of at most 20000 rows, enriched with
# `notebooks/common/audit_sql.py`, deduplicated by `event_key` (bronze holds some
# rows twice, from the bronze task's lookback) and merged **insert-only**:
# silver rows are never updated or deleted, so retries, replays and a lost
# checkpoint never create duplicates. The first run creates both silver tables,
# partitioned by `event_date`. It logs one JSON record at the start and a summary
# at the end (rows read, events inserted, unparsed rows, table accesses).
#
# Requires the bronze table (run the `starrocks_deploy` workflow first). In
# Airflow, `workflows/starrocks/starrocks_audit.json` runs it every hour after the
# bronze task (task `starrocks_audit_event_load`). In Jupyter, run
# `bronze/bronze_ingestion.py` first. No task arguments.

# %% [markdown]
# ## Initialization
#
# Jupyter only: in `spark-submit` (Airflow) these two cells are comments.
# 1. Install this repository's extra packages (`requirements.txt`). After the
#    first install in a server session, restart the kernel, then run all cells.
# 2. Find this clone, load its config and start Spark: `dev` by default; for
#    another environment, run `%env LAKEHOUSE_ENVIRONMENT=uat` (or `prod`) first.
#    It defines `spark` and `config`.

# %%
# !lakehouse-install-requirements ~/work/opensource-lakehouse-audit-dp

# %%
# %run ../../../common/jupyter_environment_resolver.py

# %% [markdown]
# ## Imports
#
# Standard imports and logging, and the shared audit logic. Under `spark-submit` the
# repository root is on `PYTHONPATH`; in Jupyter the Initialization put it there.

# %%
import json
import logging
from datetime import datetime, timezone

from notebooks.common.audit_sql import (
    BRONZE_COLUMNS,
    EVENT_KEY_COLUMNS,
    SILVER_EVENT_COLUMNS,
    TABLE_ACCESS_COLUMNS,
    TABLE_ACCESS_KEY_COLUMNS,
    as_tuple,
    chunked,
    ddl_columns,
    deduplicate,
    enrich_payload,
    table_access_rows,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# %% [markdown]
# ## Names
#
# Every table lives in the environment's one namespace, at
# `<storage_path>/<catalog_name>/<database_name>/<table>`; the checkpoint is
# `<checkpoint_path>/starrocks/starrocks_audit_event`.

# %%
PROJECT = "starrocks"
BRONZE_TABLE = "starrocks_audit_event_fe_brz"
EVENT_TABLE = "starrocks_audit_event"
ACCESS_TABLE = "starrocks_audit_table_access"
CHECKPOINT_SCHEME = "s3ckpt://"
CHUNK_SIZE = 20_000


def qualified_table(config, table: str) -> str:
    """``<catalog_name>.<database_name>.<table>`` from the environment config."""
    return f"{config['catalog_name']}.{config['database_name']}.{table}"


def table_location(config, table: str) -> str:
    """``<storage_path>/<catalog_name>/<database_name>/<table>``, joined by exactly one ``/``."""
    return "/".join((config["storage_path"].rstrip("/"), config["catalog_name"], config["database_name"], table))


def checkpoint_location(config) -> str:
    """``<checkpoint_path>/starrocks/starrocks_audit_event``.

    Raises:
        ValueError: ``checkpoint_path`` is missing, empty, or not an ``s3ckpt://`` path.
    """
    base = config.get("checkpoint_path")
    if not isinstance(base, str) or not base.startswith(CHECKPOINT_SCHEME) or len(base.rstrip("/")) <= len(CHECKPOINT_SCHEME):
        raise ValueError(f"the environment config needs checkpoint_path, an {CHECKPOINT_SCHEME} path in the product's bucket, not {base!r}")
    return "/".join((base.rstrip("/"), PROJECT, EVENT_TABLE))


def targets(config) -> dict:
    """What this run reads and writes (logged)."""
    return {
        "environment": getattr(config, "environment", None),
        "source": qualified_table(config, BRONZE_TABLE),
        "events": qualified_table(config, EVENT_TABLE),
        "events_location": table_location(config, EVENT_TABLE),
        "table_access": qualified_table(config, ACCESS_TABLE),
        "table_access_location": table_location(config, ACCESS_TABLE),
        "checkpoint": checkpoint_location(config),
    }


# %% [markdown]
# ## Tables and merge
#
# Both silver tables are partitioned by `event_date`, so the gold views can be
# partitioned in line with them. The merge only inserts rows whose key is absent.


# %%
def create_table_sql(config, table: str, columns) -> str:
    """``CREATE TABLE IF NOT EXISTS`` for a silver table, partitioned by ``event_date``."""
    return (
        f"CREATE TABLE IF NOT EXISTS {qualified_table(config, table)} ({ddl_columns(columns)}) USING iceberg "
        f"PARTITIONED BY (event_date) LOCATION '{table_location(config, table)}' TBLPROPERTIES ('format-version'='2')"
    )


def ensure_silver_tables(spark, config) -> None:
    """Create both silver tables if they are missing."""
    spark.sql(create_table_sql(config, EVENT_TABLE, SILVER_EVENT_COLUMNS))
    spark.sql(create_table_sql(config, ACCESS_TABLE, TABLE_ACCESS_COLUMNS))


def check_bronze(spark, config) -> None:
    """Stop when the bronze table is missing (BR-34)."""
    source = qualified_table(config, BRONZE_TABLE)
    if not spark.catalog.tableExists(source):
        raise RuntimeError(f"bronze table {source} not found: run the starrocks_deploy workflow first")


def merge_sql(target: str, view: str, keys) -> str:
    """An insert-only ``MERGE INTO``: rows whose key is already in ``target`` are skipped (BR-32)."""
    condition = " AND ".join(f"t.`{key}` = s.`{key}`" for key in keys)
    return f"MERGE INTO {target} t USING {view} s ON {condition} WHEN NOT MATCHED THEN INSERT *"


# %% [markdown]
# ## Micro-batches
#
# A micro-batch is read on the driver in chunks (no Python UDFs: in Jupyter, Spark's
# Python workers don't have this repository on their import path). Each chunk is
# enriched, deduplicated and merged; a key seen in an earlier chunk is skipped by the
# merge.


# %%
def enriched_chunks(rows, size: int = CHUNK_SIZE):
    """Bronze rows (Spark ``Row`` or dict) enriched into silver event dicts, ``size`` at a time."""
    for chunk in chunked(rows, size):
        yield [enrich_payload(row["query_id"], row["event_time"], row["payload"], row["copied_at"], row["copy_task"]) for row in chunk]


def merge_chunk(session, config, events: list, loaded_at: datetime) -> dict:
    """Merge one enriched chunk into both silver tables; returns its counts."""
    events = deduplicate(events, EVENT_KEY_COLUMNS, "first_copied_at")
    for event in events:
        event["loaded_at"] = loaded_at
    accesses = deduplicate(table_access_rows(events), TABLE_ACCESS_KEY_COLUMNS)
    for table, columns, keys, rows in ((EVENT_TABLE, SILVER_EVENT_COLUMNS, EVENT_KEY_COLUMNS, events), (ACCESS_TABLE, TABLE_ACCESS_COLUMNS, TABLE_ACCESS_KEY_COLUMNS, accesses)):
        if not rows:
            continue
        view = f"{table}_updates"
        session.createDataFrame([as_tuple(row, columns) for row in rows], ddl_columns(columns)).createOrReplaceTempView(view)
        session.sql(merge_sql(qualified_table(config, table), view, keys))
    return {"events": len(events), "unparsed": sum(1 for event in events if event["event_type"] == "unparsed"), "table_accesses": len(accesses)}


def merge_batch_rows(session, config, rows, counts: dict, loaded_at: datetime, size: int = CHUNK_SIZE) -> None:
    """Enrich and merge every row of one micro-batch, adding to ``counts``."""
    for events in enriched_chunks(rows, size):
        counts["rows_read"] += len(events)
        for name, value in merge_chunk(session, config, events, loaded_at).items():
            counts[name] += value


# %% [markdown]
# ## Stream
#
# The stream reads the bronze snapshots appended since the last run and stops when
# it has caught up (`availableNow`). Times are handled in UTC.


# %%
def new_counts() -> dict:
    """Zeroed run counters."""
    return {"batches": 0, "rows_read": 0, "events": 0, "unparsed": 0, "table_accesses": 0}


def run_stream(spark, config, checkpoint: str) -> dict:
    """Stream the new bronze rows into silver; returns the run's counts."""
    counts = new_counts()
    source = qualified_table(config, BRONZE_TABLE)
    columns = [name for name, _ in BRONZE_COLUMNS]

    def merge_batch(batch, batch_id):
        counts["batches"] += 1
        loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        merge_batch_rows(batch.sparkSession, config, batch.select(*columns).toLocalIterator(), counts, loaded_at)

    query = spark.readStream.format("iceberg").load(source).writeStream.foreachBatch(merge_batch).option("checkpointLocation", checkpoint).trigger(availableNow=True).start()
    query.awaitTermination()
    return counts


def process(spark, config) -> dict:
    """Check bronze, create silver if needed, stream, and log; returns the summary record."""
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    target = targets(config)
    logger.info("Silver processing start: %s", json.dumps({"layer": "silver", **target}, sort_keys=True))
    check_bronze(spark, config)
    ensure_silver_tables(spark, config)
    counts = run_stream(spark, config, target["checkpoint"])
    record = {"layer": "silver", **target, **counts}
    if counts["unparsed"]:
        logger.warning("Silver processing: %d unparsed audit rows kept with event_type 'unparsed'", counts["unparsed"])
    logger.info("Silver processing summary: %s", json.dumps(record, sort_keys=True))
    return record


# %% [markdown]
# ## Execution
#
# In Airflow, `start_spark_job` loads the environment's config and starts Spark; in
# Jupyter, the Initialization already defined `spark` and `config`. Spark is stopped
# at the end, also after a failure.

# %%
if __name__ == "__main__":
    # Importing this notebook from pytest must not start Spark.
    import sys

    if "ipykernel" not in sys.modules:
        from notebooks.common.job_runtime import start_spark_job

        spark, config = start_spark_job("silver_processing")
    try:
        process(spark, config)
    finally:
        spark.stop()
