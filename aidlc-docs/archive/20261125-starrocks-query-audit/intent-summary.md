# Intent Summary: StarRocks Query Audit

## Intent
StarRocks Query Audit (`20261125-starrocks-query-audit`)

## Objective
First source of the audit data product: every StarRocks query, login and platform action pulled hourly into Iceberg bronze and silver tables of the audit product, with masked gold views; plus the conversion of the repository from the sample template.

## Date
2026-11-26

## Scope
Included: Unit 1 repo-identity (template cleanup, new repository and remote), Unit 2 starrocks (audit_sql, bronze deploy and pull, silver, five views, dev samples, workflows, tests, smoke script), the pull-model and disconnection change requests, deployment to uat and production. Excluded: platform capture, grants and connection helper (paired intent 20261125-starrocks-audit-capture in opensource-lakehouse-infra); dev notebook run in Jupyter (same code verified in uat and production); the silver log field rename.

## Units / workstreams
Unit 1 repo-identity, Unit 2 starrocks

## Major requirements
- Capture queries, logins and platform activity of the one StarRocks instance into the audit product, hourly, kept indefinitely
- Full SQL for owners and developers only; analysts read masked views in the prod MV database
- Credentials redacted; literals masked; seven statement categories; actor types; product and environment attribution
- Repository converted from the sample template to opensource-lakehouse-audit-dp

## Architecture and design decisions
- Pull model: a bronze task reads the AuditLoader staging table through lakehouse_starrocks.connect()
- Bronze stores the staging row as canonical JSON; lookback duplicates removed by silver's event key
- One event table (event_type query, connection, disconnection, unparsed) plus a table-access table; partitioned by event_date
- Pure audit logic in notebooks/common/audit_sql.py; own tokenizer; driver-side enrichment
- Five masked hourly views; slow is 60 s or more; slow/failed keeps 30 days

## Implementation decisions
- Insert-only MERGE; availableNow stream with checkpoint
- Chunks of 20000 rows in bronze and silver
- Spark smoke script for real Spark and Iceberg checks

## Deviations
- Change request 2026-11-25: push model (platform copy task) replaced by the pull model; dev pulls real data too
- Change request 2026-11-26: queryType disconnection gets its own event type
- Dev notebooks not run end to end in Jupyter (uat and production verified instead)

## Verification
construction/build-and-test/build-and-test-summary.md: pytest 261 passed, code_quality all PASS, validator OK (2 workflows, 5 views), Python 3.10 OK, Spark smoke PASS; platform integration 2026-11-26: uat 728 events = 728 keys and production 1068 = 1068, 0 unparsed, 0 unredacted credentials, five views active in both, analysts limited to the views; Jupyter connection verified by the user.

## Canonical documents updated
- aidlc-docs/knowledge/opensource-lakehouse/application-design.md
- aidlc-docs/knowledge/opensource-lakehouse/functional-design/workload-foundation.md
- aidlc-docs/knowledge/opensource-lakehouse/functional-design/jenkins-deployment.md
- aidlc-docs/knowledge/starrocks/application-design.md
- aidlc-docs/knowledge/starrocks/functional-design/query-audit.md

## Superseded decisions
- Platform StarRocks copy task writing bronze (requirements Q1:A, AD-2) -> bronze_ingestion.py pull
- Dev gets synthetic rows only (AD-7) -> every environment pulls the real stream; the generator is optional
- Bronze schema shared with the platform -> bronze owned and written by this repository

## Archived artifacts
- aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/aidlc-docs/current/inception
- aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/aidlc-docs/current/construction
- aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/aidlc-docs/current/deploy
- aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/notebooks/common/audit_sql.py
- aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/workflows/starrocks
- aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/materialized_views/starrocks

## Archive
- Full artifacts and audit snapshot: `aidlc-docs/archive/20261125-starrocks-query-audit/full-artifacts/`
