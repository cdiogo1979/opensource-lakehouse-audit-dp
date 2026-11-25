# %% [markdown]
# # StarRocks audit: deploy the bronze table
#
# This Jupytext `py:percent` notebook creates the bronze table
# `starrocks_audit_event_fe_brz` in the environment's namespace, at
# `<storage_path>/<catalog_name>/<database_name>/starrocks_audit_event_fe_brz`.
# The table is the contract with the lakehouse platform: an hourly StarRocks task
# copies the last 2 hours of StarRocks audit rows into it (prod and uat), with the
# columns `query_id`, `event_time` (UTC), `payload` (the audit row as JSON, credentials
# redacted), `copied_at` and `copy_task`. It is an Iceberg table, format-version 2,
# written as Parquet (StarRocks writes only Parquet Iceberg tables), partitioned by
# day of `event_time`, and append-only; nothing deletes its rows.
#
# Safe to re-run: an existing table with the expected columns is left alone; one
# with other columns stops the run (it is never altered). Logs one JSON record.
#
# **Run it** once per environment before the platform's copy task is enabled:
# in Airflow with the manual workflow `starrocks_deploy` (task
# `starrocks_audit_event_fe_brz_create`, as the product writer) for uat and prod; in
# Jupyter for dev (owners and developers can create tables there). No task arguments.

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
# Standard imports and logging, and the bronze contract from the shared audit module.

# %%
import json
import logging

from notebooks.common.audit_sql import BRONZE_COLUMNS, ddl_columns

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# %% [markdown]
# ## Names
#
# The bronze table follows `<project>_<entity>_<source>_brz`: project `starrocks`,
# entity `audit_event`, source `fe` (the StarRocks frontend that writes the audit log).

# %%
BRONZE_TABLE = "starrocks_audit_event_fe_brz"


def qualified_table(config, table: str = BRONZE_TABLE) -> str:
    """``<catalog_name>.<database_name>.<table>`` from the environment config."""
    return f"{config['catalog_name']}.{config['database_name']}.{table}"


def table_location(config, table: str = BRONZE_TABLE) -> str:
    """``<storage_path>/<catalog_name>/<database_name>/<table>``, joined by exactly one ``/``."""
    return "/".join((config["storage_path"].rstrip("/"), config["catalog_name"], config["database_name"], table))


# %% [markdown]
# ## Bronze table
#
# The DDL and the schema check. Spark reports types in lower case (`string`,
# `timestamp`), the contract writes them in upper case.


# %%
def create_table_sql(config) -> str:
    """The ``CREATE TABLE`` statement of the bronze contract for this environment."""
    return (
        f"CREATE TABLE {qualified_table(config)} ({ddl_columns(BRONZE_COLUMNS)}) USING iceberg "
        f"PARTITIONED BY (days(event_time)) LOCATION '{table_location(config)}' "
        "TBLPROPERTIES ('format-version'='2', 'write.format.default'='parquet')"
    )


def schema_problems(actual: list) -> list:
    """Differences between an existing table's ``(name, type)`` columns and the contract.

    Args:
        actual: ``(name, simple type)`` pairs, as Spark reports them.

    Returns:
        One message per difference; empty when the table matches.
    """
    expected = [(name, kind.lower()) for name, kind in BRONZE_COLUMNS]
    found = [(str(name).lower(), str(kind).lower()) for name, kind in actual]
    if found == expected:
        return []
    problems = [f"missing column {name} {kind}" for name, kind in expected if (name, kind) not in found]
    problems += [f"unexpected column {name} {kind}" for name, kind in found if (name, kind) not in expected]
    return problems or ["the columns are in a different order than the contract"]


def ensure_bronze_table(spark, config) -> str:
    """Create the bronze table if it is missing; check it otherwise.

    Returns:
        ``created`` or ``exists``.

    Raises:
        ValueError: The existing table's columns differ from the contract (it is not altered).
    """
    table = qualified_table(config)
    if spark.catalog.tableExists(table):
        problems = schema_problems([(field.name, field.dataType.simpleString()) for field in spark.table(table).schema.fields])
        if problems:
            raise ValueError(f"{table} does not match the bronze contract: " + "; ".join(problems))
        return "exists"
    spark.sql(create_table_sql(config))
    return "created"


def deploy(spark, config) -> dict:
    """Ensure the bronze table and log what was done; returns the logged record."""
    record = {
        "layer": "bronze",
        "environment": getattr(config, "environment", None),
        "table": qualified_table(config),
        "location": table_location(config),
        "result": ensure_bronze_table(spark, config),
    }
    logger.info("Bronze table deploy: %s", json.dumps(record, sort_keys=True))
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

        spark, config = start_spark_job("deploy_bronze_table")
    try:
        deploy(spark, config)
    finally:
        spark.stop()
