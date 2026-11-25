# Requirement Clarification Questions — StarRocks Query Audit

Intent `20261125-starrocks-query-audit`. Raised after validating `requirement-verification-questions.md`.

## Contradiction 1: full SQL "for auditors only" vs. the standard product groups
You chose "Full SQL text readable only by auditors; masked text for everyone else" (Q3:A) and "follow the normal platform data product group concept: owner, developer, analyst groups" (Q4:D).
These conflict because the platform grants access per namespace, not per table or column: a group with `read` on `audit_lh` reads every Iceberg table in it, including the full SQL text. There is no separate "auditor" group in the standard model. The platform does grant the materialized-view databases separately (`materialized_views` in the product file), so a group can read curated views without reading the namespace.

### Clarification Question 1
Which standard groups may read the full SQL text?

A) Owners and developers read the namespace (full SQL text); analysts get `none` on the production namespace and `read` on the production materialized-view database, whose views expose only the digest and masked SQL text

B) Every group with read access (owner, developer, analyst) reads the full SQL text; masking is not needed

C) Nobody stores full SQL text in Iceberg: bronze and silver keep only the digest and masked text; the full text stays in the StarRocks staging table until it is deleted

D) Other (please describe after [Answer]: tag below)

[Answer]: A

## Ambiguity 1: how long the StarRocks staging rows are kept
You answered (Q6) that the data product keeps audit data without retention, and that in StarRocks it can be deleted. The StarRocks staging table (where the AuditLoader plugin writes before the hourly copy into Iceberg) needs a deletion rule; it must keep rows long enough to catch up after a failed or paused copy.

### Clarification Question 2
How long should rows stay in the StarRocks staging table?

A) 7 days (partition time-to-live; enough to catch up after a missed weekend)

B) 30 days

C) Deleted as soon as they are copied to Iceberg

D) Other (please describe after [Answer]: tag below)

[Answer]: A
