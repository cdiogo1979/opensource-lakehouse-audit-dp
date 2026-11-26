# Code Generation Plan — Unit 1 `repo-identity`

Intent `20261125-starrocks-query-audit`. This plan is the single source of truth for Unit 1's Code Generation. Requirements: FR-8 (template cleanup), NFR-5 (repository rules). No functional design (cleanup only).

## Unit context
- **Workspace root**: `/Users/carlosdiogo/code_repo/opensource-lakehouse-audit-dp` (fresh git repository, `origin` = `https://github.com/cdiogo1979/opensource-lakehouse-audit-dp.git`, empty, no commits).
- **Why**: the repository is a copy of `opensource-lakehouse-sample-dp`. The sample and tpch notebooks are deleted, but their workflows, tests and documentation remain, so CI fails today (`pytest`: `ModuleNotFoundError: generate_tpch`, and the imports of the deleted `test_environment` notebook).
- **Depends on**: nothing. **Unit 2 depends on it** for a green baseline.
- **Out of scope**: `dags/lakehouse.py` (platform stub, never edited); `notebooks/common/` code (unchanged); canonical knowledge under `aidlc-docs/knowledge/` (curated at intent closure, as the compaction rules require; the inventory in the execution plan lists the updates).

## Steps

### Step 1 — Remove the stale workflows (FR-8.1)
- [x] Delete `workflows/sample/test_environment.json`, `workflows/sample/generate_tpch.json`, `workflows/tpch/tpch_medallion.json` and their folders. `workflows/` stays, with a `.gitkeep`, until Unit 2 adds `workflows/starrocks/`.

### Step 2 — Retarget the foundation tests (FR-8.1)
- [x] Delete `tests/workload_foundation/test_generate_tpch.py` (tests a deleted notebook).
- [x] `tests/workload_foundation/test_init_environment.py`: remove the tests of the deleted sample notebook (`write_sample_dataset`, `SAMPLE_*`); keep its config-loader checks, moved into `test_config_loader.py` if no sample code remains in the file, then delete the file.
- [x] `tests/workload_foundation/test_properties.py`: remove the sample-write property (`test_repeated_sample_writes_replace_only_the_sample_table`) and its import; keep every config and loader property.
- [x] `tests/workload_foundation/test_jupyter_scripts.py`: the Initialization line becomes `# !lakehouse-install-requirements ~/work/opensource-lakehouse-audit-dp`; example paths use `starrocks/silver/silver_processing.py` instead of `sample/...` and `generate_tpch.py`.
- [x] `tests/workload_foundation/test_job_runtime.py`, `test_task_arguments.py`, `test_config_loader.py`: replace the incidental `generate_tpch` / `tpch` / `sample` names in strings with neutral ones; no behavior change.
- [x] Remove the empty `tests/jenkins_deployment/` folder (only `__pycache__` is left).

### Step 3 — Requirements (FR-8.1)
- [x] `requirements.txt`: remove `duckdb==1.5.6` (used only by the deleted TPC-H notebook); keep the header comment, so the file holds no packages.

### Step 4 — AGENTS.md (FR-8.2, NFR-5)
- [x] Replace `opensource-lakehouse-sample-dp` with `opensource-lakehouse-audit-dp` (the Initialization cell rule).
- [x] Rewrite the subproject layout example and the naming-convention examples from `sample` and `tpch` to this repository's subproject `starrocks` (marked as delivered by this intent), keeping every rule unchanged.
- [x] Add one line under "What belongs here" saying this repository is the `audit` data product.

### Step 5 — README.md (FR-8.2)
- [x] Title and introduction: the audit and observability data product (`audit`), its purpose (one source per subproject, starting with StarRocks queries), its namespaces, buckets and MV databases.
- [x] Replace the `default` product and bundle names in examples with `audit` (`audit-main`, `audit-uat`, `audit_lh`, `bucket-audit`, ...).
- [x] Replace "Repository layout" and "The tpch subproject" with the audit layout and a "Subprojects" section (`starrocks`: planned in intent `20261125-starrocks-query-audit`; filled in by Unit 2).
- [x] Remove the DuckDB and TPC-H mentions in "Python packages", "How-to" and "Limits"; keep the platform-contract sections (how a run works, workflow JSON reference, DAG stub, entry points, configs, Jupyter, CI, materialized views, security alerts) with only the names changed.

### Step 6 — Configs (FR-8.2)
- [x] `configs/dev.yml`, `configs/uat.yml`: replace the template's "Replace these conspicuous placeholders ..." comments with plain descriptions; values already target `audit_*` and `bucket-audit*` (unchanged).

### Step 7 — Verify (NFR-5)
- [x] `python -m compileall -q dags notebooks`
- [x] `scripts/code_quality.sh all` (Ruff, Black, Flake8, Bandit)
- [x] `python -m pytest -q` (all green; Hypothesis seed printed)
- [x] `lakehouse-validate-workflows .` from the platform repository (`opensource-lakehouse-infra/infra/jenkins/bin/`) if it runs outside Jenkins; otherwise recorded as not run
- [x] `grep -ri "sample-dp\|tpch\|duckdb"` over the repository (excluding `aidlc-docs/` and `.venv`) returns nothing

### Step 8 — Documentation summary
- [x] `aidlc-docs/current/construction/repo-identity/code/summary.md`: files modified, created and deleted, and the verification results.

### Step 9 — First commit and push (FR-8.4, user decision)
- [x] `git add -A`; one commit on `main`: "Audit data product: repository baseline from the lakehouse template" (with the attribution line).
- [x] Create `uat` from the same commit.
- [x] `git push -u origin main uat`.
- Done 2026-11-25: commit `dd99258` on `main` and `uat`, pushed to `origin`.
- **Approving this plan authorizes this step** (an outward-facing push to `https://github.com/cdiogo1979/opensource-lakehouse-audit-dp`). It runs only after Step 7 is green.

### Step 10 — Added during generation (DOC-02)
- [x] `configs/prod.yml` header comment fixed as well (said "Hive catalog namespace").
- [x] Local `.venv` rebuilt (`python3 -m venv --clear .venv`, `pip install -r requirements-dev.txt`): the copied venv's tool scripts pointed at a missing interpreter.
- [x] Subproject-folder test relaxed to allow zero subprojects until Unit 2 (Unit 2 restores the non-empty check).

## Traceability
| Requirement | Steps |
|---|---|
| FR-8.1 | 1, 2, 3 |
| FR-8.2 | 4, 5, 6 |
| FR-8.4 | 9 |
| NFR-5 | 4, 7 |
