CREATE MATERIALIZED VIEW ${mv_database}.mv_starrocks_product_day
COMMENT 'StarRocks queries per day, data product, environment and statement category: queries, failed queries, distinct users, scanned bytes and query time. Events with no known data product are left out. No SQL text.'
PARTITION BY event_date
REFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)
AS
SELECT
    event_date,
    data_product,
    environment,
    statement_category,
    COUNT(*) AS queries,
    SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS failed_queries,
    COUNT(DISTINCT `user`) AS users,
    SUM(scan_bytes) AS scan_bytes,
    SUM(query_time_ms) AS query_time_ms
FROM ${catalog}.${namespace}.starrocks_audit_event
WHERE event_type = 'query'
  AND data_product IS NOT NULL
GROUP BY event_date, data_product, environment, statement_category
