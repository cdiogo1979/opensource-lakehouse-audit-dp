# Functional Design — `query-audit` (StarRocks audit: bronze, silver, gold)

Consolidated current design of the `starrocks` subproject's audit pipeline: rules, entities, algorithms, failure behavior and tested properties. Architecture: [`../application-design.md`](../application-design.md). History: the [StarRocks Query Audit intent archive](../../../archive/20261125-starrocks-query-audit/intent-summary.md).

# Rules

## Masking (`mask_sql`)
- **BR-1** Literals become `?`: single- and double-quoted strings (StarRocks treats `"..."` as a string), integers, decimals, exponents, and hex (`0x..`, `X'..'`). Identifiers, including backtick-quoted ones and names containing digits (`t1`, `q4_2026`), are kept. Typed literals keep their keyword (`DATE '2026-01-01'` becomes `DATE ?`).
- **BR-2** `IN (...)` lists made only of literals, commas and whitespace collapse to `IN (?)`. An `IN (SELECT ...)` is masked normally inside, not collapsed.
- **BR-3** Comments are removed (`-- ...` to end of line, `# ...` to end of line, `/* ... */`). Hints `/*+ ... */` become `/*+ ? */` (FD-Q3).
- **BR-4** Whitespace runs collapse to one space; the result is trimmed. Keywords keep their original case. An unterminated string or comment is masked to the end of the text. `None` stays `None`; an empty or whitespace-only text becomes `''`.

## Categories (`statement_category`)
- **BR-5** The first word after leading comments, whitespace, parentheses and any `WITH ... AS (...)` prefix decides the category (case-insensitive):

| Category | First word(s) |
|---|---|
| `SELECT` | `SELECT`, `WITH ... SELECT`, `VALUES`, `TABLE` |
| `DML` | `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `REPLACE`, `LOAD`, `TRUNCATE`, `EXPORT` |
| `DDL` | `CREATE`, `ALTER`, `DROP`, `RENAME`, `REFRESH`, `CANCEL REFRESH`, `ANALYZE`, `SUBMIT TASK`, `RECOVER` |
| `ADMIN` | `GRANT`, `REVOKE`, `CREATE`/`ALTER`/`DROP` followed by `USER` or `ROLE`, `SET PASSWORD`, `SET ROLE`, `SET DEFAULT ROLE`, `ALTER SYSTEM`, `ADMIN`, `KILL`, `INSTALL`/`UNINSTALL PLUGIN` |
| `METADATA` | `SHOW`, `DESC`, `DESCRIBE`, `EXPLAIN` |
| `SESSION` | `SET` (other than the `ADMIN` forms), `USE` |
| `OTHER` | anything else, or an empty text |

`ADMIN` forms win over `DDL` and `SESSION`. `None` gives `None`.

## Actors (`actor_type`)
- **BR-6** The user is normalized first: surrounding quotes removed, a `'name'@'host'` suffix removed, lowercased. Then:
  - empty or null → `unknown`;
  - `srv-<token>-writer` → `product_writer`;
  - `root`, `admin`, any other `srv-*` name (for example `srv-starrocks`), and `%` (internal) → `platform_service`;
  - otherwise → `person`.

## Scope (`infer_scope`, event attribution)
- **BR-7** A database name ending in `_lh`, `_lhuat` or `_lhdev` with a non-empty prefix names product `<prefix>` in `prod`, `uat` or `dev`; this holds for namespaces in the Iceberg catalogs and for MV databases in `default_catalog`. Without such a database, the catalog alone gives the environment: `lakehouse` → `prod`, `lakehouse_uat` → `uat`, `lakehouse_dev` → `dev`, with no product. The database wins when catalog and database disagree. Names are compared lowercase.
- **BR-8** Event-level `data_product` and `environment` (FD-Q4), first match wins:
  1. the session `catalog`/`database`, if `infer_scope` gives a product;
  2. the referenced tables: if they name exactly one product, that product (and its single environment, else `multiple`);
  3. several products among the referenced tables → `data_product = 'multiple'`, `environment` the single environment or `multiple`;
  4. otherwise both null, except the environment from rule 1's catalog alone when it gave one.

## Referenced tables (`referenced_tables`)
- **BR-9** Only `SELECT`, `DML` and `DDL` statements are searched. Table references are the dotted names right after `FROM`, `JOIN`, `INTO`, `UPDATE`, `TABLE`, `OVERWRITE`, and after `MATERIALIZED VIEW`, skipping `IF [NOT] EXISTS`; after `FROM`, comma-separated tables (`FROM t1 a, t2 b`) are all read. After `FROM`/`JOIN` a name followed by `(` is a table function and is ignored (after `INTO`/`TABLE` the `(` opens a column list); a `FROM` inside a function's parentheses (`EXTRACT(YEAR FROM d)`) is not a table reference; names a `WITH` clause defines are ignored. (Refined at Code Generation, 2026-11-25.) One part is qualified with the session catalog and database, two parts with the session catalog; a missing session catalog is `default_catalog`; a missing database stays null. Results are lowercase and distinct, in order of first appearance. Masked or redacted text gives the same result as the original (literals are never table names).

## Events
- **BR-12** `event_type`: `connection` when the payload marks a login (`eventType`/`type` is `CONNECTION`, or `queryType` is `connection`); `disconnection` when it marks a logout (`DISCONNECTION` / `disconnection`; added 2026-11-26 after the paired intent's probe P-1 found AuditLoader 5.0.0 writes both); `query` otherwise (including `slow_query`); `unparsed` when the payload isn't a JSON object or has no usable `timestamp` and no `stmt` and no `queryId` (FD-Q7). The connection markers are confirmed against StarRocks 4.1.7 by the paired intent (probe P-1); until then the mapping lives in one constant.
- **BR-13** `failed` is true when `state` is `ERR` (case-insensitive), false otherwise (including null).
- **BR-14** An `unparsed` row keeps `event_key`, `event_type`, `event_time` and `event_date` from bronze, `actor_type = 'unknown'`, `failed = false`, the payload, copy metadata and `loaded_at`; other columns are null. The run logs how many rows were unparsed and succeeds.
- **BR-15** Payload keys are matched case-insensitively and with `_` ignored, so `queryId`, `query_id` and `QUERYID` all match. Numeric fields that aren't valid integers become null; they never fail the run.

## Credentials (`redact_credentials`)
- **BR-20** Credentials are replaced by `'***'` (FD-Q2): the string after `IDENTIFIED BY`, `IDENTIFIED WITH <plugin> BY|AS`, `PASSWORD(`, `SET PASSWORD ... =`, and the value of any key-value pair (`"key" = "value"` or `key = 'value'`) whose key contains `password`, `secret`, `access_key`, `access.key`, `accesskey`, `token`, `credential` or `private_key` (case-insensitive). Everything else is unchanged, including whitespace and comments.
- **BR-21** Silver's `sql_text` is the redacted `stmt`. Masking runs on the redacted text, so `sql_masked` never holds credentials either.
- **BR-22** Silver's `payload` is the bronze payload with `stmt` redacted, serialized as canonical JSON (sorted keys, no extra whitespace).
- **BR-23** The bronze task redacts the statement with BR-20 before appending (`staging_to_bronze`), so bronze never holds credentials; silver redacts again, so synthetic or older rows never reach silver or gold unredacted (pull model, 2026-11-25; supersedes the platform copy task's `regexp_replace`).

## Keys, deduplication and loading
- **BR-30** Bronze may hold a staging row twice: each run re-reads from the bronze watermark minus the lookback. The same staging row always gives the same canonical payload, so duplicates have identical payloads.
- **BR-31** `event_key` = the trimmed `queryId` when it is non-empty; otherwise `sha256:` + the SHA-256 of the canonical JSON of the payload as found in bronze (sorted keys, no extra whitespace). Duplicates therefore share a key.
- **BR-32** Silver is insert-only: a micro-batch is deduplicated by key (keeping the earliest `copied_at` as `first_copied_at`), then merged with "insert when not matched" on (`event_key`, `event_date`) for events and (`event_key`, `ref_catalog`, `ref_database`, `ref_table`, `event_date`) for table accesses. Existing rows are never updated or deleted (FR-7.1).
- **BR-33** Silver reads bronze as a stream of appended snapshots, `availableNow`, checkpoint `<checkpoint_path>/starrocks/starrocks_audit_event`. Replaying a batch (retry, lost checkpoint) inserts nothing twice, by BR-32.
- **BR-35** The bronze task (pull model, 2026-11-25) reads `SELECT *` from the staging table (config `starrocks_audit_table`, two plain identifiers) where `` `timestamp` >= `` the bronze watermark minus `lookback_minutes` (0 to 10080, default 30); with an empty bronze it reads every staging row. Rows come oldest first, in chunks of 20000, each appended to bronze; `copy_task = 'bronze_ingestion'`, `copied_at` = the run's start (UTC). The connection is the platform's `lakehouse_starrocks.connect()` and is always closed. Staging values become JSON: datetimes as `YYYY-MM-DD HH:MM:SS[.ffffff]` (UTC), decimals as numbers, bytes as UTF-8 text. Gaps longer than the staging table's 7 days can't be recovered.
- **BR-36** Every environment pulls the same real stream (one StarRocks instance): prod and uat in Airflow as the product writer, dev in Jupyter as the signed-in user.
- **BR-34** Silver requires the bronze table: if it doesn't exist, the job fails with "bronze table <name> not found: run the starrocks_deploy workflow first".

## Gold
- **BR-40** No view selects `sql_text` or `payload`. `mv_starrocks_slow_failed` exposes `sql_masked` and `digest` only.
- **BR-41** Silver is partitioned by the identity of `event_date`, so the views can be partitioned by `event_date` in line with it.
- **BR-42** Slow means `query_time_ms >= 60000` (FD-Q5). `mv_starrocks_slow_failed` keeps the last 30 days (FD-Q6) by partition time-to-live (`partition_ttl = "30 DAY"`, partitioned by `event_date`). Fallback if StarRocks refuses it in dev: filter `event_date >= date_sub(current_date(), 30)`; if that is refused too, keep all history and record the deviation.
- **BR-43** Views refresh hourly, starting at minute 35, after the hh:20 silver load.

## Environments and retention
- **BR-50** The deploy, bronze and silver notebooks run in `dev`, `uat` and `prod`. The optional sample generator runs only in `dev` and refuses others; it requires `--events` to be an integer from 1 to 100000 and `--seed` an integer.
- **BR-51** Nothing deletes rows from bronze or silver (FR-7.1). Iceberg snapshot expiry, if added later, must not remove rows.

# Entities

All tables live in the environment's namespace (`audit_lh`, `audit_lhuat`, `audit_lhdev`) at `<storage_path>/<catalog_name>/<database_name>/<table>`. Gold views live in the MV database of the same name. Rules referenced as `BR-n`: the rules above.

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
| `event_type` | string | no | `query`, `connection`, `disconnection`, `unparsed` (BR-12, BR-14) |
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

# Algorithms, failure behavior and properties

## 2. `audit_sql` algorithms (C1, pure Python)

### 2.1 Tokenizer — `tokenize(sql) -> list[Token]`
A single left-to-right scan producing tokens of kinds `space`, `line_comment`, `block_comment`, `hint`, `string`, `quoted_ident` (backticks), `number`, `word`, `punct`. Strings handle backslash escapes and doubled quotes; an unterminated string, comment or identifier runs to the end. The scan is **lossless**: concatenating the tokens' text gives back the input exactly. Every other function works on tokens, never on raw regular expressions, so quotes and comments are never misread.

### 2.2 Credential redaction — `redact_credentials(sql) -> str`
Walk the significant tokens (skipping space and comments) and replace the `string` tokens that BR-20 identifies with `'***'`, then join all tokens back. Everything else is byte-for-byte unchanged.

### 2.3 Masking — `mask_sql(sql) -> str`
1. Tokenize.
2. Drop comments; turn hints into `/*+ ? */`.
3. Replace `string` and `number` tokens with `?` (a `-` before a number stays as `-`).
4. Collapse `IN ( ? , ? , ... )` to `IN (?)` (BR-2).
5. Join with single spaces between significant tokens, following simple spacing rules (no space before `,` `)` `.` or after `(` `.`), and trim.

### 2.4 Categorization — `statement_category(sql) -> str | None`
Skip leading space, comments and `(`. If the first word is `WITH`, skip the CTE definitions (balanced parentheses) to the main statement. Match the first one or two words against BR-5's table, `ADMIN` forms first.

### 2.5 Actors — `actor_type(user) -> str`
Normalize (BR-6), then classify by the ordered checks in BR-6.

### 2.6 Scope — `infer_scope(catalog, database) -> Scope`
Lowercase both values, then apply the database suffix rule, falling back to the catalog map (BR-7). `event_scope(session_scope, table_scopes) -> Scope` applies BR-8's order.

### 2.7 Referenced tables — `referenced_tables(sql, default_catalog, default_database) -> list[TableRef]`
Collect the CTE names; walk the significant tokens; after a trigger keyword (BR-9), read a dotted name of `word`/`quoted_ident` parts; ignore it if `(` follows or it is a CTE name; qualify; deduplicate in order.

### 2.8 Event key — `event_key(query_id, payload) -> str`
`query_id.strip()` if non-empty; otherwise `sha256:` + the hex digest of `canonical_json(payload)`, where `canonical_json` parses the JSON and dumps it with sorted keys and compact separators. If the payload isn't valid JSON, it is hashed as-is.

### 2.9 Payload access — `payload_field(payload_dict, name) -> value`
Key lookup with BR-15's normalization; used for every silver column.

## 3. Silver processing (C3)

1. `start_spark_job("silver_processing")` gives `spark` and `config`.
2. Check that the bronze table exists (BR-34); `ensure_silver_tables` creates the two silver tables if missing (partitioned by `event_date`, at their locations).
3. Start the stream: `readStream` on bronze, `trigger(availableNow=True)`, `foreachBatch(merge_batch)`, checkpoint (BR-33). Wait for it to finish.
4. `merge_batch(batch_df, batch_id)`:
   1. **Parse and enrich** on the driver, in chunks of at most 20000 rows read with `toLocalIterator()` (refined at Code Generation planning: Spark's Python workers don't have the repository on their import path in Jupyter, so no Python UDF is used; each chunk goes back with `createDataFrame` and steps 2 to 4 run per chunk, which the insert-only merge makes safe): parse the JSON payload; derive every silver column with C1 (`redact_credentials` on `stmt` → `sql_text`; `mask_sql(sql_text)`; `statement_category`; `actor_type`; `referenced_tables`; `infer_scope`/`event_scope`; `event_key`; canonical redacted payload); mark `unparsed` rows (BR-12, BR-14).
   2. **Deduplicate** by `event_key`, keeping the row with the earliest `copied_at` (BR-32).
   3. **MERGE** the events into `starrocks_audit_event`, insert only.
   4. **Explode** the referenced tables of the batch's events into table-access rows, deduplicate by their key, MERGE insert only into `starrocks_audit_table_access`.
   5. Log counts: rows read, distinct events, inserted events, unparsed rows, table-access rows.
5. Stop Spark in `finally:`.

The enrichment logic is one pure function, `enrich_payload(query_id, event_time, payload, copied_at, copy_task) -> dict`, so the whole row transformation is unit-tested without Spark; the notebook only maps it over the batch.

## 3b. Bronze ingestion (C6, pull model 2026-11-25)
1. Check `lookback_minutes` (BR-35); `start_spark_job("bronze_ingestion")`.
2. `bronze_watermark`: `SELECT max(event_time)` from bronze (fails when bronze is missing).
3. Open `lakehouse_starrocks.connect()`; run `pull_query(staging_table, watermark - lookback)`; `fetchmany(20000)` at a time.
4. For each chunk: `staging_to_bronze` per row (redact, canonical JSON, UTC times), then `createDataFrame(...).writeTo(bronze).append()`.
5. Close the connection (always); log watermark, start, rows, chunks, first and last event times.

## 4. Bronze deployment (C2)
`ensure_bronze_table(spark, config)`: if the table is missing, `CREATE TABLE <catalog>.<db>.starrocks_audit_event_fe_brz (...) USING iceberg PARTITIONED BY (days(event_time)) LOCATION '<storage_path>/<catalog>/<db>/starrocks_audit_event_fe_brz' TBLPROPERTIES ('format-version'='2', 'write.format.default'='parquet')` and return `created`. If it exists, compare column names and types with `bronze_schema()`; return `exists` when equal, raise when not (never alters).

## 5. Dev sample generator (C5)
`generate_events(count, seed, start)` uses a seeded `random.Random` to produce staging-shaped dicts covering every category, the actor types (people, `srv-default-writer`, `srv-starrocks`, `root`), the products `default`, `audit` and `sales` in all environments, about 10% failures, about 5% slow queries (60 s or more), about 10% connection events (some failed), `CREATE USER ... IDENTIFIED BY` statements with fake passwords, a few without `queryId`, and about 20% exact duplicates. `write_sample_events` builds bronze rows (`copy_task = 'ops_generate_sample_events'`, `copied_at` now) and appends them. It refuses any environment other than `dev` (BR-50).

## 6. Failure behavior

| Situation | Behavior |
|---|---|
| Bronze table missing | Silver fails at start with BR-34's message; no checkpoint written |
| Bronze schema differs from the contract | Deploy fails without altering; silver reads only the contract's columns |
| Unparseable or incomplete payload | Row kept as `unparsed` (BR-14); run succeeds; count logged |
| Non-numeric numeric field | Null (BR-15) |
| Spark or merge failure mid-run | Task fails; Airflow retries once after 300 s; the checkpoint replays the batch; insert-only merge prevents duplicates |
| Lost or deleted checkpoint | Next run reprocesses all of bronze; insert-only merge prevents duplicates (slower run only) |
| Run longer than 2400 s | Task times out and retries; the next hourly run continues from the checkpoint |
| Missed or failed bronze runs | The next run starts at the bronze watermark minus the lookback: gaps up to the staging table's 7 days are recovered (BR-35) |
| StarRocks unreachable or login refused | The bronze task fails and retries once after 300 s; silver then processes nothing new; the next hourly run catches up |

## 7. Testable properties (PBT-01)
Partial PBT is enforced (PBT-02, -03, -07, -08, -09). Framework: Hypothesis (PBT-09), with the fixed seed from `tests/workload_foundation/conftest.py` (PBT-08).

| Component | Property | Category | Enforced rule |
|---|---|---|---|
| `staging_to_bronze` | The same staging row gives the same payload whatever its column order; no credential survives | Invariant | PBT-03 |
| `tokenize` | Joining the tokens' text returns the input, for any text | Round-trip | PBT-02 |
| `infer_scope` | For generated product tokens and environments, `infer_scope(catalog(env), database(token, env))` returns `(token, env)` | Round-trip | PBT-02 |
| `event_key` | Equal for the same payload serialized with any key order and spacing (no `queryId`) | Invariant | PBT-03 |
| `mask_sql` | No generated literal value appears in the output; every generated identifier does | Invariant | PBT-03 |
| `mask_sql` | Two statements differing only in literal values mask to the same text | Oracle (digest equivalence) | PBT-03 (advisory PBT-05) |
| `mask_sql` | `mask_sql(mask_sql(x)) == mask_sql(x)` | Idempotence | advisory (PBT-04) |
| `redact_credentials` | No generated secret appears in the output; text without credential patterns is unchanged | Invariant | PBT-03 |
| `redact_credentials` | Idempotent | Idempotence | advisory (PBT-04) |
| `statement_category` | Unchanged by leading comments, whitespace, keyword case, and masking or redaction of the text | Invariant | PBT-03 |
| `actor_type` | Unchanged by quoting, `@host` suffixes and case | Invariant | PBT-03 |
| `referenced_tables` | Generated fully qualified table names in generated statements are all found; the result is the same for the original, masked and redacted text | Invariant | PBT-03 |
| `enrich_payload` | Never raises for any JSON or non-JSON payload; always returns a valid `event_type` and a non-empty `event_key` | Invariant | PBT-03 |
| Dedup of a batch | Applying it twice equals applying it once; duplicates in the input don't change the output | Idempotence | advisory (PBT-04) |

Generators (PBT-07): SQL statements built from a small grammar (SELECT/INSERT/CREATE USER/SET/SHOW with qualified names from product-style namespaces, string, number and date literals with random content, comments with random text); payload dicts with AuditLoader's camelCase keys; StarRocks user names with optional quotes and hosts.

No PBT properties for the Spark wiring (C2 `ensure_bronze_table`, the stream setup) and the gold SQL: they are thin integration layers, covered by example-based tests with fakes and by the view-intent tests.
