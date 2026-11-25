# Services and Orchestration — StarRocks Query Audit

There are no long-running services in this repository. Work is orchestrated by three schedulers: StarRocks (capture and copy), Airflow (silver), and the StarRocks MV scheduler (gold).

## Hourly timeline (production and UAT)

| Minute | Scheduler | Step | Component |
|---|---|---|---|
| continuous | StarRocks FE | AuditLoader batches audit events into the staging table (about once a minute) | P2 |
| hh:05 | StarRocks task | Copy staging rows with `timestamp` in the last 2 hours into the environment's bronze table | P3 |
| hh:20 | Airflow (`audit-main-starrocks_audit`, `audit-uat-starrocks_audit`, cron `20 * * * *`) | `submit_starrocks_audit_event_load`: stream new bronze snapshots, merge into silver | C3 |
| hh:35 | StarRocks MV scheduler (`REFRESH ASYNC START(... 00:35:00) EVERY (INTERVAL 1 HOUR)`) | Refresh the gold views | C4 |

Freshness: an event reaches silver within about 20 to 80 minutes, and gold within about 35 to 95 minutes (NFR-1). A failed or late step is caught up by the next run: the copy overlaps 2 hours, the stream resumes from its checkpoint, and the views refresh fully from silver.

## Workflows (this repository)

| Workflow | Schedule | Tasks | Notes |
|---|---|---|---|
| `starrocks_audit` | `20 * * * *` | `starrocks_audit_event_load` (`silver/silver_processing.py`), `retries: 1`, `retry_delay_seconds: 300`, `execution_timeout_seconds: 2400` | One Spark job per run (NFR-3); it writes both silver tables in one `foreachBatch` |
| `starrocks_deploy` | `null` (manual) | `starrocks_audit_event_fe_brz_create` (`deploy/deploy_bronze_table.py`) | Run once per environment before the platform's copy task is enabled; safe to re-run |

DAG IDs come from the platform: `audit-main-<name>` (prod) and `audit-uat-<name>` (uat).

## Environment behavior

| Environment | Bronze filled by | Silver and gold |
|---|---|---|
| prod (`audit_lh`) | Platform copy task (real rows) | Airflow `audit-main-*`; views deployed by the platform |
| uat (`audit_lhuat`) | Platform copy task (real rows) | Airflow `audit-uat-*`; views deployed by the platform |
| dev (`audit_lhdev`) | C5 in Jupyter (synthetic rows) | Notebooks run by hand in Jupyter; views created by hand in the dev MV database |

## Ordering at first deployment (detailed in Deploy)
1. Platform: `audit.yaml` applied (groups, namespaces, buckets, MV databases, bundles).
2. This repository: `starrocks_deploy` run in uat and prod (bronze table exists).
3. Platform: plugin, staging table and copy tasks enabled.
4. This repository: `starrocks_audit` unpaused; views promoted.
