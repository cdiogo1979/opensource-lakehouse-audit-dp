# Requirements — StarRocks Query Audit

Intent `20261125-starrocks-query-audit`. Depth: **Standard**. Answers: [requirement-verification-questions.md](requirement-verification-questions.md), [requirement-clarification-questions.md](requirement-clarification-questions.md).

## Intent analysis
| Item | Value |
|---|---|
| User request | "usind the folder opensource-lakehouse-audit-dp , lets start a new intent using the folder rules , for the first solution - starrocks audit" |
| Context | First source of a new audit and observability data product, which will later also cover RustFS, Airflow, Polaris and Spark lineage |
| Request type | New Feature (new data product subproject), plus cleanup of template leftovers |
| Scope | Multiple components in this repository; depends on a paired platform intent in `opensource-lakehouse-infra` |
| Complexity | Moderate |

## Context and constraints
- This repository becomes the `audit` data product: namespaces `audit_lh` / `audit_lhuat` / `audit_lhdev`, buckets `bucket-audit` / `-uat` / `-dev`, MV databases named like the namespaces (already set in `configs/`).
- Product Spark runs only receive Polaris-vended table credentials and checkpoint keys: they cannot read `fe.audit.log` or StarRocks internal tables. The StarRocks side of the pipeline is therefore platform work (Q1:A, Q8:A).
- Access is granted per namespace and per MV database, not per table or column.

## Functional requirements

### FR-1 Capture (platform, paired intent)
- FR-1.1 The platform installs StarRocks' AuditLoader plugin so that every audit event is written to a StarRocks **staging table**.
- FR-1.2 In scope (Q2:C): query statements (SELECT, DML, DDL) from every client; connection events (successful and failed logins); platform activity (materialized-view refreshes, the reconcilers' grants and user changes, loads), which arrive as statements by `root`, `srv-*` users and the MV scheduler.
- FR-1.3 Staging rows are deleted after **7 days** (partition time-to-live) (Clarification 2:A).

### FR-2 Transfer into the data product (platform, paired intent)
- FR-2.1 A scheduled StarRocks task copies new staging rows **every hour** into the audit product's Iceberg bronze table in each environment that is enabled (Q1:A, Q5:B).
- FR-2.2 The copy is safe to re-run: each hourly run copies the last 2 hours of staging rows, so a re-run or a missed hour loses no rows. Bronze may hold the resulting duplicates; silver removes them by event key (changed at Application Design, Q2:B).
- FR-2.3 Production and UAT receive real copies (one task each); dev gets synthetic rows from an `ops/` notebook (decided at Application Design, Q7:A).

### FR-3 Bronze (this repository)
- FR-3.1 A new subproject `starrocks` (folders under `workflows/`, `notebooks/workload/`, `materialized_views/`, `aidlc-docs/knowledge/`).
- FR-3.2 Bronze table `starrocks_audit_event_fe_brz` (naming `<project>_<entity>_<source>_brz`) holds the raw audit row unchanged as a JSON payload (including the full SQL text), plus `query_id`, `event_time` and load metadata. The platform task fills it; this repository creates it with its declared schema (a `deploy/` notebook) so the table definition lives here (refined at Application Design, Q1:B).

### FR-4 Silver (this repository)
- FR-4.1 An hourly workflow `starrocks_audit` merges new bronze rows into silver `starrocks_audit_event` (one table, `event_type` query or connection) and `starrocks_audit_table_access` (per referenced table), deduplicated by event key (refined at Application Design, Q4:A).
- FR-4.2 Silver normalizes the fields: event time (UTC), event type (query or connection), user, client IP, catalog and database, statement, state and error code, duration, scanned rows and bytes, returned rows, CPU and memory if available, query ID, digest, and the FE host.
- FR-4.3 Silver derives:
  - **masked SQL text**: every literal (string, number, date, IN-lists) replaced with a placeholder;
  - **statement category**: SELECT, DML, DDL, GRANT/user administration, other;
  - **actor type**: person, product writer (`srv-<product>-writer`), platform service (`srv-starrocks`, `root`, the reconciler, the MV scheduler);
  - **data product**: inferred from the namespace or MV database named in the statement or session (`<token>_lh`, `<token>_lhuat`, `<token>_lhdev`) where possible, otherwise unknown;
  - **environment**: prod, uat or dev, from the catalog or database.
- FR-4.4 Silver keeps the full SQL text alongside the masked text (Q3:A, Clarification 1:A).

### FR-5 Gold (this repository)
- FR-5.1 StarRocks materialized views in the product's MV database (`materialized_views/starrocks/`), refreshed hourly, reading silver only (Q7:C):
  - usage per user per day (queries, failures, scanned bytes, duration);
  - usage per data product and environment per day;
  - usage per table per day, where the table can be determined;
  - slow and failed queries (detail with masked SQL only);
  - login activity per user per day (successes and failures).
- FR-5.2 No gold view exposes the full SQL text; detail views expose only the digest and masked text.

### FR-6 Access (platform product file, paired intent)
- FR-6.1 The `audit` product follows the standard group model (Q4): `role-audit-owner`, `role-audit-developer`, `role-audit-analyst`, `role-audit-writer`.
- FR-6.2 Owners and developers read the namespace (full SQL text). Analysts have `none` on the production namespace and `read` on the production MV database (masked views only) (Clarification 1:A).
- FR-6.3 The rest of the product file follows the platform defaults unless Application Design finds a reason to differ.

### FR-7 Retention
- FR-7.1 Iceberg tables in the data product keep every row: no expiry or delete job (Q6).
- FR-7.2 Iceberg snapshot expiry and compaction (table maintenance, not row deletion) may be added if needed for size; they must not remove rows.

### FR-8 Template cleanup (this repository, Q7:C)
- FR-8.1 Remove `workflows/sample/` and `workflows/tpch/` (their entry points are already deleted) so CI validates.
- FR-8.2 Replace references to `opensource-lakehouse-sample-dp` (README, AGENTS.md, notebook Initialization cell path, Jenkins and test references) with this repository's name, and rewrite the README for the audit product.
- FR-8.3 Refresh canonical knowledge at closure: the repository-level design names this repository and the `starrocks` subproject; `knowledge/starrocks/` is created.
- FR-8.4 Git: the repository was re-initialized with `origin` = `https://github.com/cdiogo1979/opensource-lakehouse-audit-dp.git` (empty). No baseline commit: the first push of `main` and `uat` follows this cleanup, so the first published state passes CI (user decision, 2026-11-25).

## Non-functional requirements
- NFR-1 Freshness: production data available in silver and gold within about 1 hour plus one run of the event time (Q5:B).
- NFR-2 Correctness: no lost audit rows across re-runs, retries and catch-up, and no duplicated rows in silver and gold (bronze may hold overlap duplicates) (FR-2.2, FR-4.1).
- NFR-3 Resource use: the hourly run fits the platform's single Spark container per DAG run; tasks ordered with `runs_after`.
- NFR-4 Privacy: full SQL text only where FR-6.2 allows; masked text must not leak literals (tested, see NFR-6).
- NFR-5 Rules of this repository (AGENTS.md): workflow JSON only (no DAG code), notebook structure and naming, configs without secrets, Black/Ruff/Flake8/Bandit clean, README and AGENTS.md kept current.
- NFR-6 Testing: example-based tests for every notebook, workflow and MV intent; property-based tests (Hypothesis, already used in this repository) for pure functions such as SQL literal masking, statement categorisation and product inference (PBT partial: PBT-02, -03, -07, -08, -09 enforced).

## Extensions
| Extension | Decision |
|---|---|
| Security Baseline | No (opted out); privacy needs are covered by FR-6 and NFR-4 |
| Resiliency Baseline | No (opted out); re-run safety is covered by NFR-2 |
| Property-Based Testing | Partial |

## Dependencies and open points
- **D-1 Paired platform intent** in `opensource-lakehouse-infra` (Q8:A): `data-products/audit.yaml` (FR-6), AuditLoader plugin and staging table with a 7-day time-to-live (FR-1), the hourly StarRocks copy task and the identity it runs as (FR-2), and any grant it needs. Until it is delivered, this repository can be developed and tested against fixtures, and in dev with sample rows.
- **P-1 To verify in the paired intent**: whether the AuditLoader plugin for StarRocks 4.1.7 records connection events as well as queries (FR-1.2); whether StarRocks' scheduled tasks (`SUBMIT TASK ... SCHEDULE`) can write to an Iceberg table in a Polaris catalog; which user the task runs as and how that identity is authorized to write to `audit_lh`.
- **P-2** Determining the data product of a query is best-effort (FR-4.3): fully qualified names and the session database are used; unqualified names in other contexts may stay unknown.
- **P-3** StarRocks' own query cache and data cache do not affect this source: every query is audited by the FE regardless of caching.
