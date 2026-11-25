# PR request: StarRocks query audit (audit data product, first source)

Proposed pull request: `starrocks-query-audit` into `uat`, then `uat` into `main`. Intent `20261125-starrocks-query-audit`. Not yet pushed or opened.

## Problem
The lakehouse has no record of what is done to its data. This repository becomes the **audit data product**: one subproject per audited source, starting with StarRocks. Every query, login and platform action executed in the shared StarRocks instance should be queryable as Iceberg tables within about an hour and kept indefinitely. Full SQL should go only to the product's owners and developers. Analysts get masked, aggregated views.

## Solution
- **Pull model:** StarRocks' AuditLoader plugin (platform) writes every audit event to `starrocks_audit_db__.starrocks_audit_tbl__` (kept 7 days). The hourly workflow `starrocks_audit` then runs two tasks:
  1. **bronze** reads the new staging rows through the platform's `lakehouse_starrocks.connect()`, from the bronze watermark minus 30 minutes, and appends them redacted;
  2. **silver** streams new bronze rows (`availableNow`, checkpoint), enriches them, deduplicates, and merges insert-only.
- **One StarRocks instance:** prod, uat and dev all pull the same real stream into their own namespace.
- **Shared logic** lives in `notebooks/common/audit_sql.py` (pure Python, standard library). It includes:
  - a lossless tokenizer;
  - credential redaction (`IDENTIFIED BY`, `SET PASSWORD`, secret-like properties);
  - literal masking, with comments removed;
  - seven statement categories and actor types (person, product writer, platform service);
  - data product and environment attribution from `<product>_lh`/`_lhuat`/`_lhdev` names;
  - table extraction and event keys.

  Later sources (RustFS, Airflow, Polaris) can reuse it.
- **Privacy:** credentials are redacted in bronze and silver. Gold views never select `sql_text` or `payload`. Analysts read only the prod MV database, so `audit.yaml` gives them `none` on the namespaces.
- **Retention:** nothing deletes bronze or silver rows. The slow/failed detail view keeps 30 days.

## Main changes
| Area | Paths | Behavior |
|---|---|---|
| Repository baseline (already on `main`/`uat`, commit `dd99258`) | README, AGENTS.md, tests, `requirements.txt` | Template leftovers (sample, tpch, duckdb) removed; renamed to the audit product |
| Shared logic | `notebooks/common/audit_sql.py` | Tokenizer, redaction, masking, categories, actors, scope, tables, event keys, row enrichment; bronze and silver column contracts |
| Bronze table | `notebooks/workload/starrocks/deploy/deploy_bronze_table.py`, `workflows/starrocks/starrocks_deploy.json` (manual) | Creates `starrocks_audit_event_fe_brz` (Iceberg v2, Parquet, `days(event_time)`) or checks it; never alters |
| Bronze pull | `notebooks/workload/starrocks/bronze/bronze_ingestion.py` | Watermark minus `lookback_minutes` (default 30, up to 10080); chunks of 20000; redacts; `copy_task = bronze_ingestion` |
| Silver | `notebooks/workload/starrocks/silver/silver_processing.py` | `starrocks_audit_event` (one row per event) and `starrocks_audit_table_access`, partitioned by `event_date`, insert-only MERGE |
| Workflow | `workflows/starrocks/starrocks_audit.json` | `20 * * * *`: `starrocks_audit_event_fe_brz_load`, then `starrocks_audit_event_load`; one retry each after 300 s; timeouts 1800 s and 2400 s |
| Gold | `materialized_views/starrocks/mv_starrocks_{user_day,product_day,table_day,slow_failed,login_day}.sql` | Hourly at :35, partitioned by `event_date`; masked only; slow is 60 s or more |
| Dev tool | `notebooks/workload/starrocks/ops/ops_generate_sample_events.py` | Optional synthetic audit rows, dev only |
| Configs | `configs/{dev,uat,prod}.yml` | `starrocks_audit_table` (no host or credentials) |
| Tests and tools | `tests/starrocks/`, `scripts/spark_smoke_starrocks.py` | 192 new tests (incl. 15 Hypothesis properties); Spark smoke script |
| Docs | `README.md`, `AGENTS.md` | `starrocks` subproject: tables, access, first deployment, dev, operations, limits |

## Verification
| Check | Result |
|---|---|
| `scripts/code_quality.sh all` (Ruff, Black, Flake8, Bandit) | PASS |
| `pytest` | PASS, 260 passed (seed 0) |
| Platform validator `lakehouse_dags.validate .` | PASS, 2 workflows, 5 materialized views |
| Python 3.10.12 (platform Spark image) | PASS |
| Spark smoke (Spark 3.5.9, Iceberg 1.10.2, local catalog, fake StarRocks) | PASS: 600 events, 600 distinct keys across a first load, a no-op run and a duplicate-only run; 512 table accesses; no credentials in bronze or silver |
| **Platform integration** (real staging table, Polaris, view DDL in StarRocks, access, UAT runs) | **NOT RUN**: needs the paired platform intent (below) |

Details: `aidlc-docs/current/construction/build-and-test/build-and-test-summary.md`.

## Deployment
Ordered steps are required: platform prerequisites first, then dev, UAT and production. See [deploy_instructions.md](deploy_instructions.md). CI deploys nothing. Airflow picks up the merged branches through the `audit-uat` and `audit-main` bundles, and the platform deploys the views with each bundle run.

## Risks and limitations
- **Depends on the paired platform intent:** `audit.yaml` applied; the AuditLoader plugin and 7-day staging table; `SELECT` on it for `srv-audit-writer` and the audit owner and developer roles; `lakehouse_starrocks.connect()`; StarRocks reachable with its CA from Spark runs and Jupyter. Until those exist, the bronze task fails at connect, and nothing reaches silver.
- **Open probes (P-1):**
  - whether AuditLoader on StarRocks 4.1.7 records connection events (if not, `mv_starrocks_login_day` stays empty);
  - the staging columns;
  - the FE time zone, since bronze `event_time` must be UTC.
- **View DDL not yet run in StarRocks:** `PARTITION BY event_date` on an Iceberg table, `partition_ttl`, and `COUNT(DISTINCT)` in a partitioned view. BR-42 lists the fallbacks.
- **Gaps:** gaps longer than the staging table's 7 days can't be recovered.
- **Table extraction is best effort:** a tokenizer, not a full SQL parser.
- **Silver's `events` log field** counts processed events, not inserted ones. A follow-up rename is suggested.
- **Rollback is limited:** data is append-only. Dropping tables needs an owner, because the writer can't drop. Silver can be rebuilt from bronze; bronze can only be rebuilt within the staging table's 7 days.
- **Personal data:** the audit log holds every SQL statement of the platform. Owners and developers of `audit` can read full statements, with credentials redacted.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
