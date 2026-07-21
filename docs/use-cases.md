# Example use cases

Practical scenarios mapped to the tools in [tools.md](tools.md).

## Ad-hoc analytics (safe by default)

> "How many orders did we get last week by region?"

Claude estimates first (`estimate_query_cost`) so you see bytes/USD before anything runs, then
calls `run_query` (results are cached for `cache_ttl_seconds` if you ask again).

> "What tables do we have in the `analytics` dataset, and what's in `events`?"

`list_datasets` → `list_tables` → `get_table_schema` → `preview_table` (free row sample, no
query job, no billing) — a safe way to explore an unfamiliar warehouse before writing SQL.

## Cost control on expensive or unbounded queries

> "Estimate the cost of scanning all of `raw_events` for the last year before running it."

`estimate_query_cost` catches a full-table scan before it bills anything; if you go ahead with
`run_query` and it would exceed `maximum_bytes_billed`, it's rejected with a suggestion to add
filters, select fewer columns, or narrow the date range.

> "Is my query still running? Cancel it if so."

`list_jobs` → `get_job_status` → `cancel_job(confirm=true)` — useful for a long-running
aggregation Claude kicked off that's taking too long or looks like it'll cost more than
expected.

## PII/PHI-safe analysis on sensitive data

With `protection_mode: auto_protect` and a `users`/`patients` table:

> "What's the age distribution of our users?"

Works fine (age isn't restricted). But:

> "Show me a sample of user records."

`preview_table` is blocked because it implicitly requests every column, and returns guidance
like `SELECT * EXCEPT(email, ssn) FROM ...` instead of a dead end.

> "Which columns across our whole warehouse look sensitive?"

`scan_sensitive_fields` auto-discovers matches against the configured patterns
(`%email%`, `%ssn%`, `%api_key%`, etc.) across every dataset/table in one call, rather than
hand-listing tables one at a time.

## Table/dataset management (opt-in DDL)

With `allow_ddl: true`:

> "Create a view joining `orders` and `customers` for the BI team."

`create_view(confirm=true)` — still requires the explicit confirmation on the call even though
DDL is enabled at the config level.

> "Spin up a scratch table for this analysis, then drop it when I'm done."

`create_table(confirm=true)` / `delete_table(confirm=true)` — `delete_table` is irreversible,
so expect (and want) the confirmation prompt.

## Data engineering / pipeline work (opt-in DML)

With `allow_dml: true`:

> "Update the `status` column for these 200 order IDs."

Use `run_parameterized_query` with bind parameters rather than string-formatting values into
SQL — especially important when the IDs come from user input or another system, since it avoids
SQL injection.

## Governance / audit

> "Show me the last 20 queries Claude ran against this project and how much they cost."

`get_audit_log(limit=20)` — useful for a team lead reviewing what an AI agent has been doing
against production BigQuery, debugging a bill spike, or a compliance review. Queries that
touched sensitive columns have their string literals redacted in the log automatically.

## First run after installing

A good smoke test before touching anything with `allow_ddl`/`allow_dml`:

1. `list_datasets` — confirms auth and project access are wired up correctly.
2. `list_tables` on one dataset, then `get_table_schema` on one table.
3. `preview_table` with a small `max_rows` to confirm read access end-to-end.
