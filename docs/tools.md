# Tool reference

All 15 tools registered across `tools/query.py`, `tools/tables.py`, `tools/jobs.py`, and
`tools/audit_tool.py`. Every tool returns a JSON-serializable dict; failures return
`{"error": "<actionable message>"}` rather than raising, and every call (success or failure)
is written to the audit log (see [security.md](security.md#audit-logging)).

## Query execution — `tools/query.py`

### `estimate_query_cost(sql: str)`

Dry-runs `sql` against BigQuery's planner. Never executes, never bills. Use before `run_query`
on anything that might scan a large table.

**Returns:** `{statement_type, total_bytes_processed, estimated_usd, would_require_confirmation}`
or `{"error": ...}` if the query fails to plan (e.g. syntax error, unsupported statement type).

```
estimate_query_cost(sql="SELECT * FROM `my_project.analytics.events`")
→ {"statement_type": "SELECT", "total_bytes_processed": 4831201920,
   "estimated_usd": 0.0281, "would_require_confirmation": false}
```

### `run_query(sql, max_rows=1000, page_token=None, confirm=false)`

Executes a validated, paginated, cached query.

- **Sensitive-column pre-check:** if the query is a bare `SELECT *` against a table with
  restricted columns, it's rejected with guidance *before* any BigQuery call (dry-run or
  otherwise) — see [`_guess_table_refs`](architecture.md#why-table-references-are-guessed-with-regex-not-parsed).
- **Cache:** identical `(sql, page_token)` pairs within `cache_ttl_seconds` are served from
  memory; the response includes `"cache_hit": true` and skips BigQuery entirely.
- **Validation:** runs the full `validate_query` pipeline — statement classification, DDL/DML
  gating, dry-run byte-cap check, `confirm=true` requirement for DDL/DML.
- **Execution:** capped by `maximum_bytes_billed` at the job-config level as a second,
  server-side backstop beyond the pre-flight estimate.
- **Pagination:** rows are collected up to `max_rows`; if more remain, `next_page_token` is
  returned for the next call. Only `SELECT` results are cached (DDL/DML results are not).

**Returns:** `{rows, row_count, next_page_token, bytes_processed, estimated_usd, cache_hit}`
or `{"error": ...}`.

```
run_query(sql="SELECT region, COUNT(*) AS n FROM `sales.orders` GROUP BY region")
→ {"rows": [{"region": "US", "n": 4213}, ...], "row_count": 6,
   "next_page_token": null, "bytes_processed": 102400, "estimated_usd": 0.0000006,
   "cache_hit": false}
```

### `run_parameterized_query(sql, parameters: dict, max_rows=1000, confirm=false)`

Same execution path as `run_query`, but binds `parameters` as BigQuery named parameters
(`@name` in SQL) instead of string-formatting values into the query — the recommended way to
run anything built from user-supplied input, to avoid SQL injection. Parameter Python types map
to BigQuery types: `bool`→`BOOL`, `int`→`INT64`, `float`→`FLOAT64`, anything else→`STRING`.

Note: unlike `run_query`, this tool does **not** run the sensitive-column pre-check or read from
the result cache — it goes straight to `validate_query` + execution.

**Returns:** `{rows, row_count, next_page_token, bytes_processed, estimated_usd}` or `{"error": ...}`.

```
run_parameterized_query(
    sql="SELECT * FROM `sales.orders` WHERE region = @region AND created_at > @since",
    parameters={"region": "US", "since": "2026-01-01"}
)
```

## Table & dataset management — `tools/tables.py`

All read-only tools here use metadata/tabledata APIs (not query jobs), so they don't need a
dry-run and don't bill for query bytes.

### `list_datasets()`
Lists every dataset in the configured project. Returns `{project_id, datasets: [...]}`.

### `list_tables(dataset_id: str)`
Lists tables/views in a dataset, each labeled with `table_type`. Returns
`{dataset_id, tables: [{table_id, type}, ...]}` or `{"error": ...}` if the dataset doesn't exist.

### `get_table_schema(dataset_id: str, table_id: str)`
Returns each column's `name`, `type`, `mode`, `description`, and whether it's `restricted`
per the current `SensitiveFieldGuard` state (reflects any prior `scan_sensitive_fields` run or
configured `prevented_fields`). Returns `{table_ref, schema: [...]}` or `{"error": ...}`.

### `get_table_metadata(dataset_id: str, table_id: str)`
Returns `{table_ref, num_rows, num_bytes, created, modified, table_type, partitioning,
clustering_fields}` or `{"error": ...}`.

### `preview_table(dataset_id: str, table_id: str, max_rows=10)`
Samples rows via the tabledata API — no query job, no billing. Still runs the sensitive-column
guard check first (a table with any restricted column and a bare `preview_table` call — which
implicitly requests all columns — is blocked with guidance). Returns `{table_ref, rows: [...]}`
or `{"error": ...}`.

### `create_table(dataset_id, table_id, schema: list[dict], confirm=false)`
Requires `allow_ddl=true` **and** `confirm=true`; without either, returns `{"error": ...}`
explaining which is missing. `schema` entries look like
`{"name": "user_id", "type": "STRING", "mode": "REQUIRED"}` (`mode` defaults to `"NULLABLE"`).
Returns `{created: "<dataset>.<table>", num_fields}` on success.

### `delete_table(dataset_id, table_id, confirm=false)`
Requires `allow_ddl=true` and `confirm=true`. **Irreversible.** Returns
`{deleted: "<dataset>.<table>"}` on success.

### `create_view(dataset_id, view_id, view_query, confirm=false)`
Requires `allow_ddl=true` and `confirm=true`. `view_query` is the SQL backing the view.
Returns `{created_view: "<dataset>.<view>"}` on success.

### `scan_sensitive_fields()`
Walks every dataset/table in the project, matching column names against
`sensitive_field_patterns`, and **merges** any matches directly into the live
`guard.prevented_fields` (in-memory, for this server process's lifetime — not persisted back
to `config.json`). Meaningful mainly under `protection_mode: auto_protect` or `allowlist`.
Returns `{scanned_tables, flagged_columns: {"dataset.table": [col, ...]}}`.

## Job management — `tools/jobs.py`

### `list_jobs(max_results=20, all_users=false)`
Lists recent jobs (query/load/etc.) with `job_id`, `job_type`, `state`, `created`,
`user_email`, and for query jobs, `total_bytes_processed` and a truncated (200-char) `query`
snippet. `all_users=true` requires IAM permission to view others' jobs in the project. Returns
`{jobs: [...]}`.

### `get_job_status(job_id: str)`
Full detail on one job: `job_id, job_type, state, created, started, ended`, plus for query jobs
`total_bytes_processed, total_bytes_billed, cache_hit`, plus `error` if the job failed. Returns
`{"error": ...}` if the job ID doesn't exist.

### `cancel_job(job_id: str, confirm=false)`
Requires `confirm=true`. Returns `{cancelled: job_id}` or `{"error": ...}` if not found.

## Audit — `tools/audit_tool.py`

### `get_audit_log(limit=50)`
Returns the most recent audit entries (see [security.md](security.md#audit-logging) for the
entry schema): `{count, entries: [...]}`.

## Common patterns across tools

- **Confirmation gate:** every mutating tool (`create_table`, `delete_table`, `create_view`,
  `cancel_job`, and DDL/DML through `run_query`/`run_parameterized_query`) requires an explicit
  `confirm=true` on the *same call* — there's no session-level "confirmed once, trust me after
  that" state.
- **Errors are data, not exceptions:** every tool returns `{"error": "..."}` on failure instead
  of throwing across the MCP boundary, and the error text is written to explain the fix (enable
  a config flag, add `confirm=true`, narrow the query, check the dataset/table name).
- **Everything is audited:** success or failure, every tool call appends one line to the audit
  log via the shared `AuditLog` instance.
