# Requirement Verification Questions — StarRocks Query Audit

Intent `20261125-starrocks-query-audit`. Answer each question by filling the letter after `[Answer]:`. If no option fits, choose the last option (Other) and describe your preference.

Context for the questions: product Spark runs only receive Polaris-vended, table-scoped storage credentials and checkpoint keys, so they cannot read StarRocks' `fe.audit.log` or StarRocks internal tables directly. Some platform-side step must move the audit records into a place the `audit` product can read or own.

## Question 1
How should StarRocks audit records reach the audit product's Iceberg tables?

A) StarRocks does it: the platform installs the AuditLoader plugin (records land in a StarRocks internal table), and a scheduled StarRocks task inserts new rows into the audit product's Iceberg bronze table; this repository's Spark jobs build silver and gold from there

B) A collector does it: the platform runs a log shipper (for example Vector) that tails `fe.audit.log` into a raw prefix of `bucket-audit`, and the platform grants the audit writer read access to that prefix; this repository's Spark job ingests the raw files into bronze

C) Spark pulls it: the platform installs the AuditLoader plugin and grants the audit writer `SELECT` on the internal audit table; this repository's Spark job reads it over JDBC with the writer's token and appends to bronze

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 2
Which StarRocks events are in scope for this first intent?

A) Query statements only (SELECT, DML, DDL), from every client

B) Query statements plus connection events (logins and failed logins)

C) Query statements, connection events, and platform activity (materialized-view refreshes, the reconciler's grants, loads)

D) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 3
How should the SQL text be stored, given it may contain literal values (personal data)?

A) Full SQL text in bronze and silver, readable only by auditors; digest and normalized text (literals masked) for everyone else

B) Only the digest and normalized text (literals masked) anywhere; the full text is never stored in Iceberg

C) Full SQL text everywhere the audit tables are readable

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question 4
Who consumes the audit data in this intent?

A) Only the audit product's own groups (auditors and platform admins); no per-product views yet

B) Audit groups, plus each product's owners see only their own product's queries (per-product gold tables or views)

C) Every signed-in user can read the aggregated gold data; detail stays with the audit groups

D) Other (please describe after [Answer]: tag below)

[Answer]: D — "should follow the normal platform data product group concept. owner, developer, analyst groups should be created "

## Question 5
How fresh must the audit data be in production?

A) Within about 15 minutes (micro-batch or streaming)

B) Within about 1 hour (hourly DAG)

C) Within a day (daily DAG)

D) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question 6
How long should detailed audit rows be kept?

A) 90 days of detail, aggregates kept indefinitely

B) 1 year of detail, aggregates kept indefinitely

C) Keep everything indefinitely (no expiry job in this intent)

D) Other (please describe after [Answer]: tag below)

[Answer]: D — "where ? on the data product it should be kept without retention , in starrock it can be deleted "

## Question 7
What should this intent deliver in this repository?

A) Bronze and silver tables only (the queryable query log); gold and dashboards come later

B) Bronze, silver, and a first gold layer: StarRocks materialized views for usage per user, per product and per table, and slow or failed queries

C) Bronze, silver, gold, plus the cleanup of the template leftovers (remove `workflows/sample` and `workflows/tpch`, rename references to `opensource-lakehouse-sample-dp`, refresh canonical knowledge)

D) Other (please describe after [Answer]: tag below)

[Answer]: C

## Question 8
How should the platform-side prerequisites (the `audit` data product file, the StarRocks plugin or collector, grants) be handled?

A) A paired intent in `opensource-lakehouse-infra`, opened and run alongside this one; this intent records it as a dependency

B) Handled in this intent: this repository's documents also plan and track the platform changes

C) Recorded only as prerequisites in this intent's deploy instructions; the platform work is done by hand or later

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Question: Security Extensions
Should security extension rules be enforced for this project?

A) Yes — enforce all SECURITY rules as blocking constraints (recommended for production-grade applications)

B) No — skip all SECURITY rules (suitable for PoCs, prototypes, and experimental projects)

X) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question: Resiliency Extensions
Should the resiliency baseline be applied to this project?

**What this extension is.** Enabling it applies a set of **directional, design-time best practices** for building resilient systems, derived from the **AWS Well-Architected Framework (Reliability Pillar)** and resilience-review guidance. It steers requirements, design, and code toward fault tolerance, high availability, observability, and recoverability — covering 15 practice areas across business goals, change management, observability, high availability, disaster recovery, and continuous improvement.

**What this extension is NOT.** Enabling it does **not** make your workload production-ready, nor does it certify or guarantee any availability, RTO, or RPO target. It is a **starting point** that scaffolds good resiliency decisions early — it is not a substitute for a formal **AWS Well-Architected Review** of the built system.

A) Yes — apply the resiliency baseline as directional best practices and design-time guidance (recommended for business-critical workloads, as an informed starting point that you can validate and harden before go-live)

B) No — skip the resiliency baseline (suitable for PoCs, prototypes, and experimental projects where rapid iteration matters more than reliability)

X) Other (please describe after [Answer]: tag below)

[Answer]: B

## Question: Property-Based Testing Extension
Should property-based testing (PBT) rules be enforced for this project?

A) Yes — enforce all PBT rules as blocking constraints (recommended for projects with business logic, data transformations, serialization, or stateful components)

B) Partial — enforce PBT rules only for pure functions and serialization round-trips (suitable for projects with limited algorithmic complexity)

C) No — skip all PBT rules (suitable for simple CRUD applications, UI-only projects, or thin integration layers with no significant business logic)

X) Other (please describe after [Answer]: tag below)

[Answer]: B
