# Deployment instructions: StarRocks query audit

Intent `20261125-starrocks-query-audit`. Steps in execution order. **Deployment is conditional on the paired platform intent (steps 1 to 4) and on the dev validation (step 6).** Nothing here has been executed. Approving these instructions doesn't authorize running them.

Manual steps: 1 to 7, 9, 10, 12, 13. Automated once merged: Airflow bundle refresh (about 5 minutes), the platform's view deploy at the start of every bundle run, and the hourly `starrocks_audit` schedule.

## Platform prerequisites (repository `opensource-lakehouse-infra`, paired intent)

### 1. Apply the audit data product — APPLIED 2026-11-25T18:29Z, RE-APPLY NEEDED
**Status**: applied (`.runtime/state/data-products/audit.json`, `applied_at` 2026-11-25T18:29:16Z) from the file as it was then, with `access.analyst.prod: read`. The file was changed at 18:32 to `none` (FR-6.2), so analysts can currently read `lakehouse.audit_lh`, including full SQL text once data arrives. **Re-apply before any prod data is loaded** (step 12): `scripts/operations/create-data-product.sh data-products/audit.yaml --dry-run`, then without `--dry-run` (needs the platform admin password). The `repo` block is unchanged, so no Airflow restart is expected; confirm in the dry run. A dry-run validation without the password reported "file valid (re-apply)".

- **Action and target**: apply `data-products/audit.yaml`: groups `role-audit-*`, namespaces `audit_lh`/`audit_lhuat`/`audit_lhdev`, buckets, MV databases, writer `srv-audit-writer`, Airflow bundles `audit-main`/`audit-uat`, Jenkins folder `audit`.
- **Prerequisites**: platform admin. Check that the file keeps `access.analyst.prod: none` (FR-6.2). Applying a product with a `repo` block recreates the Airflow containers, so pick a time with no running DAG runs, or use `--no-restart` and later `scripts/lakehouse.sh start-airflow`.
- **Execution**: `scripts/operations/create-data-product.sh data-products/audit.yaml` (add `--no-restart` to defer the Airflow restart), then commit the file in the platform repository.
- **Validation**: the groups exist in Keycloak; the namespaces exist in the three Polaris catalogs; `SHOW DATABASES` in StarRocks lists the MV databases; Airflow lists the `audit-main`/`audit-uat` bundles.
- **Failure handling**: stop; follow the platform's data-product docs. Nothing in this repository depends on a partial apply.

### 2. Install AuditLoader and the staging table
- **Action and target**: the StarRocks FE, database `starrocks_audit_db__`, table `starrocks_audit_tbl__` with dynamic daily partitions and a **7-day** time-to-live.
- **Prerequisites**: step 1 isn't required. Use the plugin version matching StarRocks 4.1.7.
- **Validation**: rows appear in the table within a minute of a query. Record the **column list**, whether **connection events** are present, and the **FE time zone** (probe P-1). If the FE isn't on UTC, the bronze task's time handling needs a change: stop and reopen the design.
- **Failure handling**: uninstall the plugin; nothing downstream has run yet.

### 3. Grants and connectivity
- **Action and target**: `SELECT` on `starrocks_audit_db__.starrocks_audit_tbl__` for `srv-audit-writer` and for the StarRocks roles of the audit owner and developer groups (dev pulls as the signed-in user). StarRocks `starrocks-fe:9030` must be reachable, with its CA, from Spark run containers and Jupyter servers.
- **Validation**: as an audit developer in StarRocks, `SELECT count(*) FROM starrocks_audit_db__.starrocks_audit_tbl__` works; as an analyst it's refused.
- **Failure handling**: revoke the grants.

### 4. `lakehouse_starrocks.connect()`
- **Action and target**: a module in the workload Python set next to `lakehouse_spark`, present in the Spark runtime and Jupyter images. It returns a DB-API connection over TLS:
  - in Airflow runs, as the product writer with the run's token (as `mv_deploy.connect` does);
  - in Jupyter, as the signed-in user with their LDAP password, asked with `getpass` and never stored.
- **Validation**: `from lakehouse_starrocks import connect; connect().cursor().execute("SELECT 1")` works in a DAG run of the audit product and in Jupyter.
- **Failure handling**: no fallback in this repository. The bronze task fails clearly, and silver processes nothing new.

## This repository

### 5. Push the branch and open the pull request
- **Action and target**: push `starrocks-query-audit`; open a pull request into `uat` with `pr_request.md` as the description.
- **Prerequisites**: your go-ahead. Pushing and opening the PR are outward-facing actions that need it.
- **Validation**: Jenkins `audit/audit_cd` builds the branch green: compile, workflow and view validation, code quality, 260 tests. It scans every 30 minutes.

### 6. Validate in dev (Jupyter), before merging
- **Prerequisites**: steps 1 to 4; signed in to JupyterHub as an audit developer or owner; this repository cloned at `~/work/opensource-lakehouse-audit-dp` on the branch.
- **Execution**, in order:
  1. `notebooks/workload/starrocks/deploy/deploy_bronze_table.py` (no arguments);
  2. `notebooks/workload/starrocks/bronze/bronze_ingestion.py` with `TASK_ARGS_VALUES = {"lookback_minutes": "30"}`, entering your password when asked;
  3. `notebooks/workload/starrocks/silver/silver_processing.py`;
  4. create the five views by hand in `audit_lhdev` from `materialized_views/starrocks/`, with `${mv_database}` = `audit_lhdev`, `${catalog}` = `lakehouse_dev` and `${namespace}` = `audit_lhdev`. Then refresh them.
- **Validation**:
  - bronze row count roughly equals the staging rows for the same window;
  - silver has one row per `queryId`;
  - run a test `CREATE USER tmp_x IDENTIFIED BY '<test value>'` beforehand, and check its password isn't in silver `sql_text` or `payload`;
  - every view is created and returns rows (`mv_starrocks_login_day` may be empty: see step 2);
  - a second bronze and silver run adds only new events.
- **Failure handling**: if a view's DDL is refused, apply BR-42's fallbacks (for the slow/failed view: a `current_date()` filter, then all history) as a code change on the branch. For data problems, an audit owner drops the dev silver tables, deletes the checkpoint `s3ckpt://bucket-audit-dev/checkpoints/starrocks/starrocks_audit_event`, and reruns silver (rebuilt from bronze).

### 7. Merge into `uat`
- **Execution**: merge the pull request after step 6 passes.
- **Validation**: within about 5 minutes, Airflow shows `audit-uat-starrocks_deploy` and `audit-uat-starrocks_audit`. Check whether new DAGs start paused on this Airflow, and unpause `audit-uat-starrocks_audit` only after step 9 (input needed: the pause default).

### 8. (automatic) View deploy in UAT
Every run of an `audit-uat` DAG starts with the platform's `deploy_materialized_views` task, which creates the five views in `audit_lhuat` as the writer. A view whose source table doesn't exist yet is skipped until a later run, so the views appear after the first silver run.

### 9. Create the UAT bronze table — DONE 2026-11-25 (run `manual__2026-11-25T18:54:34`, result `created`)
- **Execution**: trigger `audit-uat-starrocks_deploy`.
- **Validation**: the task logs `"result": "created"`, and the table exists in `lakehouse_uat.audit_lhuat`.

### 10. First UAT load
- **Execution**: trigger `audit-uat-starrocks_audit` once, then leave it scheduled (`20 * * * *`).
- **Validation**:
  - the bronze summary has `"watermark": null` and every staging row (up to 7 days); the silver summary has `unparsed: 0`;
  - a second run logs a `since` 30 minutes before the previous watermark;
  - in StarRocks, the views in `audit_lhuat` return data after their next refresh (minute 35);
  - as an analyst, `lakehouse_uat.audit_lhuat.starrocks_audit_event` is refused;
  - run time stays well under the hour.
- **Failure handling**: pause the DAG. The bronze task can be rerun safely (lookback). For silver, see step 6's failure handling, with the UAT checkpoint.

### 11. Promote to `main`
- **Execution**: pull request from `uat` into `main`, after UAT has run hourly without errors for at least a day (suggested).

### 12. Production
- **Execution**: trigger `audit-main-starrocks_deploy`, then `audit-main-starrocks_audit`; leave it scheduled.
- **Validation**:
  - as in step 10, on `lakehouse.audit_lh`;
  - as an analyst: the namespace is refused, while `audit_lh.mv_starrocks_slow_failed` and the other views are readable;
  - as a developer, both are readable.

### 13. After go-live
- Watch the hourly summaries (bronze `rows`, silver `rows_read`, `unparsed`) for a week. Record the hourly event volume in the performance instructions.
- Rollback: pause `audit-main-starrocks_audit`. To remove the feature, delete `workflows/starrocks/` and `materialized_views/starrocks/` through a pull request; the platform drops the views. Tables stay until an owner drops them. **No row-level rollback is designed**: the tables are append-only by requirement (FR-7.1).

## Inputs needed and blockers
| Item | Owner | Status |
|---|---|---|
| Paired platform intent: steps 2 to 4 (step 1 applied, re-apply pending) | platform admin | Not started (blocker) |
| Probe P-1: AuditLoader columns, connection events, FE time zone | platform admin | Open |
| Airflow's pause default for new DAGs | platform admin | Unknown |
| Step 5: branch pushed; PR #1 merged into `uat` 2026-11-25 (`3935df5`), before dev validation (step 6, still to do before `main`) | you | Done |
| Re-apply of `audit.yaml` (analysts `none` on prod) | platform admin | Done 2026-11-25 (user) |

## Execution record (2026-11-26)
- Steps 1 to 4 (platform): done by the paired intent `20261125-starrocks-audit-capture` (closed); `audit.yaml` re-applied with analysts `none`.
- Steps 5, 7, 9, 10: PRs #1 and #2 merged into `uat`; `audit-uat-starrocks_deploy` created bronze; `audit-uat-starrocks_audit` loads hourly (verified).
- Step 6 (dev validation): Jupyter connection verified by the user; the dev notebooks themselves not run (same code verified in uat and production).
- Steps 11, 12: PR #3 merged into `main`; `audit-main-starrocks_deploy` created bronze; `audit-main-starrocks_audit` succeeded (1068 events); analyst access limited to the views.
