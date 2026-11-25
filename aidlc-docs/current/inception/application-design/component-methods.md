# Component Methods — StarRocks Query Audit

Signatures and purposes only. Business rules (masking grammar, category lists, inference rules, thresholds, deduplication order) are defined in Functional Design for Unit 2 `starrocks`.

## C1 `notebooks/common/audit_sql.py`

| Function | Input | Output | Purpose |
|---|---|---|---|
| `tokenize(sql: str) -> list[Token]` | SQL text | tokens (`kind`, `text`) | Quote-, comment- and backtick-aware lexing; the base of every other function |
| `mask_sql(sql: str \| None) -> str \| None` | SQL text | masked text | Replaces string, number, date and hex literals with `?` and collapses `IN (...)` lists to `IN (?)`; `None` stays `None` |
| `statement_category(sql: str \| None) -> str \| None` | SQL text | `SELECT`, `DML`, `DDL`, `ADMIN`, `METADATA`, `SESSION`, `OTHER` | Classifies by the leading keyword(s) after comments and `WITH` (seven categories: Functional Design FD-Q1) |
| `actor_type(user: str \| None) -> str` | StarRocks user | `person`, `product_writer`, `platform_service`, `unknown` | Separates people from `srv-<product>-writer`, `srv-starrocks`, `root` and other platform identities |
| `referenced_tables(sql: str \| None, default_catalog: str \| None, default_database: str \| None) -> list[TableRef]` | SQL text and session context | `TableRef(catalog, database, table)` list, distinct | Tables after `FROM`, `JOIN`, `INTO`, `UPDATE`, `TABLE`, qualified with the session defaults; best effort |
| `infer_scope(catalog: str \| None, database: str \| None) -> Scope` | a catalog and database | `Scope(data_product, environment)`, each possibly `None` | Maps `<token>_lh` / `_lhuat` / `_lhdev` and the catalogs `lakehouse` / `lakehouse_uat` / `lakehouse_dev` to product and environment |
| `redact_credentials(sql: str \| None) -> str \| None` | SQL text | text with credentials as `'***'` | Passwords and secret-like property values (Functional Design FD-Q2) |
| `event_scope(session: Scope, tables: list[Scope]) -> Scope` | scopes | event-level `Scope` | Attribution order for an event (FD-Q4) |
| `payload_field(payload: dict, name: str) -> Any` | payload, field | value or `None` | Case- and underscore-insensitive key lookup |
| `enrich_payload(query_id, event_time, payload, copied_at, copy_task) -> dict` | one bronze row | one silver row (dict) | The whole row transformation, pure; the notebook maps it over a batch |
| `event_key(query_id: str \| None, payload: str) -> str` | AuditLoader `queryId` and the raw JSON | stable key | `query_id` when present, otherwise a SHA-256 of the canonical payload (connection events may lack a query ID) |

Types: `Token`, `TableRef` and `Scope` are frozen dataclasses (or named tuples) defined in the module.

## C2 `deploy/deploy_bronze_table.py`

| Function | Purpose |
|---|---|
| `bronze_table_name(config) -> str` | `<catalog_name>.<database_name>.starrocks_audit_event_fe_brz` |
| `bronze_schema() -> StructType` | The bronze contract (see [component-dependency.md](component-dependency.md)) |
| `ensure_bronze_table(spark, config) -> str` | Creates the table if missing (Parquet, `format-version` 2, partitioned by `days(event_time)`, explicit location); returns `created` or `exists`; raises on schema mismatch |

## C3 `silver/silver_processing.py`

| Function | Purpose |
|---|---|
| `ensure_silver_tables(spark, config) -> None` | Creates `starrocks_audit_event` and `starrocks_audit_table_access` if missing |
| `enriched_chunks(batch_df, size) -> Iterator[list[dict]]` | Reads the batch on the driver in chunks and applies C1's `enrich_payload` to each row (redaction, masking, category, actor, scope, key, `event_type`, `event_date`) |
| `table_access_rows(events: list[dict]) -> list[dict]` | One row per event and referenced table |
| `deduplicate(rows: list[dict], key_columns) -> list[dict]` | One row per key within a chunk, earliest `copied_at` kept (bronze holds overlap duplicates) |
| `merge_batch(batch_df, batch_id) -> None` | `foreachBatch` sink: MERGE insert-if-absent into both silver tables |
| `run(spark, config) -> None` | Builds the `availableNow` stream from bronze with the checkpoint and waits for it to finish |

Task arguments: none in this intent.

## C4 Gold views
SQL only; one `CREATE MATERIALIZED VIEW ${mv_database}.<name>` per file, `REFRESH ASYNC` hourly, sources `${catalog}.${namespace}.starrocks_audit_event` and `starrocks_audit_table_access`.

## C5 `ops/ops_generate_sample_events.py`

| Function | Purpose |
|---|---|
| `generate_events(count: int, seed: int, start: datetime) -> list[dict]` | Deterministic synthetic AuditLoader-shaped rows (all categories, connections, failures, platform users, some duplicates) |
| `write_sample_events(spark, config, rows) -> int` | Appends them to the dev bronze table in the bronze contract; returns the row count |
| `check_arguments(args, environment) -> None` | Refuses an environment other than `dev` and invalid counts |

Task arguments: `--events` (default `200`), `--seed` (default `1`).
