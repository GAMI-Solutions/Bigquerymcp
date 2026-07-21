# Architecture

## Module map

```
src/bigquery_mcp/
├── server.py        entry point: parses args, loads config, builds the FastMCP server
├── config.py        BigQueryMCPConfig (pydantic) + load_config() precedence chain
├── auth.py          resolves an authenticated google.cloud.bigquery.Client
├── safety.py        statement classification, dry-run cost check, DDL/DML gating
├── protection.py    SensitiveFieldGuard — PII/PHI column blocking + auto-scan
├── cache.py         QueryResultCache — TTL cache keyed on sql+params+page_token
├── audit.py         AuditLog — JSON-lines log of every tool call
└── tools/
    ├── query.py      estimate_query_cost, run_query, run_parameterized_query
    ├── tables.py     list_datasets, list_tables, get_table_schema, get_table_metadata,
    │                 preview_table, create_table, delete_table, create_view,
    │                 scan_sensitive_fields
    ├── jobs.py       list_jobs, get_job_status, cancel_job
    └── audit_tool.py get_audit_log
```

## Startup sequence (`server.py: main()` → `build_server()`)

1. `build_arg_parser().parse_args()` — CLI flags.
2. `load_config(config_file, overrides=<cli flags>)` — merges file → env vars → CLI flags
   (see [configuration.md](configuration.md) for the exact precedence order).
3. `get_bigquery_client(...)` from `auth.py`, then `test_connection(client)` — a dry-run
   `SELECT 1` used purely to fail fast with an actionable error if credentials/project/API
   access are wrong. On failure the process logs the error and calls `sys.exit(1)` — the
   server never starts half-authenticated.
4. Construct the shared singletons: `QueryResultCache(ttl_seconds=...)`,
   `SensitiveFieldGuard(protection_mode, prevented_fields, sensitive_field_patterns)`,
   `AuditLog(path=...)`.
5. Create one `FastMCP("bigquery-mcp")` instance and call each tools module's `register()`,
   passing the shared `client`/`cache`/`guard`/`audit` objects plus the relevant config flags
   (`maximum_bytes_billed`, `allow_ddl`, `allow_dml`).
6. `mcp.run(transport="stdio")` or `mcp.run(transport="streamable-http", port=...)` depending
   on `--http`.

Because `cache`, `guard`, and `audit` are constructed once in `build_server()` and closed over
by every tool's registration closure, they are shared, in-process, mutable state for the
lifetime of the server — e.g. `scan_sensitive_fields` mutates the same `guard.prevented_fields`
dict that `run_query`/`preview_table` read on every subsequent call.

## Request flow: `run_query`

This is the path most tool calls take; other tools are simpler subsets of it.

```
run_query(sql, max_rows, page_token, confirm)
  │
  ├─ _guess_table_refs(sql)                     regex-based FROM/JOIN table extraction
  │    └─ guard.check_columns(table_ref, ["*"]) if SELECT * against a restricted table → reject
  │                                              with guidance, before any BigQuery call at all
  │
  ├─ cache.get(sql, page_token)                 SHA-256(sql, params, page_token) lookup;
  │    └─ hit → return immediately, cache_hit=True, no BigQuery call
  │
  ├─ validate_query(...)  [safety.py]
  │    ├─ classify_statement(sql)               regex match on leading keyword
  │    ├─ reject if DDL/DML and not allowed by config
  │    ├─ dry_run(client, sql)                   real BigQuery dry-run job → bytes/cost estimate
  │    ├─ reject if bytes > maximum_bytes_billed
  │    └─ reject if DDL/DML and confirm != true
  │
  ├─ client.query(sql, job_config=...)          actual execution, capped by maximum_bytes_billed
  ├─ _rows_to_dicts(...)                         paginates rows into a list, capped at max_rows
  ├─ cache.set(...)                              only for statement_type == "SELECT"
  └─ audit.record(...)                           always, success or failure
```

Every rejection path (sensitive columns, DDL/DML disabled, over byte cap, missing confirm)
returns a plain `{"error": "..."}` dict rather than raising through the MCP boundary — the
message is written to explain *why* and *what to do next* (e.g. "re-run with confirm=true",
"add filters to narrow the query"), since that text is what the calling LLM sees and acts on.

## Why table references are guessed with regex, not parsed

`tools/query.py`'s `_guess_table_refs` is a best-effort `FROM|JOIN` regex, not a real SQL
parser. It's sufficient to flag likely sensitive-table access for the cooperative protection
layer, but it can miss table references inside CTEs with unusual formatting, subqueries, or
non-standard quoting. This is a known, accepted limitation — see
[security.md](security.md#protection-is-cooperative-not-a-hard-boundary).
