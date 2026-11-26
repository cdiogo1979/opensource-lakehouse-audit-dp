CREATE MATERIALIZED VIEW ${mv_database}.mv_starrocks_slow_failed
COMMENT 'StarRocks queries of the last 30 days that failed or ran 60 seconds or more, one row each, with the masked SQL text (literals as ?) and digest only.'
PARTITION BY event_date
REFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)
PROPERTIES ("partition_ttl" = "30 DAY")
AS
SELECT
    event_date,
    event_time,
    event_key AS query_id,
    `user`,
    actor_type,
    environment,
    data_product,
    statement_category,
    state,
    error_code,
    failed,
    query_time_ms,
    scan_rows,
    scan_bytes,
    digest,
    sql_masked
FROM ${catalog}.${namespace}.starrocks_audit_event
WHERE event_type = 'query'
  AND (failed OR query_time_ms >= 60000)
