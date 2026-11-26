# Code Summary — Unit 2 `starrocks`

Intent `20261125-starrocks-query-audit`. Plan: [starrocks-code-generation-plan.md](../../plans/starrocks-code-generation-plan.md). Branch `starrocks-query-audit`.

## Files
| Change | Path | Component |
|---|---|---|
| Created | `notebooks/common/audit_sql.py` | C1: tokenizer, `redact_credentials`, `mask_sql`, `statement_category`, `actor_type`, `infer_scope`, `event_scope`, `referenced_tables`, `event_key`, `enrich_payload`, `table_access_rows`, `deduplicate`; bronze and silver column contracts |
| Created | `notebooks/workload/starrocks/bronze/bronze_ingestion.py` (change request 2026-11-25) | C6 |
| Created | `notebooks/workload/starrocks/deploy/deploy_bronze_table.py` | C2 |
| Created | `notebooks/workload/starrocks/silver/silver_processing.py` | C3 |
| Created | `notebooks/workload/starrocks/ops/ops_generate_sample_events.py` | C5 |
| Created | `workflows/starrocks/starrocks_audit.json` (hourly, `20 * * * *`: bronze task, then silver), `workflows/starrocks/starrocks_deploy.json` (manual) | C2, C3, C6 |
| Modified | `configs/{dev,uat,prod}.yml`: `starrocks_audit_table` (the staging table) | C6 |
| Deleted | `workflows/.gitkeep` | — |
| Created | `materialized_views/starrocks/mv_starrocks_{user_day,product_day,table_day,slow_failed,login_day}.sql` | C4 |
| Created | `tests/starrocks/conftest.py`, `test_audit_sql.py`, `test_audit_sql_properties.py`, `test_deploy_bronze_table.py`, `test_silver_processing.py`, `test_ops_generate_sample_events.py`, `test_workflows.py`, `test_materialized_views.py` | tests |
| Modified | `tests/workload_foundation/test_jupyter_scripts.py` (at least one subproject required again) | foundation |
| Modified | `README.md` (layout, `starrocks` subproject section with tables, access, operations, first deployment) | docs |

## Tests
| File | Kind | Count |
|---|---|---|
| `test_audit_sql.py` | example-based, BR-1 to BR-34 | 98 |
| `test_audit_sql_properties.py` | Hypothesis (PBT-02 round trips: tokenizer, scope; PBT-03 invariants: masking leaks, digest equivalence, redaction, category, actor, tables, event key, enrichment, `staging_to_bronze` order and secrets; advisory PBT-04 idempotence: masking, redaction, dedup) | 15 |
| `test_bronze_ingestion.py` | example-based with fake Spark and StarRocks connections | 19 |
| `test_deploy_bronze_table.py` | example-based with a fake Spark | 9 |
| `test_silver_processing.py` | example-based with a fake Spark (insert-only merge modeled) | 13 |
| `test_ops_generate_sample_events.py` | example-based | 12 |
| `test_workflows.py` | example-based | 5 |
| `test_materialized_views.py` | platform file rules and view intent | 21 |
| `tests/workload_foundation/` | unchanged foundation tests | 68 |
| **Total** | | **260** |

## Verification (2026-11-25)
| Check | Result |
|---|---|
| `python -m compileall -q dags notebooks` | PASS |
| `scripts/code_quality.sh all` (Ruff, Black, Flake8, Bandit) | PASS |
| `python -m pytest -q` (seed 0 printed) | PASS, 260 passed (after the change request) |
| `lakehouse_dags.validate .` (platform validator, run locally) | PASS, `OK: 2 workflow(s), 5 materialized view(s)` |
| Python 3.10 (platform image `local/lakehouse-spark-runtime:3.5.9_1.10.2`, Python 3.10.12, no network, read-only mount) | PASS before the change request: every notebook parses as 3.10; the notebooks import; 353 generated rows enrich into all six categories plus connections. After the change request the image had been removed by Rancher Desktop's image cleanup, so only the 3.10 grammar check (`ast.parse(..., feature_version=(3, 10))`, 11 files) and a search for 3.11+ APIs were run: PASS. The in-image recheck is part of Build and Test. |

## Deviations and decisions during generation
- **BR-9 refined** (DOC-01, business-rules.md updated): only SELECT/DML/DDL are searched; `INTO t (cols)` and `CREATE TABLE t (...)` keep their target (a `(` after them is a column list); `FROM t1, t2` reads every table; `IF [NOT] EXISTS` skipped; `FROM` inside a function (`EXTRACT(YEAR FROM d)`) ignored.
- **Credential redaction** expression split into named conditions (Flake8 W503 with Black's operator placement); behavior unchanged.
- **Bandit B608** on the sample generator's statement templates: marked `# nosec B608 - sample text, never executed` on those four lines, as the template repository did for its generated SQL. The notebooks' own DDL and MERGE statements are built from config values and constants and were not flagged.
- **Times**: silver and the sample generator set `spark.sql.session.timeZone = UTC` and work with UTC wall-clock datetimes (BR contract: bronze `event_time` is UTC).
- **Masking spacing**: a function call masks as `EXTRACT (YEAR FROM d)` (space before `(` after a word); deterministic and idempotent, kept for simplicity.

## Not yet verified (for Build and Test)
- Real Spark: the Iceberg DDL, the `availableNow` stream, `toLocalIterator`, `createDataFrame` with the DDL schemas, and the MERGE, against a real catalog.
- StarRocks: the five views' DDL (`PARTITION BY event_date` on an Iceberg table, `partition_ttl`, `COUNT(DISTINCT)`), in the dev MV database.
- End-to-end against the real StarRocks staging table (needs the paired intent's AuditLoader plugin, grants and `lakehouse_starrocks.connect()`).

## Change request 2026-11-25: pull model
- The user asked for a bronze task that extracts from StarRocks, and for dev to pull the real (only) StarRocks instance. Added `bronze/bronze_ingestion.py` (C6) and `audit_sql.staging_to_bronze`; `starrocks_audit` runs the bronze task, then silver; `starrocks_audit_table` added to the configs; notebook title cells, README and AGENTS.md updated.
- Requirements (FR-2, FR-3.2, D-1, P-1), application design (AD-2, AD-7, new AD-10, C6, P3, services, dependencies, methods) and functional design (BR-23, BR-30, BR-35, BR-36, BR-50, entities, logic model, properties) updated (DOC-01).
- New platform dependency: `lakehouse_starrocks.connect()` (writer token in Airflow, the user's password in Jupyter), `SELECT` grants on the staging table, StarRocks reachable with its CA from Spark runs and Jupyter.
