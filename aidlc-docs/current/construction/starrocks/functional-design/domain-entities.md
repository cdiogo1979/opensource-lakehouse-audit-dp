# Domain Entities — Unit 2 `starrocks` (component `query-audit`)

All tables live in the environment's namespace (`audit_lh`, `audit_lhuat`, `audit_lhdev`) at `<storage_path>/<catalog_name>/<database_name>/<table>`. Gold views live in the MV database of the same name. Rules referenced as `BR-n`: [business-rules.md](business-rules.md).

## Relationships

```text
staging row (StarRocks, platform) --bronze task, 1..2 reads--> AuditEventRaw (bronze)
AuditEventRaw (n duplicates) --event_key--> AuditEvent (silver, 1)
AuditEvent (1) --event_key--> TableAccess (silver, 0..n)
AuditEvent / TableAccess --aggregate or filter--> gold views
```

## AuditEventRaw — bronze `starrocks_audit_event_fe_brz`
Written by the bronze task (AD-1, AD-10). Iceberg format-version 2, `write.format.default = parquet`, partitioned by `days(event_time)`, append-only.

| Column | Type | Null | Meaning |
|---|---|---|---|
| `query_id` | string | yes | Staging `queryId` |
| `event_time` | timestamp | no | Staging `timestamp`, UTC |
| `payload` | string | no | Canonical JSON object of every staging column, keys as in the staging table; statement credentials redacted by the bronze task (BR-20, BR-23) |
| `copied_at` | timestamp | no | When the bronze task read the row (UTC) |
| `copy_task` | string | no | Writer name: `bronze_ingestion`, or `ops_generate_sample_events` for synthetic dev rows |

No key: the same staging row appears once or twice (the bronze task's lookback, BR-30).

## AuditEvent — silver `starrocks_audit_event`
One row per audit event. Partitioned by `event_date` (identity; BR-41). Insert-only (BR-32).

| Column | Type | Null | Source / rule |
|---|---|---|---|
| `event_key` | string | no | BR-31 |
| `event_type` | string | no | `query`, `connection`, `unparsed` (BR-12, BR-14) |
| `event_time` | timestamp | no | payload `timestamp`; bronze `event_time` when absent |
| `event_date` | date | no | `DATE(event_time)` (UTC) |
| `user` | string | yes | payload `user`, normalized (BR-6) |
| `authorized_user` | string | yes | payload `authorizedUser`, normalized |
| `actor_type` | string | no | `person`, `product_writer`, `platform_service`, `unknown` (BR-6) |
| `client_ip` | string | yes | payload `clientIp` |
| `fe_host` | string | yes | payload `feIp` |
| `catalog` | string | yes | payload `catalog` (session catalog) |
| `database` | string | yes | payload `db` (session database) |
| `data_product` | string | yes | BR-8 |
| `environment` | string | yes | `prod`, `uat`, `dev`, `multiple`, or null (BR-8) |
| `statement_category` | string | yes | BR-5; null for non-query events |
| `query_type` | string | yes | payload `queryType` |
| `is_query` | boolean | yes | payload `isQuery` |
| `state` | string | yes | payload `state` (`EOF`, `OK`, `ERR`, ...) |
| `failed` | boolean | no | BR-13 |
| `error_code` | string | yes | payload `errorCode` |
| `query_time_ms` | bigint | yes | payload `queryTime` |
| `scan_rows` | bigint | yes | payload `scanRows` |
| `scan_bytes` | bigint | yes | payload `scanBytes` |
| `return_rows` | bigint | yes | payload `returnRows` |
| `cpu_cost_ns` | bigint | yes | payload `cpuCostNs` |
| `mem_cost_bytes` | bigint | yes | payload `memCostBytes` |
| `digest` | string | yes | payload `digest` |
| `sql_masked` | string | yes | BR-1 to BR-4 over the redacted text |
| `sql_text` | string | yes | payload `stmt`, credentials redacted (BR-20, BR-21) |
| `payload` | string | no | The bronze payload, credentials redacted, canonical JSON (BR-22) |
| `copy_task` | string | no | From bronze (first copy) |
| `first_copied_at` | timestamp | no | Earliest `copied_at` among the duplicates seen in the micro-batch |
| `loaded_at` | timestamp | no | When silver inserted the row |

## TableAccess — silver `starrocks_audit_table_access`
One row per event and distinct referenced table. Partitioned by `event_date`. Insert-only. Key (`event_key`, `ref_catalog`, `ref_database`, `ref_table`).

| Column | Type | Null | Source / rule |
|---|---|---|---|
| `event_key` | string | no | Parent event |
| `event_time`, `event_date` | timestamp, date | no | Parent event |
| `user`, `actor_type` | string | yes / no | Parent event |
| `statement_category` | string | yes | Parent event |
| `ref_catalog` | string | no | BR-9 (lowercase) |
| `ref_database` | string | yes | BR-9 (null when unknown) |
| `ref_table` | string | no | BR-9 (lowercase) |
| `data_product`, `environment` | string | yes | `infer_scope(ref_catalog, ref_database)` (BR-7) |

## Gold views (MV database; masked only, BR-40)

| View | Grain | Measures | Source filter |
|---|---|---|---|
| `mv_starrocks_user_day` | `event_date`, `user`, `actor_type`, `environment` | `queries`, `failed_queries`, `scan_rows`, `scan_bytes`, `query_time_ms`, `max_query_time_ms` | `event_type = 'query'` and category not `METADATA`/`SESSION` |
| `mv_starrocks_product_day` | `event_date`, `data_product`, `environment`, `statement_category` | `queries`, `failed_queries`, `users` (distinct), `scan_bytes`, `query_time_ms` | `event_type = 'query'`, `data_product` not null |
| `mv_starrocks_table_day` | `event_date`, `ref_catalog`, `ref_database`, `ref_table`, `data_product`, `environment` | `accesses`, `users` (distinct) | table access rows |
| `mv_starrocks_slow_failed` | one row per event | `event_time`, `query_id` (= `event_key`), `user`, `actor_type`, `environment`, `data_product`, `statement_category`, `state`, `error_code`, `query_time_ms`, `scan_rows`, `scan_bytes`, `digest`, `sql_masked` | `event_type = 'query'` and (`failed` or `query_time_ms >= 60000`); last 30 days (BR-42) |
| `mv_starrocks_login_day` | `event_date`, `user`, `actor_type`, `client_ip` | `logins`, `failed_logins` | `event_type = 'connection'` |

## Dev sample event (C5)
A Python dict shaped like a staging row (camelCase keys as AuditLoader writes them), wrapped into an `AuditEventRaw` with `copy_task = 'ops_generate_sample_events'`.
