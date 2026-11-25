# Business Logic Model — Unit 2 `starrocks` (component `query-audit`)

Rules: [business-rules.md](business-rules.md). Entities: [domain-entities.md](domain-entities.md). Components C1 to C5: [application design](../../../inception/application-design/components.md).

## 1. End-to-end flow

```text
staging row --(platform copy, redacts, hourly, 2h overlap)--> bronze AuditEventRaw
bronze --(silver_processing, availableNow stream)--> micro-batch
micro-batch: parse payload -> enrich (audit_sql) -> dedupe by event_key
          -> MERGE insert-if-absent: starrocks_audit_event
          -> explode referenced tables -> MERGE insert-if-absent: starrocks_audit_table_access
silver --(StarRocks async refresh, hh:35)--> gold views
```

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
| Platform copy missed hours | Covered by the 2-hour overlap if one run is missed; a longer gap is a platform catch-up (paired intent) |

## 7. Testable properties (PBT-01)
Partial PBT is enforced (PBT-02, -03, -07, -08, -09). Framework: Hypothesis (PBT-09), with the fixed seed from `tests/workload_foundation/conftest.py` (PBT-08).

| Component | Property | Category | Enforced rule |
|---|---|---|---|
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
