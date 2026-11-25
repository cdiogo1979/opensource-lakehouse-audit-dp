CREATE MATERIALIZED VIEW ${mv_database}.mv_starrocks_login_day
COMMENT 'StarRocks connections per day, user, actor type and client address: successful and failed logins. Empty if the audit log records no connection events.'
PARTITION BY event_date
REFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)
AS
SELECT
    event_date,
    `user`,
    actor_type,
    client_ip,
    SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS logins,
    SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS failed_logins
FROM ${catalog}.${namespace}.starrocks_audit_event
WHERE event_type = 'connection'
GROUP BY event_date, `user`, actor_type, client_ip
