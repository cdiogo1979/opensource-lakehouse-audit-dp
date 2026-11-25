# Application Design — StarRocks Query Audit (consolidated)

Intent `20261125-starrocks-query-audit`, subproject `starrocks` of the `audit` data product. Details: [components.md](components.md), [component-methods.md](component-methods.md), [services.md](services.md), [component-dependency.md](component-dependency.md). Decisions: [application-design-plan.md](../plans/application-design-plan.md).

## Decisions
| # | Decision | Source |
|---|---|---|
| AD-1 | Bronze `starrocks_audit_event_fe_brz` holds `query_id`, `event_time`, the whole staging row as a JSON `payload`, `copied_at`, `copy_task`; a StarRocks version change touches only the payload | Q1:B |
| AD-10 | **Pull model**: a bronze task in this repository (`bronze/bronze_ingestion.py`) reads the staging table through the platform's `lakehouse_starrocks.connect()` and appends to bronze, as the first task of `starrocks_audit` | change request 2026-11-25 (supersedes requirements Q1:A, AD-2's copy task) |
| AD-2 | The bronze task reads from the bronze watermark minus a 30-minute lookback; bronze is append-only and holds the re-read rows twice; silver deduplicates by `event_key` | Q2:B, adapted to the pull model (2026-11-25) |
| AD-3 | Silver reads bronze with Structured Streaming `availableNow` and a checkpoint, merging insert-if-absent | Q3:A |
| AD-4 | One silver event table (partitioned by `event_date`, Functional Design BR-41) `starrocks_audit_event` (`event_type` query or connection), plus `starrocks_audit_table_access` (event and referenced table) for per-table usage | Q4:A; the child table is a design addition, so `unnest` isn't needed in views |
| AD-5 | Pure logic in `notebooks/common/audit_sql.py`, reused by later audit sources | Q5:A |
| AD-6 | Own quote- and comment-aware tokenizer for masking; no new dependency | Q6:A |
| AD-7 | Every environment (prod, uat, dev) pulls the same real stream (one StarRocks instance); the synthetic `ops/` notebook is optional in dev | Q7:A, superseded by the user's request of 2026-11-25 |
| AD-8 | Bronze definition owned here (`deploy/` notebook, manual `starrocks_deploy` workflow), Parquet and partitioned by day, as StarRocks writes only Parquet Iceberg tables | FR-3.2 |
| AD-9 | Gold: five hourly views; none exposes `sql_text` or `payload` | FR-5, FR-6.2 |

## Components
| Id | Component | Location |
|---|---|---|
| C1 | `audit_sql` shared logic | `notebooks/common/audit_sql.py` |
| C2 | Bronze table deployment | `notebooks/workload/starrocks/deploy/deploy_bronze_table.py`, `workflows/starrocks/starrocks_deploy.json` |
| C3 | Silver processing | `notebooks/workload/starrocks/silver/silver_processing.py`, `workflows/starrocks/starrocks_audit.json` |
| C4 | Gold views | `materialized_views/starrocks/mv_starrocks_{user_day,product_day,table_day,slow_failed,login_day}.sql` |
| C5 | Dev sample events | `notebooks/workload/starrocks/ops/ops_generate_sample_events.py` |
| C6 | Bronze ingestion | `notebooks/workload/starrocks/bronze/bronze_ingestion.py`, first task of `workflows/starrocks/starrocks_audit.json` |
| P1–P3 | Product file; AuditLoader and staging table; `SELECT` grants and the `lakehouse_starrocks.connect()` helper | paired intent in `opensource-lakehouse-infra` |

## Flow
StarRocks FE → AuditLoader → staging table (7-day TTL) → Airflow `starrocks_audit` at hh:20: task 1 pulls into bronze (watermark minus 30 minutes, redacted), task 2 streams bronze into silver (enrich with C1, deduplicate, merge) → views refreshing at hh:35 → gold. Event to silver in about 20 to 85 minutes, to gold in about 35 to 95 minutes.

## Contracts
- **Bronze** (written and read by this repository): see [component-dependency.md — the bronze contract](component-dependency.md#the-bronze-contract). This repository's bronze task writes it (AD-10).
- **Silver** (consumed by gold and future sources): see [component-dependency.md — silver contract](component-dependency.md#silver-contract-consumed-by-the-gold-views-and-by-future-audit-sources).

## Access
Owners and developers: namespace `read` or higher (full SQL text in silver and bronze). Analysts: production MV database `read` only (masked gold). Writer: `write` on the namespaces and `author` on the MV databases, as the platform default.

## Requirements traceability
| Requirement | Components |
|---|---|
| FR-1 | P2 (paired intent) |
| FR-2 | C6, P3 (access) |
| FR-3 | C2 |
| FR-4 | C1, C3 |
| FR-5 | C4 |
| FR-6 | P1, access rules above |
| FR-7 | C2, C3 (append-only, insert-if-absent, no expiry) |
| FR-8 | Unit 1 `repo-identity` |
| NFR-1 | services timeline |
| NFR-2 | AD-2, AD-3 (watermark lookback, then deduplicate by `event_key`) |
| NFR-3 | one task per run |
| NFR-4 | AD-9, C1 masking |
| NFR-6 | C1 pure functions (PBT), C2 to C5 example tests |

## Open points carried to Functional Design and the paired intent
- The exact AuditLoader columns of StarRocks 4.1.7 and whether connection events are recorded (P-1). If they aren't, `mv_starrocks_login_day` stays empty and the gap is documented.
- The FE time zone (bronze `event_time` must be UTC).
- The platform's `lakehouse_starrocks.connect()` helper, the `SELECT` grants on the staging table, and network and TLS access to StarRocks from Spark runs and Jupyter (pull model, 2026-11-25).
- Slow-query threshold and failure states: decided in Functional Design (60 s; `state = 'ERR'`; slow/failed detail keeps 30 days).
