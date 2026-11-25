# %% [markdown]
# # StarRocks audit: bronze ingestion
#
# This Jupytext `py:percent` notebook pulls the StarRocks audit log into the bronze
# table `starrocks_audit_event_fe_brz` of the environment's namespace. StarRocks'
# AuditLoader plugin (installed by the platform) writes every audit event of the one
# shared StarRocks instance to the staging table
# `starrocks_audit_db__.starrocks_audit_tbl__` (config `starrocks_audit_table`), which
# keeps 7 days. Every environment (prod, uat and dev) pulls the same real stream into
# its own bronze table.
#
# Each run reads the newest `event_time` already in bronze (the watermark) and selects
# the staging rows from `lookback_minutes` before it (default 30, to catch rows the
# plugin wrote late); the first run takes everything the staging table holds. Rows are
# read in chunks of 20000 and appended as `query_id`, `event_time` (UTC), `payload`
# (the staging row as canonical JSON, credentials in the statement redacted to
# `'***'`), `copied_at` and `copy_task = 'bronze_ingestion'`. The lookback re-reads a
# few rows each run; silver drops them by event key. A missed run is caught up by the
# next one, for gaps up to the staging table's 7 days. Logs one JSON record at the
# start and a summary at the end.
#
# **Connection**: the platform's `lakehouse_starrocks.connect()`. In Airflow it logs
# in as the product writer with the run's token; in Jupyter it logs in as you and asks
# for your StarRocks (LDAP) password, which is never stored. Your account (or the
# writer) needs `SELECT` on the staging table, granted by the platform.
#
# **Prerequisites**: the bronze table exists (`deploy/deploy_bronze_table.py`, workflow
# `starrocks_deploy`). In Airflow, `workflows/starrocks/starrocks_audit.json` runs it
# hourly (task `starrocks_audit_event_fe_brz_load`) before the silver task. In Jupyter,
# run the Initialization cells, set `lookback_minutes` in `TASK_ARGS_VALUES` if needed,
# and run the remaining cells.

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
# Standard imports and logging, and the shared audit logic.

# %%
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone

from notebooks.common.audit_sql import BRONZE_COLUMNS, STAGING_TABLE, chunked, ddl_columns, staging_to_bronze, utc_naive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# %% [markdown]
# ## Task arguments
#
# The definition (which arguments the job accepts, in Airflow and Jupyter) and this
# run's values in Jupyter. Airflow uses the workflow's `args` (none: the default).

# %%
# The arguments this job accepts: how far before the bronze watermark to read again, in minutes.
TASK_ARGS_DEFINITION = {"lookback_minutes": {"default": "30"}}

# Jupyter only: this run's values, as strings (a name left out takes its default).
TASK_ARGS_VALUES = {"lookback_minutes": "30"}

MAX_LOOKBACK_MINUTES = 10_080  # 7 days, the staging table's retention
MINUTES = re.compile(r"0|[1-9][0-9]{0,4}")


def check_arguments(arguments: dict) -> int:
    """The lookback in minutes, from ``"0"`` to ``"10080"`` written in digits.

    Raises:
        ValueError: Any other value.
    """
    value = arguments.get("lookback_minutes")
    if not isinstance(value, str) or not MINUTES.fullmatch(value) or int(value) > MAX_LOOKBACK_MINUTES:
        raise ValueError(f"lookback_minutes must be a whole number of minutes from 0 to {MAX_LOOKBACK_MINUTES}, not {value!r}")
    return int(value)


# %% [markdown]
# ## Names
#
# The bronze table, its location, and the staging table to read (config
# `starrocks_audit_table`, defaulting to the AuditLoader table).

# %%
BRONZE_TABLE = "starrocks_audit_event_fe_brz"
LOADER = "bronze_ingestion"
CHUNK_SIZE = 20_000
STAGING_NAME = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+")


def qualified_table(config) -> str:
    """``<catalog_name>.<database_name>.starrocks_audit_event_fe_brz``."""
    return f"{config['catalog_name']}.{config['database_name']}.{BRONZE_TABLE}"


def staging_table(config) -> str:
    """The staging table, ``<database>.<table>`` (config ``starrocks_audit_table``).

    Raises:
        ValueError: The configured name is not two plain identifiers joined by ``.``.
    """
    name = config.get("starrocks_audit_table") or STAGING_TABLE
    if not isinstance(name, str) or not STAGING_NAME.fullmatch(name):
        raise ValueError(f"starrocks_audit_table must be <database>.<table> of letters, digits and _, not {name!r}")
    return name


# %% [markdown]
# ## Pull
#
# The watermark comes from bronze through Spark; the rows come from StarRocks through
# the platform's connection. The query's only parameter is the start time.


# %%
def bronze_watermark(spark, config):
    """The newest ``event_time`` in bronze, or ``None`` when it is empty.

    Raises:
        RuntimeError: The bronze table doesn't exist yet.
    """
    table = qualified_table(config)
    if not spark.catalog.tableExists(table):
        raise RuntimeError(f"bronze table {table} not found: run the starrocks_deploy workflow first")
    row = spark.sql(f"SELECT max(event_time) AS watermark FROM {table}").collect()[0]  # nosec B608 - table name from config, checked by Polaris
    return utc_naive(row["watermark"])


def pull_query(table: str, since) -> tuple:
    """The staging query and its parameters: rows from ``since`` (all rows when ``None``), oldest first."""
    if since is None:
        return f"SELECT * FROM {table} ORDER BY `timestamp`", ()  # nosec B608 - table name checked by staging_table
    return f"SELECT * FROM {table} WHERE `timestamp` >= %s ORDER BY `timestamp`", (since.strftime("%Y-%m-%d %H:%M:%S"),)  # nosec B608 - as above


def fetch_rows(connection, sql: str, parameters: tuple, size: int = CHUNK_SIZE):
    """The query's rows as dicts of column name to value, read ``size`` at a time."""
    cursor = connection.cursor()
    try:
        cursor.execute(sql, parameters)
        names = [column[0] for column in cursor.description]
        while True:
            batch = cursor.fetchmany(size)
            if not batch:
                return
            for values in batch:
                yield dict(zip(names, values))
    finally:
        cursor.close()


def append_rows(spark, config, rows: list) -> None:
    """Append bronze tuples to the bronze table."""
    spark.createDataFrame(rows, ddl_columns(BRONZE_COLUMNS)).writeTo(qualified_table(config)).append()


def open_connection():
    """A StarRocks connection from the platform: the product writer in Airflow, yourself in Jupyter."""
    from lakehouse_starrocks import connect

    return connect()


def ingest(spark, config, connection, lookback_minutes: int, now: datetime, size: int = CHUNK_SIZE) -> dict:
    """Pull the staging rows from the watermark minus the lookback into bronze; returns the run's counts."""
    watermark = bronze_watermark(spark, config)
    since = None if watermark is None else watermark - timedelta(minutes=lookback_minutes)
    sql, parameters = pull_query(staging_table(config), since)
    counts = {"rows": 0, "chunks": 0, "first_event": None, "last_event": None}
    for chunk in chunked(fetch_rows(connection, sql, parameters, size), size):
        rows = [staging_to_bronze(row, now, LOADER) for row in chunk]
        append_rows(spark, config, rows)
        counts["rows"] += len(rows)
        counts["chunks"] += 1
        counts["first_event"] = counts["first_event"] or rows[0][1].isoformat()
        counts["last_event"] = rows[-1][1].isoformat()
    return {"watermark": watermark.isoformat() if watermark else None, "since": since.isoformat() if since else None, **counts}


def process(spark, config, lookback_minutes: int, connect=open_connection) -> dict:
    """Open the connection, ingest, log; returns the summary record."""
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    target = {"layer": "bronze", "environment": getattr(config, "environment", None), "source": staging_table(config), "table": qualified_table(config), "lookback_minutes": lookback_minutes}
    logger.info("Bronze ingestion start: %s", json.dumps(target, sort_keys=True))
    connection = connect()
    try:
        counts = ingest(spark, config, connection, lookback_minutes, datetime.now(timezone.utc).replace(tzinfo=None))
    finally:
        connection.close()
    record = {**target, **counts}
    logger.info("Bronze ingestion summary: %s", json.dumps(record, sort_keys=True))
    return record


# %% [markdown]
# ## Execution
#
# Reads and checks the task arguments (the workflow's `args` in Airflow,
# `TASK_ARGS_VALUES` in Jupyter). In Airflow, `start_spark_job` loads the environment's
# config and starts Spark; in Jupyter, the Initialization already defined `spark` and
# `config`. Spark is stopped at the end, also after a failure.

# %%
if __name__ == "__main__":
    # Importing this notebook from pytest must not start Spark or parse arguments.
    from notebooks.common.cli import parse_task_args

    try:
        lookback_minutes = check_arguments(parse_task_args(TASK_ARGS_DEFINITION, prog="bronze_ingestion.py"))
    except ValueError as error:
        if "ipykernel" in sys.modules:
            raise
        print(f"bronze_ingestion.py: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    if "ipykernel" not in sys.modules:
        from notebooks.common.job_runtime import start_spark_job

        spark, config = start_spark_job("bronze_ingestion")
    try:
        process(spark, config, lookback_minutes)
    finally:
        spark.stop()
