# %% [markdown]
# # StarRocks audit: generate sample events (dev only)
#
# This Jupytext `py:percent` notebook appends synthetic StarRocks audit rows to the
# **dev** bronze table `starrocks_audit_event_fe_brz`. Optional: dev normally pulls the
# real audit log with `bronze/bronze_ingestion.py`; use this when StarRocks or the
# AuditLoader plugin isn't available, or to exercise edge cases (failures, slow queries,
# credentials, rows without a query ID). The rows have the same shape as the bronze
# task's (`query_id`, `event_time`, `payload` with AuditLoader's camelCase
# keys, `copied_at`, `copy_task = 'ops_generate_sample_events'`) and cover every
# statement category, people, product writers and platform services, the products
# `default`, `audit` and `sales` in every environment, failures, slow queries (60 s or
# more), connection events (some failed), `CREATE USER ... IDENTIFIED BY` statements
# with fake passwords (not redacted, unlike the bronze task's rows, so silver's
# redaction is exercised), events without a query ID, and about 20% duplicated rows. The same `--seed` and start hour give the same rows.
#
# **Prerequisites**: the dev bronze table exists (run `deploy/deploy_bronze_table.py`
# in Jupyter first). **Run it** in Jupyter with `dev` only: it refuses any other
# environment. Then run `silver/silver_processing.py`. Arguments: `events` (how many
# audit events, 1 to 100000, default 200) and `seed` (default 1). Each run appends;
# the silver load ignores duplicates by event key.

# %% [markdown]
# ## Initialization
#
# Jupyter only: in `spark-submit` (Airflow) these two cells are comments.
# 1. Install this repository's extra packages (`requirements.txt`). After the
#    first install in a server session, restart the kernel, then run all cells.
# 2. Find this clone, load its config and start Spark: `dev` by default. It defines
#    `spark` and `config`.

# %%
# !lakehouse-install-requirements ~/work/opensource-lakehouse-audit-dp

# %%
# %run ../../../common/jupyter_environment_resolver.py

# %% [markdown]
# ## Imports
#
# Standard imports and logging, and the bronze contract from the shared audit module.

# %%
import hashlib
import json
import logging
import random
import re
import sys
from datetime import datetime, timedelta, timezone

from notebooks.common.audit_sql import BRONZE_COLUMNS, ddl_columns, mask_sql

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# %% [markdown]
# ## Task arguments
#
# The definition (which arguments the job accepts) and this run's values in Jupyter.
# The common parser only reads them; `check_arguments` checks them.

# %%
# The arguments this job accepts: how many audit events, and the random seed.
TASK_ARGS_DEFINITION = {"events": {"default": "200"}, "seed": {"default": "1"}}

# Jupyter only: this run's values, as strings (a name left out takes its default).
TASK_ARGS_VALUES = {"events": "200", "seed": "1"}

MAX_EVENTS = 100_000
INTEGER = re.compile(r"[+-]?\d{1,9}")


def check_arguments(arguments: dict, environment: str) -> tuple:
    """The event count and seed, in dev only (BR-50).

    Args:
        arguments: The mapping from ``parse_task_args(TASK_ARGS_DEFINITION)``.
        environment: The run's environment.

    Returns:
        ``(events, seed)``.

    Raises:
        ValueError: Another environment, a count outside 1 to 100000, or a seed that isn't an integer.
    """
    if environment != "dev":
        raise ValueError(f"sample events are for dev only; this run is in {environment!r}")
    events, seed = arguments.get("events"), arguments.get("seed")
    if not isinstance(events, str) or not INTEGER.fullmatch(events.strip()) or not 1 <= int(events) <= MAX_EVENTS:
        raise ValueError(f"events must be a whole number from 1 to {MAX_EVENTS}, not {events!r}")
    if not isinstance(seed, str) or not INTEGER.fullmatch(seed.strip()):
        raise ValueError(f"seed must be a whole number, not {seed!r}")
    return int(events), int(seed)


# %% [markdown]
# ## Sample events
#
# Statement templates per category, filled with random names and literal values.

# %%
PRODUCTS = ("default", "audit", "sales")
ENVIRONMENTS = (("prod", "lakehouse", "_lh"), ("uat", "lakehouse_uat", "_lhuat"), ("dev", "lakehouse_dev", "_lhdev"))
PEOPLE = ("maria-owner", "joe-developer", "ana-analyst")
SERVICES = ("srv-starrocks", "root")
TABLES = ("orders", "customers", "items", "daily_sales")
COPY_TASK = "ops_generate_sample_events"


def _statement(rng: random.Random, catalog: str, database: str) -> str:
    """One statement of a random category over the given catalog and database."""
    table, other = rng.sample(TABLES, 2)
    kind = rng.choices(("select", "join", "insert", "ddl", "refresh", "admin", "grant", "show", "set", "use"), weights=(30, 15, 10, 5, 5, 4, 3, 10, 10, 8))[0]
    note = rng.choice(("Lisbon", "Porto", "Faro", "Braga"))
    if kind == "select":
        return f"SELECT id, amount FROM {catalog}.{database}.{table} WHERE city = '{note}' AND amount > {rng.randint(10, 9999)}"  # nosec B608 - sample text, never executed
    if kind == "join":
        ids = ", ".join(str(rng.randint(1, 500)) for _ in range(rng.randint(1, 4)))
        recent = f"SELECT * FROM {catalog}.{database}.{table} WHERE day >= DATE '2026-11-{rng.randint(1, 28):02d}'"  # nosec B608 - sample text, never executed
        return f"WITH recent AS ({recent}) SELECT r.id, o.name FROM recent r JOIN {catalog}.{database}.{other} o ON r.id = o.id WHERE o.id IN ({ids})"  # nosec B608 - sample text
    if kind == "insert":
        return f"INSERT INTO {catalog}.{database}.{table} SELECT * FROM {catalog}.{database}.{other} WHERE amount > {rng.randint(1, 100)}"  # nosec B608 - sample text, never executed
    if kind == "ddl":
        return f"CREATE TABLE IF NOT EXISTS {catalog}.{database}.tmp_{rng.randint(1, 99)} (id BIGINT, note STRING)"
    if kind == "refresh":
        return f"REFRESH MATERIALIZED VIEW {database}.mv_{table}"
    if kind == "admin":
        return f"CREATE USER 'tmp_{rng.randint(1, 999)}'@'%' IDENTIFIED BY 'FakePass-{rng.randint(100000, 999999)}'"
    if kind == "grant":
        return f"GRANT SELECT ON ALL TABLES IN DATABASE {database} TO ROLE role_{database}"
    if kind == "show":
        return f"SHOW TABLES FROM {catalog}.{database}"
    if kind == "set":
        return f"SET query_timeout = {rng.choice((60, 300, 600))}"
    return f"USE {catalog}.{database}"


def generate_events(count: int, seed: int, start: datetime) -> list:
    """Deterministic staging-shaped audit rows (dicts with AuditLoader's keys), spread over the hour after ``start``."""
    rng = random.Random(seed)  # nosec B311 - synthetic test data, not security-sensitive
    events = []
    for index in range(count):
        when = start + timedelta(seconds=rng.randint(0, 3599), milliseconds=rng.randint(0, 999))
        product = rng.choice(PRODUCTS)
        environment, catalog, suffix = rng.choice(ENVIRONMENTS)
        user = rng.choices((rng.choice(PEOPLE), f"srv-{product}-writer", rng.choice(SERVICES)), weights=(75, 15, 10))[0]
        timestamp = when.strftime("%Y-%m-%d %H:%M:%S.") + f"{when.microsecond // 1000:03d}"
        client = f"10.0.{rng.randint(0, 3)}.{rng.randint(2, 250)}"
        if rng.random() < 0.10:
            events.append({"eventType": "CONNECTION", "timestamp": timestamp, "user": f"'{user}'@'%'", "clientIp": client, "feIp": "172.18.0.5", "state": "ERR" if rng.random() < 0.3 else "EOF"})
            continue
        statement = _statement(rng, catalog, product + suffix)
        slow = rng.random() < 0.05
        failed = rng.random() < 0.10
        event = {
            "queryId": f"{rng.getrandbits(128):032x}",
            "timestamp": timestamp,
            "queryType": "query",
            "clientIp": client,
            "user": f"'{user}'@'%'" if rng.random() < 0.5 else user,
            "authorizedUser": user,
            "resourceGroup": "default_wg",
            "catalog": catalog,
            "db": product + suffix,
            "state": "ERR" if failed else rng.choice(("EOF", "OK")),
            "errorCode": "1064" if failed else "",
            "queryTime": rng.randint(60_000, 300_000) if slow else rng.randint(5, 5_000),
            "scanBytes": rng.randint(0, 10**9),
            "scanRows": rng.randint(0, 10**6),
            "returnRows": rng.randint(0, 1000),
            "cpuCostNs": rng.randint(10**5, 10**10),
            "memCostBytes": rng.randint(10**4, 10**9),
            "stmtId": index,
            "isQuery": statement.startswith(("SELECT", "WITH", "SHOW")),
            "feIp": "172.18.0.5",
            "stmt": statement,
            "digest": hashlib.sha256((mask_sql(statement) or "").encode("utf-8")).hexdigest()[:32],
        }
        if rng.random() < 0.03:
            del event["queryId"]
        events.append(event)
    return events


def bronze_rows(events: list, copied_at: datetime, seed: int) -> list:
    """Bronze tuples in ``BRONZE_COLUMNS`` order; about 20% of the events appear twice, like the copy overlap."""
    rng = random.Random(seed + 1)  # nosec B311 - synthetic test data
    rows = []
    for event in events:
        when = datetime.strptime(event["timestamp"], "%Y-%m-%d %H:%M:%S.%f")
        row = (event.get("queryId"), when, json.dumps(event, sort_keys=True), copied_at, COPY_TASK)
        rows.append(row)
        if rng.random() < 0.2:
            rows.append(row[:3] + (copied_at + timedelta(hours=1), COPY_TASK))
    return rows


def bronze_table(config) -> str:
    """``<catalog_name>.<database_name>.starrocks_audit_event_fe_brz``."""
    return f"{config['catalog_name']}.{config['database_name']}.starrocks_audit_event_fe_brz"


def write_sample_events(spark, config, rows: list) -> int:
    """Append the rows to the dev bronze table; returns how many.

    Raises:
        RuntimeError: The bronze table doesn't exist yet.
    """
    table = bronze_table(config)
    if not spark.catalog.tableExists(table):
        raise RuntimeError(f"bronze table {table} not found: run deploy/deploy_bronze_table.py first")
    spark.createDataFrame(rows, ddl_columns(BRONZE_COLUMNS)).writeTo(table).append()
    return len(rows)


def run(spark, config, events: int, seed: int, now: datetime) -> dict:
    """Generate and append the sample rows for the hour before ``now``; logs and returns a summary."""
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    rows = bronze_rows(generate_events(events, seed, start), now, seed)
    record = {"table": bronze_table(config), "events": events, "seed": seed, "start": start.isoformat(), "rows_written": write_sample_events(spark, config, rows)}
    logger.info("Sample events: %s", json.dumps(record, sort_keys=True))
    return record


# %% [markdown]
# ## Execution
#
# Reads and checks the task arguments (`TASK_ARGS_VALUES` in Jupyter), then appends the
# rows. Spark is stopped at the end, also after a failure.

# %%
if __name__ == "__main__":
    # Importing this notebook from pytest must not start Spark or parse arguments.
    from notebooks.common.cli import parse_task_args

    if "ipykernel" not in sys.modules:
        from notebooks.common.job_runtime import start_spark_job

        spark, config = start_spark_job("ops_generate_sample_events")
    try:
        try:
            events, seed = check_arguments(parse_task_args(TASK_ARGS_DEFINITION, prog="ops_generate_sample_events.py"), config.environment)
        except ValueError as error:
            if "ipykernel" in sys.modules:
                raise
            print(f"ops_generate_sample_events.py: {error}", file=sys.stderr)
            raise SystemExit(2) from None
        run(spark, config, events, seed, datetime.now(timezone.utc).replace(tzinfo=None))
    finally:
        spark.stop()
