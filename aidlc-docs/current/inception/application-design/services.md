# Services and Orchestration — StarRocks Query Audit

There are no long-running services in this repository. Work is orchestrated by StarRocks (capture into the staging table), Airflow (bronze pull, then silver), and the StarRocks MV scheduler (gold).

## Hourly timeline (production and UAT)

| Minute | Scheduler | Step | Component |
|---|---|---|---|
| continuous | StarRocks FE | AuditLoader batches audit events into the staging table (about once a minute) | P2 |
| hh:20 | Airflow (`audit-main-starrocks_audit`, `audit-uat-starrocks_audit`, cron `20 * * * *`) | `submit_starrocks_audit_event_fe_brz_load`: pull staging rows from the bronze watermark minus 30 minutes into bronze | C6 |
| after it | same DAG | `submit_starrocks_audit_event_load`: stream new bronze snapshots, merge into silver | C3 |
| hh:35 | StarRocks MV scheduler (`REFRESH ASYNC START(... 00:35:00) EVERY (INTERVAL 1 HOUR)`) | Refresh the gold views | C4 |

Freshness: an event reaches silver within about 20 to 85 minutes, and gold within about 35 to 95 minutes (NFR-1). A failed or late step is caught up by the next run: the bronze task resumes from the bronze watermark (gaps up to the staging table's 7 days), the stream resumes from its checkpoint, and the views refresh fully from silver. (Pull model, 2026-11-25: the hh:05 platform copy task is gone.)

## Workflows (this repository)

| Workflow | Schedule | Tasks | Notes |
|---|---|---|---|
| `starrocks_audit` | `20 * * * *` | `starrocks_audit_event_fe_brz_load` (`bronze/bronze_ingestion.py`, `retries: 1`, `retry_delay_seconds: 300`, `execution_timeout_seconds: 1800`), then `starrocks_audit_event_load` (`silver/silver_processing.py`, `runs_after` the bronze task, `retries: 1`, `retry_delay_seconds: 300`, `execution_timeout_seconds: 2400`) | Two Spark jobs, one after the other (NFR-3) |
| `starrocks_deploy` | `null` (manual) | `starrocks_audit_event_fe_brz_create` (`deploy/deploy_bronze_table.py`) | Run once per environment before the first `starrocks_audit` run; safe to re-run |

DAG IDs come from the platform: `audit-main-<name>` (prod) and `audit-uat-<name>` (uat).

## Environment behavior

| Environment | Bronze filled by | Silver and gold |
|---|---|---|
| prod (`audit_lh`) | C6 in Airflow, as the writer (real rows) | Airflow `audit-main-*`; views deployed by the platform |
| uat (`audit_lhuat`) | C6 in Airflow, as the writer (real rows) | Airflow `audit-uat-*`; views deployed by the platform |
| dev (`audit_lhdev`) | C6 in Jupyter, as the user (real rows; same StarRocks instance); C5 optionally | Notebooks run by hand in Jupyter; views created by hand in the dev MV database |

## Ordering at first deployment (detailed in Deploy)
1. Platform: `audit.yaml` applied (groups, namespaces, buckets, MV databases, bundles).
2. This repository: `starrocks_deploy` run in uat and prod (bronze table exists).
3. Platform: plugin and staging table, `SELECT` grants, `lakehouse_starrocks` helper.
4. This repository: `starrocks_audit` unpaused; views promoted.
