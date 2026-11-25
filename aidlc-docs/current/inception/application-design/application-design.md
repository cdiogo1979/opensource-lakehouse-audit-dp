# Application Design — StarRocks Query Audit (consolidated)

Intent `20261125-starrocks-query-audit`, subproject `starrocks` of the `audit` data product. Details: [components.md](components.md), [component-methods.md](component-methods.md), [services.md](services.md), [component-dependency.md](component-dependency.md). Decisions: [application-design-plan.md](../plans/application-design-plan.md).

## Decisions
| # | Decision | Source |
|---|---|---|
| AD-1 | Bronze `starrocks_audit_event_fe_brz` holds `query_id`, `event_time`, the whole staging row as a JSON `payload`, `copied_at`, `copy_task`; a StarRocks version change touches only the payload | Q1:B |
| AD-2 | The platform task copies the last 2 hours every hour, without checking bronze; bronze is append-only and holds overlap duplicates; silver deduplicates by `event_key` | Q2:B |
| AD-3 | Silver reads bronze with Structured Streaming `availableNow` and a checkpoint, merging insert-if-absent | Q3:A |
| AD-4 | One silver event table (partitioned by `event_date`, Functional Design BR-41) `starrocks_audit_event` (`event_type` query or connection), plus `starrocks_audit_table_access` (event and referenced table) for per-table usage | Q4:A; the child table is a design addition, so `unnest` isn't needed in views |
| AD-5 | Pure logic in `notebooks/common/audit_sql.py`, reused by later audit sources | Q5:A |
| AD-6 | Own quote- and comment-aware tokenizer for masking; no new dependency | Q6:A |
| AD-7 | Prod and UAT receive real rows (one copy task each); dev uses a synthetic `ops/` notebook | Q7:A |
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
| P1–P3 | Product file, AuditLoader and staging table, copy tasks | paired intent in `opensource-lakehouse-infra` |

## Flow
StarRocks FE → AuditLoader → staging table (7-day TTL) → hourly copy task at hh:05 (last 2 hours) → bronze → Airflow `starrocks_audit` at hh:20 (stream, enrich with C1, deduplicate, merge) → silver → views refreshing at hh:35 → gold. Event to silver in about 20 to 80 minutes, to gold in about 35 to 95 minutes.

## Contracts
- **Bronze** (shared with the platform): see [component-dependency.md — the bronze contract](component-dependency.md#the-bronze-contract-shared-with-the-paired-platform-intent). The paired intent must create the copy task against it.
- **Silver** (consumed by gold and future sources): see [component-dependency.md — silver contract](component-dependency.md#silver-contract-consumed-by-the-gold-views-and-by-future-audit-sources).

## Access
Owners and developers: namespace `read` or higher (full SQL text in silver and bronze). Analysts: production MV database `read` only (masked gold). Writer: `write` on the namespaces and `author` on the MV databases, as the platform default.

## Requirements traceability
| Requirement | Components |
|---|---|
| FR-1, FR-2 | P2, P3 (paired intent), bronze contract |
| FR-3 | C2 |
| FR-4 | C1, C3 |
| FR-5 | C4 |
| FR-6 | P1, access rules above |
| FR-7 | C2, C3 (append-only, insert-if-absent, no expiry) |
| FR-8 | Unit 1 `repo-identity` |
| NFR-1 | services timeline |
| NFR-2 | AD-2, AD-3 (overlap, then deduplicate by `event_key`) |
| NFR-3 | one task per run |
| NFR-4 | AD-9, C1 masking |
| NFR-6 | C1 pure functions (PBT), C2 to C5 example tests |

## Open points carried to Functional Design and the paired intent
- The exact AuditLoader columns of StarRocks 4.1.7 and whether connection events are recorded (P-1). If they aren't, `mv_starrocks_login_day` stays empty and the gap is documented.
- The FE time zone (bronze `event_time` must be UTC).
- The copy task's identity and its write grant on `audit_lh` and `audit_lhuat`.
- The copy task redacts credentials in `stmt` before writing bronze (Functional Design BR-20, BR-23).
- Slow-query threshold and failure states: decided in Functional Design (60 s; `state = 'ERR'`; slow/failed detail keeps 30 days).
