# Build Instructions — StarRocks Query Audit

Intent `20261125-starrocks-query-audit`. This repository has no compiled artifact: "building" means installing the test tools, compiling the Python, running the code-quality script, and validating the workflow and view files with the platform's rules.

## Prerequisites
- Python 3 on the host (tests ran on 3.14; the platform runs the notebooks on Python 3.10).
- `requirements-dev.txt` (Black, Ruff, Flake8 with Flake8-pyproject, Bandit, pytest, Hypothesis, PyYAML).
- For the workflow validator: a checkout of `opensource-lakehouse-infra` (its `infra/airflow/lakehouse_dags`).
- For the Spark smoke test and the Python 3.10 check: Docker and the platform image `local/lakehouse-spark-runtime:3.5.9_1.10.2` (built by `scripts/lakehouse.sh start-airflow`, or alone with `docker build -t local/lakehouse-spark-runtime:3.5.9_1.10.2 -f infra/spark-runtime/Dockerfile .` in the platform repository).
- No environment variables, credentials or endpoints are needed.

## Steps
1. Install the tools:
   ```bash
   python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
   ```
2. Compile (as Jenkins does):
   ```bash
   .venv/bin/python -m compileall -q dags notebooks
   ```
3. Code analysis, same script and scope as the `Jenkinsfile` (`notebooks`, `scripts`, `tests`, `dags`; Bandit on `notebooks`, `scripts`, `dags`):
   ```bash
   PATH=".venv/bin:$PATH" scripts/code_quality.sh all
   ```
4. Validate workflows, views and the stub with the platform's rules (Jenkins runs `lakehouse-validate-workflows .`):
   ```bash
   PYTHONPATH=<platform>/infra/airflow .venv/bin/python -m lakehouse_dags.validate .
   ```
   Expected: `OK: 2 workflow(s), 5 materialized view(s)`.
5. `requirements.txt` holds no packages, so the lock check (`workload_python.py check-extras`) reports `OK: requirements.txt has no requirements`.

## Troubleshooting
- **Black, Flake8 or Bandit "bad interpreter"**: the `.venv` was copied from another folder; rebuild it with `python3 -m venv --clear .venv` and reinstall.
- **`No module named lakehouse_dags`**: run the validator without `python -I` and with `PYTHONPATH` pointing at the platform's `infra/airflow`.
- **Spark image missing**: Rancher Desktop may delete unused images when its disk fills; rebuild it as above.
