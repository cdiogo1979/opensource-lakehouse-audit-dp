# Application Design — `starrocks` subproject (StarRocks query audit)

Every audit event of the shared StarRocks instance, landed hourly in the `audit` product. Current since 2026-11-26; history: the [StarRocks Query Audit intent archive](../../archive/20261125-starrocks-query-audit/intent-summary.md). Functional design: [`functional-design/query-audit.md`](functional-design/query-audit.md).

## Flow
```text
StarRocks FE --AuditLoader (platform)--> starrocks_audit_db__.starrocks_audit_tbl__ (7 days, UTC)
  --starrocks_audit at :20, task 1 bronze/bronze_ingestion.py (watermark - 30 min, redacted)--> starrocks_audit_event_fe_brz
  --task 2 silver/silver_processing.py (availableNow stream, insert-only merge)--> starrocks_audit_event, starrocks_audit_table_access
  --views refresh at :35--> mv_starrocks_user_day, _product_day, _table_day, _slow_failed, _login_day (masked only)
```

## Components
| Component | Location | Responsibility |
|---|---|---|
| Shared logic | `notebooks/common/audit_sql.py` | Tokenizer, redaction, masking, categories, actors, scope, tables, keys, row enrichment, column contracts |
| Bronze deploy | `deploy/deploy_bronze_table.py`, `workflows/starrocks/starrocks_deploy.json` (manual) | Creates or checks bronze (Iceberg v2, Parquet, `days(event_time)`); never alters |
| Bronze ingestion | `bronze/bronze_ingestion.py`, task 1 of `workflows/starrocks/starrocks_audit.json` | Pulls the staging table through `lakehouse_starrocks.connect()` from the bronze watermark minus `lookback_minutes` (default 30); chunks of 20000; redacts; appends |
| Silver | `silver/silver_processing.py`, task 2 | Streams bronze (`availableNow`, checkpoint `<checkpoint_path>/starrocks/starrocks_audit_event`); enriches on the driver in chunks; dedups by `event_key`; insert-only MERGE into both silver tables (partitioned by `event_date`) |
| Gold | `materialized_views/starrocks/*.sql` | Five hourly views partitioned by `event_date`; never `sql_text` or `payload`; slow/failed keeps 30 days (`partition_ttl`) |
| Dev samples | `ops/ops_generate_sample_events.py` | Optional synthetic bronze rows, dev only |

## Decisions
- **Pull model** (2026-11-25 change request): this repository owns the bronze load; the platform only captures into the staging table and grants read.
- **Bronze** holds the staging row as canonical JSON (`payload`), so a StarRocks upgrade changes only the payload; re-read rows from the lookback are expected and removed by silver.
- **One stream for every environment** (one StarRocks instance): prod and uat in Airflow as `srv-audit-writer`, dev in Jupyter as the user.
- **Driver-side enrichment** (no Python UDFs: Spark's Python workers lack the repository in Jupyter).
- **Privacy**: credentials redacted in bronze and silver (StarRocks also masks `IDENTIFIED BY`); full SQL only for owners and developers; analysts see masked views only.
- **Retention**: nothing deletes bronze or silver rows.

## Verified
2026-11-26: uat (728 events = 728 keys) and production (1068 = 1068) loads; five views active in both; analysts limited to the views.
