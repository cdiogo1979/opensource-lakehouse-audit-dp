# AI-DLC State Tracking

## Project Information
- Project Type: Brownfield
- Start Date: 2026-11-25T17:17:31Z
- Current Stage: CONSTRUCTION - Build and Test (awaiting approval)
- Intent: Build the first source of the audit and observability data product: every query executed in StarRocks captured, stored as Iceberg tables in the audit product's namespaces, and made queryable.
- Intent ID: 20261125-starrocks-query-audit
- Previous Intent: aidlc-docs/archive/20261112-sample-dependency-alerts (closed in the template repository `opensource-lakehouse-sample-dp`; its archive was removed from this working tree)

## Workspace State
- **Existing Code**: Yes. Python (Jupytext notebooks, helpers, tests), workflow JSON, YAML configs. No build system beyond `pyproject.toml` and `scripts/code_quality.sh`.
- **Workspace Root**: /Users/carlosdiogo/code_repo/opensource-lakehouse-audit-dp
- **Origin**: a copy of `opensource-lakehouse-sample-dp` being converted into the `audit` data product (branch `dependency-alerts`, uncommitted).
- **Reverse Engineering Needed**: No. Canonical knowledge `aidlc-docs/knowledge/opensource-lakehouse/` (repository-level design, `workload-foundation`, `jenkins-deployment`) describes the shared foundation; the deltas below are recorded instead.
- **Related repository**: `/Users/carlosdiogo/code_repo/opensource-lakehouse-infra` (the platform). Platform-side work this intent depends on is tracked there as its own intent.

### Workspace findings (recorded at Workspace Detection)
1. `configs/{dev,uat,prod}.yml` already target `audit_lhdev` / `audit_lhuat` / `audit_lh` and `bucket-audit{-dev,-uat,}` (uncommitted).
2. The sample and tpch notebooks, tests, materialized views, `knowledge/tpch/` and all archives are deleted (uncommitted), but `workflows/sample/*.json` and `workflows/tpch/tpch_medallion.json` remain and point at deleted entry points, so `lakehouse-validate-workflows .` (CI) would fail.
3. `git remote origin` still points at `opensource-lakehouse-sample-dp.git`. — Resolved 2026-11-25 (outside the intent's stages): the copied history was moved out and a fresh repository initialized on `main`, no commits yet; `origin` set to `https://github.com/cdiogo1979/opensource-lakehouse-audit-dp.git` (empty).
4. Canonical knowledge still names `opensource-lakehouse-sample-dp`, the `tpch` subproject and `~/work/opensource-lakehouse-sample-dp` in the Jupyter Initialization cell.
5. The platform has no `audit` data product yet (`opensource-lakehouse-infra/data-products/` holds only `default.yaml`).
6. Product Spark runs receive only Polaris-vended, table-scoped storage credentials and checkpoint keys: they cannot read raw files or StarRocks internal tables directly. StarRocks (4.1.7) audit is not configured (`fe.conf` sets only `sys_log_level`); `fe.audit.log` is written by default inside the FE container.

## Code Location Rules
- **Application Code**: Workspace root (NEVER in aidlc-docs/)
- **Documentation**: aidlc-docs/ only
- **Structure patterns**: AGENTS.md (subproject folders under `workflows/`, `notebooks/workload/`, `materialized_views/`, `aidlc-docs/knowledge/`)

## Stage Progress
### INCEPTION PHASE
- [x] Workspace Detection — brownfield; previous intent closed; new intent opened (2026-11-25T17:17:31Z).
- [x] Reverse Engineering — SKIPPED: canonical knowledge present; workspace deltas recorded above.
- [x] Requirements Analysis — approved 2026-11-25 (approve-continue).
- [x] User Stories — SKIPPED (personas are the standard product groups).
- [x] Workflow Planning — approved 2026-11-25 (approve-continue).
- [x] Application Design — approved 2026-11-25 (approve-continue).
- [x] Units Generation — SKIPPED: Unit 1 `repo-identity`, Unit 2 `starrocks`.

### CONSTRUCTION PHASE
- [x] Unit 1 `repo-identity`: Code Generation — approved 2026-11-25; commit `dd99258` on `main` and `uat`
- [ ] Unit 2 `starrocks` (branch `starrocks-query-audit`): Functional Design approved 2026-11-25; Code Generation approved 2026-11-25 (including the pull-model change request)
- [x] NFR Requirements, NFR Design, Infrastructure Design — SKIPPED
- [ ] Build and Test — local verification PASS (unit 260, code quality, validator, Python 3.10, Spark smoke); platform integration blocked by the paired intent; awaiting approval

### DEPLOY PHASE
- [ ] Deploy handoff

## Extension Configuration
| Extension | Enabled | Decided At |
|---|---|---|
| Security Baseline | No | Requirements Analysis |
| Resiliency Baseline | No | Requirements Analysis |
| Property-Based Testing | Partial (PBT-02, -03, -07, -08, -09 enforced; others advisory) | Requirements Analysis |
| Workflow Conventions | Yes (always enforced) | Workflow start |

## Intent Lifecycle
<!-- intent-lifecycle:start -->
```json
{
  "current_stage": "CONSTRUCTION - Build and Test",
  "intent_id": "20261125-starrocks-query-audit",
  "name": "StarRocks Query Audit",
  "previous_intent": "aidlc-docs/archive/20261112-sample-dependency-alerts",
  "status": "ACTIVE"
}
```
<!-- intent-lifecycle:end -->
