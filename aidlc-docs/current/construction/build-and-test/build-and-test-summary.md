# Build and Test Summary — StarRocks Query Audit

Intent `20261125-starrocks-query-audit`, branch `starrocks-query-audit`, 2026-11-25. Instructions: [build](build-instructions.md), [unit](unit-test-instructions.md), [integration](integration-test-instructions.md), [performance](performance-test-instructions.md).

## Results
| Check | Scope | Result |
|---|---|---|
| Compile | `python -m compileall -q dags notebooks` | PASS |
| Code analysis | `scripts/code_quality.sh all`: Ruff, Black, Flake8 on `notebooks`, `scripts`, `tests`, `dags`; Bandit on `notebooks`, `scripts`, `dags` | PASS |
| Unit tests | `python -m pytest -q` (Hypothesis seed 0) | PASS, 260 passed |
| Platform validator | `lakehouse_dags.validate .` from `opensource-lakehouse-infra` | PASS, `OK: 2 workflow(s), 5 materialized view(s)` |
| Extras lock check | `requirements.txt` | PASS, no requirements |
| Python 3.10 | platform image, Python 3.10.12: parse, import, redaction | PASS |
| Spark smoke | `scripts/spark_smoke_starrocks.py` in `local/lakehouse-spark-runtime:3.5.9_1.10.2`, no network | PASS (details below) |
| Platform integration: bronze deploy in UAT | `audit-uat-starrocks_deploy`, run `manual__2026-11-25T18:54:34`, as `srv-audit-writer` through Polaris | PASS (2026-11-25): `created` `lakehouse_uat.audit_lhuat.starrocks_audit_event_fe_brz` at `s3a://bucket-audit-uat/lakehouse_uat/audit_lhuat/starrocks_audit_event_fe_brz` |
| Platform integration: bronze pull, silver, views, access, dev | needs the paired platform intent | NOT RUN: blocked |
| Performance | not required; watch points listed | N/A |

### Spark smoke details
Deploy `created` then `exists`; bronze partitioned `days(event_time)` at `.../lakehouse_dev/audit_lhdev/starrocks_audit_event_fe_brz`. Bronze run 1 (empty bronze): 600 rows in 3 chunks; run 2: from the watermark minus 30 minutes, 304 rows re-read (904 in bronze). Silver run 1: 904 rows read, **600 events, 600 distinct keys**, 512 table accesses, 0 unparsed; run 2: nothing new; run 3 after 304 more duplicates: still 600 events. Silver partitioned by `event_date`, schema equal to the contract. 0 fake passwords in bronze and silver. Categories: SELECT 256, SESSION 91, DML 63, DDL 62, METADATA 45, ADMIN 29; 54 connection events. Sample writer appended 11 rows.

## Findings
1. **Silver summary field `events` counts processed events, not inserted ones** (run 3 logs `events: 304`, none inserted). Harmless; suggest renaming it `events_processed` (and `table_accesses_processed`) in a follow-up, or reading the MERGE metrics.
2. **Reading silver from the same Spark session after the stream needs `REFRESH TABLE`**: the outer session caches tables it created (the template's notebook noted the same). The notebooks never read silver back; the smoke script refreshes before counting.
3. **`data-products/audit.yaml` (platform repository)** gave analysts `read` on the prod namespace; at the user's request it now says `none` with the justification (FR-6.2) in comments. Not yet committed or applied in the platform repository.
4. **Platform images can disappear**: Rancher Desktop deleted `local/lakehouse-spark-runtime` and `local/lakehouse-jupyter-singleuser` under disk pressure during this intent; the Spark image was rebuilt (from cache) for these tests; the Jupyter image is still missing and is rebuilt by `scripts/lakehouse.sh start`.

## Extension compliance
| Extension | Status |
|---|---|
| Property-Based Testing (Partial) | PBT-02 compliant (tokenizer and scope round trips); PBT-03 compliant (11 invariant properties); PBT-07 compliant (domain generators: SQL grammar, payloads, user names, staging rows); PBT-08 compliant (fixed, printed, overridable seed; `print_blob`); PBT-09 compliant (Hypothesis, in `requirements-dev.txt`). Advisory PBT-04 idempotence properties included. |
| Security Baseline | Disabled (N/A) |
| Resiliency Baseline | Disabled (N/A) |
| Workflow Conventions | DOC-01 to DOC-04, APG, AUD and QT rules followed; see `audit.md` |

## Canonical knowledge reconciliation (for closure)
Matches the approved inventory: UPDATE `knowledge/opensource-lakehouse/application-design.md`, `functional-design/workload-foundation.md`, `functional-design/jenkins-deployment.md`; CREATE `knowledge/starrocks/application-design.md`, `knowledge/starrocks/functional-design/query-audit.md`; RETIRE `knowledge/tpch/`. No new path.

## Overall
Local verification **passed**. Platform integration is **not verified** until the paired platform intent delivers; closing the intent before then would need an explicit verification waiver naming item 3 of the integration instructions.
