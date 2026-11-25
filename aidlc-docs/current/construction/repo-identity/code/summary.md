# Code Summary — Unit 1 `repo-identity`

Intent `20261125-starrocks-query-audit`. Plan: [repo-identity-code-generation-plan.md](../../plans/repo-identity-code-generation-plan.md).

## Files
| Change | Path |
|---|---|
| Deleted | `workflows/sample/test_environment.json`, `workflows/sample/generate_tpch.json`, `workflows/tpch/tpch_medallion.json` (and their folders) |
| Created | `workflows/.gitkeep` (keeps `workflows/` until Unit 2 adds `workflows/starrocks/`) |
| Deleted | `tests/workload_foundation/test_generate_tpch.py`, `tests/workload_foundation/test_init_environment.py`, `tests/jenkins_deployment/` (only `__pycache__`) |
| Modified | `tests/workload_foundation/test_config_loader.py` (gained the nested-settings test from `test_init_environment.py`; `sample_db` became `example_db`) |
| Modified | `tests/workload_foundation/test_properties.py` (removed the sample-write property and its fakes; config properties kept) |
| Modified | `tests/workload_foundation/test_jupyter_scripts.py` (Initialization path `~/work/opensource-lakehouse-audit-dp`; example paths under `starrocks/`; the subproject-folder test allows zero subprojects until Unit 2) |
| Modified | `tests/workload_foundation/test_job_runtime.py`, `test_task_arguments.py` (incidental `generate_tpch` names became `silver_processing`) |
| Modified | `requirements.txt` (removed `duckdb==1.5.6`; no packages) |
| Modified | `AGENTS.md` (audit product, `starrocks` layout and naming examples, Initialization path; rules unchanged) |
| Modified | `README.md` (audit product introduction and environment table, layout, Subprojects section replacing the tpch section, DuckDB/TPC-H mentions removed, names `audit`) |
| Modified | `configs/dev.yml`, `configs/uat.yml`, `configs/prod.yml` (header comments; values unchanged) |

## Deviations from the plan (recorded per DOC-02)
- `configs/prod.yml` header also fixed (it said "Hive catalog namespace"); the plan listed dev and uat only.
- The local `.venv` (copied with the template) had tool scripts pointing at a missing interpreter (`../opensource-lakehouse/.venv/bin/python`), so Black, Flake8 and Bandit could not start. Rebuilt with `python3 -m venv --clear .venv` and `pip install -r requirements-dev.txt` (local only; ignored by git).
- `test_workflows_and_notebooks_live_in_matching_subproject_folders` no longer requires at least one subproject; Unit 2 restores that requirement once `starrocks` exists.
- `README.md` keeps the `pytpch` wheel example under "Python packages": it illustrates the platform's wheel rule, not the deleted subproject.

## Verification (2026-11-25)
| Check | Result |
|---|---|
| `python -m compileall -q dags notebooks` | PASS |
| `scripts/code_quality.sh all` (Ruff, Black, Flake8, Bandit) | PASS |
| `python -m pytest -q` | PASS, 68 passed |
| `lakehouse_dags.validate .` (platform repository's validator, run locally) | PASS, `OK: 0 workflow(s)` |
| `workload_python.py check-extras requirements.txt` (platform lock) | PASS, no requirements |
| Leftover search `sample-dp`, `tpch`, `duckdb` (outside `aidlc-docs/`, tooling folders) | Only the `pytpch` wheel example in README (kept on purpose) |
