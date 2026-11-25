# Integration Test Instructions — StarRocks Query Audit

## 1. Spark smoke test (no platform services) — RUN, PASS
Real Spark 3.5.9 and Iceberg 1.10.2 in the platform image, a local Hadoop catalog whose table locations equal the notebooks' `<storage_path>/<catalog>/<database>/<table>`, a fake StarRocks connection with the sample generator's rows, no network:
```bash
mkdir -p /tmp/starrocks-smoke
docker run --rm --network none --hostname localhost -e SPARK_LOCAL_IP=127.0.0.1 -e SPARK_LOCAL_HOSTNAME=localhost \
    -v "$PWD":/repo:ro -v /tmp/starrocks-smoke:/work --entrypoint /opt/spark/bin/spark-submit \
    local/lakehouse-spark-runtime:3.5.9_1.10.2 --master 'local[2]' /repo/scripts/spark_smoke_starrocks.py
```
It covers: the bronze DDL and contract check (created, then exists), the bronze pull from an empty and a non-empty bronze (watermark minus 30 minutes), `createDataFrame` with the DDL schemas and `writeTo().append()`, silver's `availableNow` stream with a checkpoint and the insert-only MERGE into both silver tables, a run with nothing new, a run with duplicates only, redaction end to end, and the sample writer.

## 2. Python 3.10 check — RUN, PASS
In the same image (Python 3.10.12): every notebook parses as 3.10, every `starrocks` notebook imports, `staging_to_bronze` redacts.

## 3. Platform integration — BLOCKED by the paired platform intent
Needs, in `opensource-lakehouse-infra`: `data-products/audit.yaml` applied (analysts `none` on the prod namespace, see FR-6.2), the AuditLoader plugin and 7-day staging table, `SELECT` on it for `srv-audit-writer` and the audit owner and developer roles, `lakehouse_starrocks.connect()` in the workload Python set, and StarRocks reachable with its CA from Spark runs and Jupyter. Then, in order:
1. **Dev (Jupyter, as an audit developer)**: run `deploy/deploy_bronze_table.py`, `bronze/bronze_ingestion.py` (enter your password when asked), `silver/silver_processing.py`. Check: bronze rows match `SELECT count(*) FROM starrocks_audit_db__.starrocks_audit_tbl__` for the same window; silver has one row per `queryId`; `sql_text` holds no password of a test `CREATE USER ... IDENTIFIED BY` run before; connection events present (probe P-1) or `mv_starrocks_login_day` documented as empty.
2. **Dev views**: create the five views by hand in `audit_lhdev` from `materialized_views/starrocks/` (placeholders replaced with `lakehouse_dev`, `audit_lhdev`, `audit_lhdev`); check that `PARTITION BY event_date` on the Iceberg table and `partition_ttl = 30 DAY` are accepted (BR-42 fallbacks otherwise), refresh, query each.
3. **Access**: as an analyst, reading `lakehouse.audit_lh.starrocks_audit_event` is refused and `audit_lh.mv_starrocks_slow_failed` is readable; as a developer, both are readable.
4. **UAT**: merge to `uat`; run `audit-uat-starrocks_deploy`, then `audit-uat-starrocks_audit`; check the counts, the views deployed by the platform, and a second hourly run adding only new events.
