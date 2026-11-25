# Code Generation Plan — Unit 2 `starrocks`

Intent `20261125-starrocks-query-audit`. This plan is the single source of truth for Unit 2's Code Generation. Design: [application design](../../inception/application-design/application-design.md), [functional design](../starrocks/functional-design/) (BR-1 to BR-51). Branch: `starrocks-query-audit` (from `main` at `dd99258`).

## Unit context
- **Implements**: FR-3 (bronze), FR-4 (silver), FR-5 (gold), FR-7 (retention), NFR-2 to NFR-6; components C1 to C5.
- **Depends on**: Unit 1 (green baseline: done). Runtime dependency on the paired platform intent only for uat/prod data (bronze rows, view deploy), not for this repository's tests.
- **Platform constraints honored**: Python 3.10 on the platform (no 3.11+ syntax or modules in notebooks and `notebooks/common/`); `lakehouse_spark` owns the session; workflow JSON v1; view files with one statement, no `;`, only `${catalog}`, `${namespace}`, `${mv_database}`, `COMMENT` right after the name.
- **Implementation decision (recorded in the functional design)**: silver enriches rows on the driver in chunks of at most 20000 (no Python UDFs, since Spark's Python workers lack the repository in Jupyter); each chunk is merged insert-only.
- **Tests run locally without Spark**: notebooks import `pyspark` only inside functions; Spark calls are covered with small fakes, as the template's tests did.

## Steps

### Step 1 — Shared audit logic (C1)
- [x] `notebooks/common/audit_sql.py`: `Token`, `TableRef`, `Scope`; `tokenize`, `redact_credentials`, `mask_sql`, `statement_category`, `actor_type`, `normalize_user`, `infer_scope`, `event_scope`, `referenced_tables`, `canonical_json`, `payload_field`, `event_key`, `enrich_payload`, `table_access_rows`, `deduplicate`; constants for categories, connection markers (BR-12), slow threshold (BR-42) and the silver column list with Spark SQL types (used by the notebooks' DDL). Standard library only; Google-style docstrings.

### Step 2 — Tests for C1
- [x] `tests/starrocks/conftest.py`: puts `notebooks/workload/starrocks/<layer>/` folders and the repository root on `sys.path`; Hypothesis seed as in the foundation tests (PBT-08).
- [x] `tests/starrocks/test_audit_sql.py`: example-based tests for every rule BR-1 to BR-34 that C1 implements (masking cases, hints, `IN` lists, unterminated strings, seven categories including `WITH`, `ADMIN` precedence, actors, scope suffixes and catalogs, attribution order including `multiple`, table extraction with CTEs and functions, redaction patterns, event key with and without `queryId`, `unparsed` rows, numeric coercion, key normalization) (PBT-10).
- [x] `tests/starrocks/test_audit_sql_properties.py`: the Hypothesis properties of the functional design's Testable Properties table, with domain generators for SQL statements, payloads and user names (PBT-02, -03, -07, -08; advisory PBT-04 idempotence properties included).

### Step 3 — Bronze deployment (C2)
- [x] `notebooks/workload/starrocks/deploy/deploy_bronze_table.py`: notebook standard (title, Initialization, Imports, own sections, Execution); `bronze_table_name`, `bronze_location`, `BRONZE_COLUMNS`, `create_table_sql`, `schema_problems`, `ensure_bronze_table`; logs one JSON record.
- [x] `tests/starrocks/test_deploy_bronze_table.py`: names and locations, the DDL (Parquet, format-version 2, `days(event_time)`), created/exists/mismatch with a fake Spark.

### Step 4 — Silver processing (C3)
- [x] `notebooks/workload/starrocks/silver/silver_processing.py`: names, locations and checkpoint (`<checkpoint_path>/starrocks/starrocks_audit_event`); `ensure_silver_tables` (DDL partitioned by `event_date`); `enriched_chunks`; `merge_sql` (insert-only, BR-32); `merge_batch`; `run_stream` (`availableNow`, `foreachBatch`); `process` with start and summary JSON logs; BR-34 check; no task arguments.
- [x] `tests/starrocks/test_silver_processing.py`: names, checkpoint validation, DDL, merge SQL (no `UPDATE`/`DELETE`), chunking and dedup across chunks with fakes, missing-bronze error, counts.

### Step 5 — Dev sample events (C5)
- [x] `notebooks/workload/starrocks/ops/ops_generate_sample_events.py`: Task arguments section (`--events` default `200`, `--seed` default `1`), `check_arguments` (BR-50, dev only), `generate_events`, `bronze_rows`, `write_sample_events`.
- [x] `tests/starrocks/test_ops_generate_sample_events.py`: determinism by seed, coverage of categories, actors, failures, slow, connections, credentials and duplicates; argument checks; refusal outside dev; generated rows pass through `enrich_payload` with no secret left.

### Step 6 — Workflows
- [x] `workflows/starrocks/starrocks_audit.json`: schedule `20 * * * *`, task `starrocks_audit_event_load` (`silver/silver_processing.py`, `retries: 1`, `retry_delay_seconds: 300`, `execution_timeout_seconds: 2400`).
- [x] `workflows/starrocks/starrocks_deploy.json`: schedule `null`, task `starrocks_audit_event_fe_brz_create` (`deploy/deploy_bronze_table.py`).
- [x] Remove `workflows/.gitkeep`.
- [x] `tests/starrocks/test_workflows.py`: the two files' shape, entry points exist, names unique, schedule after the copy (minute 20).

### Step 7 — Gold views (C4)
- [x] `materialized_views/starrocks/mv_starrocks_user_day.sql`, `mv_starrocks_product_day.sql`, `mv_starrocks_table_day.sql`, `mv_starrocks_slow_failed.sql` (`partition_ttl = "30 DAY"`, BR-42), `mv_starrocks_login_day.sql`: each with `COMMENT`, `PARTITION BY event_date`, `REFRESH ASYNC START('2026-11-26 00:35:00') EVERY (INTERVAL 1 HOUR)`, silver sources only.
- [x] `tests/starrocks/test_materialized_views.py`: the platform's file rules (one statement, no `;`, known placeholders, name matches file), silver sources only, never `sql_text` or `payload` (BR-40), the slow threshold 60000 and the 30-day TTL, hourly refresh at minute 35, each view's grain and filters.

### Step 8 — Restore the foundation check
- [x] `tests/workload_foundation/test_jupyter_scripts.py`: the subproject-folder test requires at least one subproject again (relaxed in Unit 1).

### Step 9 — Documentation
- [x] `README.md`: "Repository layout" lists the `starrocks` files; "Subprojects" describes tables, workflows, views, the dev sample, first deployment order and operations (lost checkpoint, unparsed rows, re-running deploy).
- [x] `AGENTS.md`: check the layout tree against the delivered files (it already lists them); no rule changes expected.

### Step 10 — Verify
- [x] `python -m compileall -q dags notebooks`; `scripts/code_quality.sh all`; `python -m pytest -q` (seed printed); `lakehouse_dags.validate .` from the platform repository (expects 2 workflows, 5 materialized views); a Python 3.10 syntax check of `notebooks/` (`python3.10 -m py_compile` if available, otherwise `ast.parse(..., feature_version=(3, 10))`).

### Step 11 — Documentation summary
- [x] `aidlc-docs/current/construction/starrocks/code/summary.md`: files, tests (counts by kind), verification results, deviations.

### Step 12 — Commit
- [x] Commit on `starrocks-query-audit` (including Unit 1's post-push records). **Local only**: pushing the branch and opening the pull request into `uat` are prepared in the Deploy stage and done only when you say so.

### Step 13 — Added during generation (DOC-02)
- [x] BR-9 refined and documented (column lists after `INTO`/`TABLE`, comma joins, `IF [NOT] EXISTS`, `FROM` inside functions, SELECT/DML/DDL only).
- [x] Redaction condition split into named parts (Flake8 W503); Black applied to the new files.
- [x] `# nosec B608` with a reason on the sample generator's four statement templates.
- [x] Python 3.10 check run in the platform's Spark runtime image (no Python 3.10 on the host).

## Traceability
| Requirement / rule | Steps |
|---|---|
| FR-3, AD-1, AD-8 | 3, 6 |
| FR-4, BR-1 to BR-34 | 1, 2, 4 |
| FR-5, BR-40 to BR-43 | 7 |
| FR-7, BR-51 | 3, 4 (insert-only, no deletes) |
| NFR-2 | 1, 4 (dedup, insert-only, checkpoint) |
| NFR-3 | 4, 6 (one task, chunked driver work) |
| NFR-4 | 1, 7 (redaction, masking, masked-only views) |
| NFR-5 | all; 10 |
| NFR-6, PBT-02/-03/-07/-08/-09 | 2, 5, 10 |
| AD-7, BR-50 | 5 |
