# Application Design — opensource-lakehouse-audit-dp (repository level)

This repository is the **`audit` data product** of the `opensource-lakehouse-infra` platform: the lakehouse's audit and observability data, one subproject per audited source. It holds workflow declarations, Spark workloads (Jupytext notebooks), shared audit logic, StarRocks materialized views, non-secret configuration and tests; the platform builds and runs the DAGs. Subproject design: [`../starrocks/application-design.md`](../starrocks/application-design.md). It started as a copy of the `opensource-lakehouse-sample-dp` template (sample and tpch subprojects removed, 2026-11-25). History: the [StarRocks Query Audit intent archive](../../archive/20261125-starrocks-query-audit/intent-summary.md).

## Platform contract
| This repository | The platform (`opensource-lakehouse-infra`) |
|---|---|
| `workflows/<project>/<name>.json` (workflow JSON v1) | The `lakehouse_dags` factory builds `<bundle>-<name>` DAGs; Git bundles `audit-main` (prod) and `audit-uat` (uat) from `data-products/audit.yaml` |
| `notebooks/workload/<project>/<layer>/*.py` | One Spark container per DAG run, the bundle mounted read-only; `spark-submit` per task |
| `notebooks/common/` helpers, including `audit_sql.py` | `lakehouse_spark.create_spark_session` (catalogs, token, redaction, checkpoint keys); `lakehouse_starrocks.connect()` (StarRocks over TLS: the writer's token in Airflow, the user's password in Jupyter) |
| `configs/<env>.yml` | The writer token per run; Polaris-vended storage credentials; checkpoint keys; the StarRocks CA and address |
| `materialized_views/<project>/*.sql` | Deployed to UAT and prod as the writer at the start of each bundle run |
| `dags/lakehouse.py` (fixed stub) | The CI validator `lakehouse-validate-workflows`, the shared Python 3.10 lock |
| `requirements.txt` (no extras today) | Installed per Airflow run and with `lakehouse-install-requirements` in Jupyter |
| reads `starrocks_audit_db__.starrocks_audit_tbl__` | AuditLoader capture into that 7-day staging table; `platform_sources.starrocks_audit` grants |

## Product
- Namespaces `audit_lh`, `audit_lhuat`, `audit_lhdev`; buckets `bucket-audit`, `-uat`, `-dev`; MV databases named like the namespaces; writer `srv-audit-writer`; groups `role-audit-owner`, `-developer`, `-analyst`, `-writer`.
- Access (`data-products/audit.yaml`): owners and developers read the namespaces (full SQL text); analysts have `none` on every namespace and read only the prod MV database (masked views); `platform_sources.starrocks_audit` read for owner, developer and writer.

## Subprojects and layers
- Each subproject has the same folder name under `workflows/`, `notebooks/workload/`, `materialized_views/` and `aidlc-docs/knowledge/`: today `starrocks` (layers `bronze/`, `silver/`, `deploy/`, `ops/`).
- Notebook layer folders: `bronze/`, `silver/`, `gold/`, `ops/`, `deploy/`, `other/`, one level deep; a workflow's `entrypoint` is relative to `notebooks/workload/<project>/`.
- DAG IDs don't include the subproject, so workflow names are unique across the repository.
- Shared, source-independent logic lives in `notebooks/common/audit_sql.py` (pure Python), reusable by later sources (RustFS, Airflow, Polaris, lineage).

## Naming conventions (AGENTS.md)
Notebooks `<layer>_<action>.py`; bronze tables `<project>_<entity>_<source>_brz`; silver tables `<project>_<entity>`; table locations `<storage_path>/<catalog_name>/<database_name>/<table>`; workflows `<project>_<purpose>`; tasks `<target_table>_load`; checkpoints `<checkpoint_path>/<project>/<target_table>`; views `mv_<project>_<purpose>` or named for an aggregate's dimensions.

## Notebook standard
Title cell (purpose, inputs, effects); `## Initialization` (`# !lakehouse-install-requirements ~/work/opensource-lakehouse-audit-dp`, `# %run ../../../common/jupyter_environment_resolver.py`); `## Imports`; optional `## Task arguments` (`TASK_ARGS_DEFINITION`, `TASK_ARGS_VALUES`); the job's own sections; `## Execution` with only the `if __name__ == "__main__":` block, which stops Spark in `finally:`. A test checks the structure.

## Environments
`prod` (bundle `audit-main`), `uat` (`audit-uat`), `dev` (Jupyter only). Configs need `storage_path`, `database_name`, `catalog_name`, `checkpoint_path: s3ckpt://<bucket>/checkpoints`, and `starrocks_audit_table`. There is one StarRocks instance, so every environment pulls the same real audit stream into its own namespace. No secrets or endpoints anywhere in the repository.

## Security alerts
Dependabot alerts cover `requirements.txt` and `requirements-dev.txt`; the shared environment and images are covered in the platform repository. Platform packages are never pinned or overridden here.

## Operational limits
- A DAG run's tasks share one Spark container: `starrocks_audit` runs its two tasks one after the other.
- A Jupyter server has 2 GiB: one notebook with a Spark session at a time.
- The product writer can't drop tables or create namespaces.
