CREATE MATERIALIZED VIEW ${mv_database}.mv_starrocks_table_day
COMMENT 'Tables named by StarRocks statements, per day: accesses and distinct users per catalog, database and table, with the data product and environment the database belongs to. Best effort: tables are found in the SQL text. No SQL text.'
PARTITION BY event_date
REFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)
AS
SELECT
    event_date,
    ref_catalog,
    ref_database,
    ref_table,
    data_product,
    environment,
    COUNT(*) AS accesses,
    COUNT(DISTINCT `user`) AS users
FROM ${catalog}.${namespace}.starrocks_audit_table_access
GROUP BY event_date, ref_catalog, ref_database, ref_table, data_product, environment
