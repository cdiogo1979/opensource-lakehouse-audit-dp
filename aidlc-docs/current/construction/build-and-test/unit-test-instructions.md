# Unit Test Execution — StarRocks Query Audit

## Run
```bash
.venv/bin/python -m pytest -q
```
The Hypothesis seed is fixed (`HYPOTHESIS_SEED`, default `0`) and printed in the captured output (PBT-08); set `HYPOTHESIS_SEED=<n>` to explore other examples, and use the printed `@reproduce_failure` blob to replay a failure.

## Expected result
260 passed, 0 failures:

| Suite | Tests |
|---|---|
| `tests/starrocks/test_audit_sql.py` (examples, BR-1 to BR-34) | 98 |
| `tests/starrocks/test_audit_sql_properties.py` (Hypothesis: PBT-02, PBT-03, advisory PBT-04) | 15 |
| `tests/starrocks/test_bronze_ingestion.py` | 19 |
| `tests/starrocks/test_silver_processing.py` | 13 |
| `tests/starrocks/test_deploy_bronze_table.py` | 9 |
| `tests/starrocks/test_ops_generate_sample_events.py` | 12 |
| `tests/starrocks/test_workflows.py` | 5 |
| `tests/starrocks/test_materialized_views.py` | 21 |
| `tests/workload_foundation/` | 68 |

No coverage target is configured in this repository; coverage is by rule (every BR has example tests) and by property (functional design, Testable Properties).

## If a test fails
Read the pytest output (and the Hypothesis falsifying example), fix the code or the rule, rerun the suite, and record the change in the code-generation plan (DOC-02).
