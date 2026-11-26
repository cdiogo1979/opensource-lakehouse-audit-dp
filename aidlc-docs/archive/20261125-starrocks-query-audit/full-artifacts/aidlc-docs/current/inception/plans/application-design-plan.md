# Application Design Plan — StarRocks Query Audit

Intent `20261125-starrocks-query-audit`. Inputs: [requirements.md](../requirements/requirements.md), [execution-plan.md](execution-plan.md), canonical `aidlc-docs/knowledge/opensource-lakehouse/application-design.md`.

## Facts checked (2026-11-25, StarRocks documentation)
- AuditLoader writes to `starrocks_audit_db__.starrocks_audit_tbl__`, partitioned by day on `timestamp` (dynamic partitions). Its columns vary between StarRocks versions (recent versions use camelCase: `queryId`, `timestamp`, `queryType`, `clientIp`, `user`, `db`, `state`, `errorCode`, `queryTime`, `scanBytes`, `scanRows`, `returnRows`, `cpuCostNs`, `memCostBytes`, `stmtId`, `isQuery`, `feIp`, `stmt`, `digest`, ...); fields absent from the table are ignored. The exact 4.1.7 column list is a probe for the paired intent.
- `SUBMIT TASK ... SCHEDULE EVERY (INTERVAL ...)` exists from StarRocks 3.3.
- StarRocks writes to Iceberg tables with `INSERT` (Parquet tables only). A scheduled task writing to an Iceberg table in a Polaris catalog is not documented as such: probe P-1.

## Plan
- [x] Answer the design questions below
- [x] Analyze the answers for ambiguity; follow-up questions if needed (none: Q1:B and Q2:B refine FR-2.2 and FR-3.2, updated per DOC-01)
- [x] Generate `components.md` (component definitions and responsibilities)
- [x] Generate `component-methods.md` (signatures; business rules come in Functional Design)
- [x] Generate `services.md` (workflow and orchestration, the platform task)
- [x] Generate `component-dependency.md` (dependencies, data flow, the bronze contract)
- [x] Generate `application-design.md` (consolidated)
- [x] Validate completeness and consistency against FR-1 to FR-8 and NFR-1 to NFR-6

## Question 1
What shape should the bronze table `starrocks_audit_event_fe_brz` have, given that it is the contract the platform's StarRocks task writes to?

A) Typed columns: an explicit list of the AuditLoader columns this design needs (named in snake_case), plus load metadata (`copied_at`, `source_fe`); the platform task selects those columns by name, so a renamed column fails loudly at copy time

B) Raw payload: a few typed key columns (`query_id`, `event_time`) plus one JSON string with the whole AuditLoader row (built by the task with `to_json` or `json_object`); silver parses it, so new or renamed columns never break the copy

C) Mirror: the same columns and names as the AuditLoader table, copied with `SELECT *`; the table definition follows the StarRocks version

D) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question 2
How should the hourly platform task avoid losing or duplicating rows (NFR-2)?

A) Watermark with lookback and anti-join: copy staging rows with `timestamp` newer than the bronze maximum minus a lookback (for example 30 minutes) and whose `query_id` is not already in bronze for that window; bronze stays duplicate-free and late rows are caught

B) Overlapping window, deduplicated later: copy the last 2 hours every hour without checking bronze; bronze holds duplicates, silver deduplicates by `query_id`

C) Partition overwrite: bronze partitioned by event hour, the task overwrites the last N hourly partitions each run (`INSERT OVERWRITE`); idempotent, if StarRocks supports overwrite on Polaris Iceberg tables

D) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question 3
How should silver read bronze incrementally?

A) Spark Structured Streaming over the bronze Iceberg table with `trigger(availableNow=True)` and a checkpoint under `checkpoint_path/starrocks/<table>`, merging each micro-batch into silver (the same pattern the template's tpch silver used)

B) Batch MERGE over an event-time lookback window (for example the last 3 hours of bronze) every run, with no checkpoint

C) Full rebuild of silver from bronze every run

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 4
How should silver be split?

A) One table `starrocks_audit_event` with an `event_type` column (query or connection) and nullable query-only columns; gold views split by type. Works whether or not AuditLoader records connection events (probe P-1)

B) Two tables `starrocks_query` and `starrocks_connection`, each with its own columns

C) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 5
Where should the pure audit logic (SQL literal masking, statement category, actor type, product and environment inference) live?

A) A module in `notebooks/common/` (for example `notebooks/common/audit_sql.py`): shared code, as AGENTS.md asks, reusable by later audit sources (RustFS, Airflow, Polaris), imported by the silver notebook and tested directly

B) Inside the silver notebook itself, as top-level functions the tests import

C) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 6
How should SQL literals be masked?

A) A small quote- and comment-aware tokenizer written here (no new dependency): string, number, date and hex literals become `?`, `IN (...)` lists collapse to `IN (?)`; deterministic and fully property-tested

B) The `sqlglot` library (pure-Python wheel) to parse and rewrite literals, with the tokenizer as a fallback when parsing fails

C) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 7
Which environments receive real audit rows from the platform task? (StarRocks is one instance, so its audit log covers every environment's queries.)

A) Production and UAT receive real copies (UAT to verify releases end-to-end); dev gets synthetic rows from an `ops/` notebook

B) Production only; UAT and dev get synthetic rows from an `ops/` notebook

C) All three environments receive real copies

D) Other (please describe after [Answer]: tag below)

[Answer]: A
