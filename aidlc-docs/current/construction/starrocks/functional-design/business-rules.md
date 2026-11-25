# Business Rules — Unit 2 `starrocks` (component `query-audit`)

Decisions: [starrocks-functional-design-plan.md](../../plans/starrocks-functional-design-plan.md) (FD-Q1 A, Q2 A, Q3 A, Q4 A, Q5 C, Q6 A, Q7 A). Entities: [domain-entities.md](domain-entities.md).

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
- **BR-12** `event_type`: `connection` when the payload marks a connection event (`eventType`/`type` is `CONNECTION`, or `queryType` is `connection`); `query` otherwise; `unparsed` when the payload isn't a JSON object or has no usable `timestamp` and no `stmt` and no `queryId` (FD-Q7). The connection markers are confirmed against StarRocks 4.1.7 by the paired intent (probe P-1); until then the mapping lives in one constant.
- **BR-13** `failed` is true when `state` is `ERR` (case-insensitive), false otherwise (including null).
- **BR-14** An `unparsed` row keeps `event_key`, `event_type`, `event_time` and `event_date` from bronze, `actor_type = 'unknown'`, `failed = false`, the payload, copy metadata and `loaded_at`; other columns are null. The run logs how many rows were unparsed and succeeds.
- **BR-15** Payload keys are matched case-insensitively and with `_` ignored, so `queryId`, `query_id` and `QUERYID` all match. Numeric fields that aren't valid integers become null; they never fail the run.

## Credentials (`redact_credentials`)
- **BR-20** Credentials are replaced by `'***'` (FD-Q2): the string after `IDENTIFIED BY`, `IDENTIFIED WITH <plugin> BY|AS`, `PASSWORD(`, `SET PASSWORD ... =`, and the value of any key-value pair (`"key" = "value"` or `key = 'value'`) whose key contains `password`, `secret`, `access_key`, `access.key`, `accesskey`, `token`, `credential` or `private_key` (case-insensitive). Everything else is unchanged, including whitespace and comments.
- **BR-21** Silver's `sql_text` is the redacted `stmt`. Masking runs on the redacted text, so `sql_masked` never holds credentials either.
- **BR-22** Silver's `payload` is the bronze payload with `stmt` redacted, serialized as canonical JSON (sorted keys, no extra whitespace).
- **BR-23** The bronze contract adds: the platform copy task applies BR-20's patterns to `stmt` before writing (`regexp_replace` in StarRocks), so bronze never holds credentials. Silver still redacts, so a gap in the platform's patterns never reaches silver or gold.

## Keys, deduplication and loading
- **BR-30** Bronze may hold each staging row twice (the 2-hour copy overlap). Duplicates have identical payloads.
- **BR-31** `event_key` = the trimmed `queryId` when it is non-empty; otherwise `sha256:` + the SHA-256 of the canonical JSON of the payload as found in bronze (sorted keys, no extra whitespace). Duplicates therefore share a key.
- **BR-32** Silver is insert-only: a micro-batch is deduplicated by key (keeping the earliest `copied_at` as `first_copied_at`), then merged with "insert when not matched" on (`event_key`, `event_date`) for events and (`event_key`, `ref_catalog`, `ref_database`, `ref_table`, `event_date`) for table accesses. Existing rows are never updated or deleted (FR-7.1).
- **BR-33** Silver reads bronze as a stream of appended snapshots, `availableNow`, checkpoint `<checkpoint_path>/starrocks/starrocks_audit_event`. Replaying a batch (retry, lost checkpoint) inserts nothing twice, by BR-32.
- **BR-34** Silver requires the bronze table: if it doesn't exist, the job fails with "bronze table <name> not found: run the starrocks_deploy workflow first".

## Gold
- **BR-40** No view selects `sql_text` or `payload`. `mv_starrocks_slow_failed` exposes `sql_masked` and `digest` only.
- **BR-41** Silver is partitioned by the identity of `event_date`, so the views can be partitioned by `event_date` in line with it.
- **BR-42** Slow means `query_time_ms >= 60000` (FD-Q5). `mv_starrocks_slow_failed` keeps the last 30 days (FD-Q6) by partition time-to-live (`partition_ttl = "30 DAY"`, partitioned by `event_date`). Fallback if StarRocks refuses it in dev: filter `event_date >= date_sub(current_date(), 30)`; if that is refused too, keep all history and record the deviation.
- **BR-43** Views refresh hourly, starting at minute 35, after the hh:20 silver load.

## Environments and retention
- **BR-50** The deploy and silver notebooks run in `dev`, `uat` and `prod`. The sample generator runs only in `dev` and refuses others; it requires `--events` to be an integer from 1 to 100000 and `--seed` an integer.
- **BR-51** Nothing deletes rows from bronze or silver (FR-7.1). Iceberg snapshot expiry, if added later, must not remove rows.
