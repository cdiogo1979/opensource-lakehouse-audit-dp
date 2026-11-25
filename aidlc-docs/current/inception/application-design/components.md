# Components — StarRocks Query Audit

Subproject `starrocks` of the `audit` data product, plus the platform-side components the paired intent builds. Decisions: [application-design-plan.md](../plans/application-design-plan.md) (Q1:B, Q2:B, Q3:A, Q4:A, Q5:A, Q6:A, Q7:A).

## In this repository

### C1 `audit_sql` — shared audit logic
- **Where**: `notebooks/common/audit_sql.py` (shared code, reusable by later audit sources).
- **Purpose**: pure Python functions over SQL text and identities, with no Spark dependency, so they can be tested directly and wrapped as Spark UDFs.
- **Responsibilities**: tokenizing SQL (quote- and comment-aware); masking literals; categorizing statements; classifying actors; inferring data product and environment; extracting referenced tables; computing the event key.
- **Interface**: plain functions (see [component-methods.md](component-methods.md)); no I/O, no configuration.

### C6 Bronze ingestion (pull model, 2026-11-25)
- **Where**: `notebooks/workload/starrocks/bronze/bronze_ingestion.py`; first task (`starrocks_audit_event_fe_brz_load`) of `workflows/starrocks/starrocks_audit.json`.
- **Purpose**: pulls new staging rows (from the bronze watermark minus `lookback_minutes`, default 30; all rows on the first run) through `lakehouse_starrocks.connect()`, redacts the statement, and appends them to bronze in chunks of 20000.
- **Responsibilities**: the only writer of bronze in every environment (prod, uat, dev); never deletes; fails clearly when bronze is missing.

### C2 Bronze table deployment
- **Where**: `notebooks/workload/starrocks/deploy/deploy_bronze_table.py`; workflow `workflows/starrocks/starrocks_deploy.json` (manual, `schedule: null`).
- **Purpose**: creates the bronze table `starrocks_audit_event_fe_brz` that C6 writes to. It is idempotent: it creates the table if it is missing and checks the schema if it exists.
- **Responsibilities**: own the bronze contract (columns, Parquet format, partitioning, location `<storage_path>/<catalog_name>/<database_name>/starrocks_audit_event_fe_brz`); fail on a schema mismatch instead of altering the table.

### C3 Silver processing
- **Where**: `notebooks/workload/starrocks/silver/silver_processing.py`; workflow `workflows/starrocks/starrocks_audit.json` (hourly).
- **Purpose**: reads new bronze rows incrementally (Structured Streaming, `availableNow`, checkpoint `<checkpoint_path>/starrocks/starrocks_audit_event`), parses the JSON payload, normalizes and enriches it with C1, deduplicates, and merges into silver.
- **Responsibilities**: own the silver tables `starrocks_audit_event` (one row per audit event; queries and connections, `event_type` column) and `starrocks_audit_table_access` (one row per event and referenced table; feeds per-table usage); never delete rows (FR-7).

### C4 Gold views
- **Where**: `materialized_views/starrocks/*.sql`, deployed by the platform into the MV database (`audit_lh`, `audit_lhuat`; by hand in `audit_lhdev`).
- **Purpose**: curated, masked aggregates and details for every reader, including analysts, who have no namespace access in production.
- **Views**: `mv_starrocks_user_day`, `mv_starrocks_product_day`, `mv_starrocks_table_day`, `mv_starrocks_slow_failed`, `mv_starrocks_login_day`.
- **Rule**: no view selects `sql_text` or `payload`; detail exposes only `sql_masked` and `digest`.

### C5 Dev sample events
- **Where**: `notebooks/workload/starrocks/ops/ops_generate_sample_events.py` (run in Jupyter as a developer; no workflow).
- **Purpose**: optional in dev (pull model, 2026-11-25): fills the dev bronze table with synthetic, realistic audit rows (queries of every category, connections, failures, platform users, unredacted fake passwords) when StarRocks isn't available or to exercise edge cases.
- **Rule**: refuses to run in any environment other than `dev`.

### C6 Template cleanup (Unit 1 `repo-identity`)
- Not a runtime component: removal of `workflows/sample/`, `workflows/tpch/`, stale tests and `duckdb`, and renaming to `opensource-lakehouse-audit-dp`. Listed for completeness.

## In the platform (paired intent in `opensource-lakehouse-infra`)

### P1 Product file `data-products/audit.yaml`
- Groups `role-audit-owner`, `-developer`, `-analyst`, `-writer`; analysts `none` on the production namespace and `read` on the production MV database (FR-6.2); `repo` = this repository (`main`, `uat`).

### P2 Capture: AuditLoader plugin and staging table
- `starrocks_audit_db__.starrocks_audit_tbl__`, dynamic daily partitions, 7-day time-to-live (FR-1.3).

### P3 Access for the bronze task (pull model, 2026-11-25)
- `SELECT` on the staging table for `srv-audit-writer` and the audit owner and developer roles.
- `lakehouse_starrocks.connect()` in the workload Python set (beside `lakehouse_spark`): a DB-API connection to StarRocks over TLS as the product writer with the run's token in Airflow, or as the signed-in user with their LDAP password (asked with `getpass`, never stored) in Jupyter; host, port and CA from the platform.
- Network access from Spark run containers and Jupyter servers to `starrocks-fe:9030`.
