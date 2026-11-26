# Performance Test Instructions — StarRocks Query Audit

Performance testing is not required for this intent (no throughput requirement beyond NFR-1 hourly freshness and NFR-3 fitting one Spark container). What to watch during UAT:
- **Run time**: the `starrocks_audit` DAG run (both tasks) should finish well inside the hour; the task timeouts are 30 minutes (bronze) and 40 minutes (silver).
- **Memory**: both notebooks hold at most one chunk of 20000 rows on the driver at a time; for a first run over a full 7-day staging table, watch the Spark container's memory (exit 137 means the chunk size should go down).
- **Volume**: the smoke test processed 904 bronze rows and 600 events in seconds on `local[2]`; record the hourly event count in UAT from the summary logs (`rows`, `rows_read`) to size later.
