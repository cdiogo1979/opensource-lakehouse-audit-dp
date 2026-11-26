CREATE MATERIALIZED VIEW ${mv_database}.mv_starrocks_user_day
COMMENT 'StarRocks queries per day, user, actor type and environment (metadata and session statements left out): queries, failed queries, scanned rows and bytes, total and longest query time. No SQL text.'
PARTITION BY event_date
REFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)
AS
SELECT
    event_date,
    `user`,
    actor_type,
    environment,
    COUNT(*) AS queries,
    SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS failed_queries,
    SUM(scan_rows) AS scan_rows,
    SUM(scan_bytes) AS scan_bytes,
    SUM(query_time_ms) AS query_time_ms,
    MAX(query_time_ms) AS max_query_time_ms
FROM ${catalog}.${namespace}.starrocks_audit_event
WHERE event_type = 'query'
  AND statement_category NOT IN ('METADATA', 'SESSION')
GROUP BY event_date, `user`, actor_type, environment
