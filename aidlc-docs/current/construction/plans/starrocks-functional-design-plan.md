# Functional Design Plan — Unit 2 `starrocks`

Intent `20261125-starrocks-query-audit`. Component `query-audit` (canonical target at closure: `aidlc-docs/knowledge/starrocks/functional-design/query-audit.md`, CREATE in the approved inventory). Inputs: [requirements.md](../../inception/requirements/requirements.md), [application-design.md](../../inception/application-design/application-design.md). Work branch: `starrocks-query-audit`.

## Plan
- [x] Answer the questions below; follow-ups if anything is ambiguous (none needed)
- [x] `business-logic-model.md`: the bronze to silver to gold flow, the `audit_sql` algorithms (tokenizer, masking, categorization, actor, scope, referenced tables, event key), deduplication and merge, the dev sample generator, failure behavior
- [x] `business-rules.md`: numbered rules (masking, redaction, categories, actors, scope, keys, thresholds, environments, retention)
- [x] `domain-entities.md`: bronze, silver and gold entities with columns, types, keys and relationships
- [x] Testable Properties section (PBT-01) per component
- [x] DOC-01 sweep of `aidlc-docs/` for changed names and values (component-methods: seven categories, `redact_credentials`, `event_scope`, `payload_field`, `enrich_payload`; component-dependency: bronze payload redacted by the copy task, silver partitioned by `event_date`, added columns; application-design: open points)

## Question 1
Which statement categories should silver use? (Gold views can then leave out metadata and session noise.)

A) `SELECT`, `DML`, `DDL`, `ADMIN` (grants, users, roles, `ALTER SYSTEM`), `METADATA` (`SHOW`, `DESC`, `EXPLAIN`), `SESSION` (`SET`, `USE`), `OTHER`

B) The five from Application Design: `SELECT`, `DML`, `DDL`, `ADMIN`, `OTHER` (`SHOW`, `SET`, `USE` fall in `OTHER`)

C) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 2
SQL text can carry credentials (`CREATE USER ... IDENTIFIED BY '...'`, `SET PASSWORD`, storage keys in `PROPERTIES`/catalog DDL). The full text is readable by owners and developers. How should credentials be handled?

A) Silver's `sql_text` and `payload` have credentials redacted (`'***'`); the paired platform intent also redacts them in the copy task, so bronze never holds them

B) Silver redacts; bronze keeps the raw text (owners and developers can read it there)

C) No redaction: the full text is kept as StarRocks logged it

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 3
What should masking do with SQL comments (`-- ...`, `/* ... */`), which may hold free text?

A) Remove them from `sql_masked`, keeping StarRocks hints (`/*+ ... */`) as `/*+ ? */`

B) Keep them unchanged in `sql_masked`

C) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 4
Which data product does an event belong to when it touches several products, or none?

A) Rule order: the product of the session database if it is a product namespace or MV database; else the single product among the referenced tables; else `multiple` when there are several; else null. Per-table attribution is always in `starrocks_audit_table_access`

B) Always null at the event level; only `starrocks_audit_table_access` attributes products

C) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 5
What counts as a slow query for `mv_starrocks_slow_failed`?

A) 10 seconds or more

B) 30 seconds or more

C) 60 seconds or more

D) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 6
How much history should the detail view `mv_starrocks_slow_failed` hold? (It is fully refreshed every hour; the silver table keeps everything regardless.)

A) The last 30 days

B) The last 90 days

C) All history

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 7
What should silver do with a bronze row whose payload can't be parsed or lacks `timestamp`?

A) Keep it in silver with `event_type = 'unparsed'`, the raw `payload`, and nulls elsewhere; gold views ignore it; the run succeeds and logs the count

B) Skip it (it stays in bronze) and log the count; the run succeeds

C) Fail the run so someone looks at it

D) Other (please describe after [Answer]: tag below)

[Answer]: A
