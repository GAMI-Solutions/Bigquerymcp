# bigquery-mcp

A Python-native MCP (Model Context Protocol) server that lets Claude query, analyze, and
manage Google BigQuery — with cost guardrails, PII/PHI protection, and audit logging
built in from the start.

Unlike simpler BigQuery MCP servers that only support read-only `SELECT` queries, this
server also covers table and dataset management, job monitoring, and safe (opt-in) DDL/DML,
while keeping every mutating action gated behind an explicit confirmation step and a
pre-flight cost estimate.

## Features

- **Cost guardrails on every query.** Every statement is dry-run planned by BigQuery
  before it executes, so you get a real bytes-scanned / USD estimate up front, and a
  hard `maximum_bytes_billed` cap that rejects runaway queries before they bill anything.
- **Read-only by default, DDL/DML by opt-in.** `SELECT` works out of the box. Table
  creation/deletion and `INSERT`/`UPDATE`/`DELETE`/`MERGE` are disabled unless you set
  `allow_ddl` / `allow_dml`, and even then require an explicit `confirm=true` on the call.
- **Table & dataset management.** List datasets/tables, inspect schemas and metadata
  (row count, size, partitioning), cheaply preview rows, create/delete tables, create views.
- **Job monitoring.** List recent BigQuery jobs, check status, cancel a running job —
  useful for keeping tabs on (or stopping) expensive queries Claude kicked off.
- **PII/PHI protection.** Define restricted columns per table, or let the auto-scanner
  discover sensitive columns (names, emails, SSNs, API keys, etc.) across your entire
  warehouse. Blocked queries get actionable guidance (`SELECT * EXCEPT(...)`, aggregates)
  instead of a dead end.
- **Query result caching.** TTL-based cache keyed on query + parameters, so repeated
  identical reads don't re-scan (and re-bill) BigQuery.
- **Audit logging.** Every tool call is logged as JSON lines (query text, bytes billed,
  duration, success/failure), with sensitive queries automatically redacted. Reviewable
  by an admin, or by Claude itself via the `get_audit_log` tool.
- **Flexible auth.** Service account key file, Application Default Credentials, or
  interactive OAuth.
- **Both transports.** Runs over `stdio` for Claude Desktop/Code, or `--http` for a
  remote/hosted deployment.

## Install

Requires Python 3.10+.

```bash
git clone <this-repo>
cd bigquery-mcp
pip install -e ".[dev]"
# or, with uv:
uv pip install -e ".[dev]"
```

## Authenticate with Google Cloud

Pick one:

```bash
# Option A: Application Default Credentials (good for local dev)
gcloud auth application-default login

# Option B: service account key file — set key_file in config.json, or --key-file
```

The authenticated identity needs at least **BigQuery Job User** on the project, plus
**BigQuery Data Viewer** (read-only mode) or **BigQuery Data Editor** (if `allow_ddl`/`allow_dml`
is enabled) on the relevant datasets.

## Configuration

Copy `config.example.json` to `config.json` and edit it, or set environment variables
(`BQMCP_PROJECT_ID`, `BQMCP_LOCATION`, `BQMCP_KEY_FILE`, `BQMCP_MAXIMUM_BYTES_BILLED`,
`BQMCP_PROTECTION_MODE`, `BQMCP_CACHE_TTL_SECONDS`, `BQMCP_ALLOW_DDL`, `BQMCP_ALLOW_DML`,
`BQMCP_AUDIT_LOG_PATH`). CLI flags override both.

| Setting | Default | Description |
|---|---|---|
| `project_id` | *(required)* | Google Cloud project to query against. |
| `location` | `"US"` | BigQuery dataset location/region. |
| `key_file` | `null` | Path to a service account key JSON file. If unset, ADC is used. |
| `maximum_bytes_billed` | `1000000000` (1GB) | Hard cap on bytes billed per query. |
| `protection_mode` | `"off"` | `off` / `allowlist` / `auto_protect` — see below. |
| `prevented_fields` | `{}` | `"dataset.table"` → list of restricted column names. |
| `sensitive_field_patterns` | built-in set | SQL LIKE-style patterns for the auto-scanner. |
| `cache_ttl_seconds` | `300` | TTL for cached query results. `0` disables caching. |
| `allow_ddl` | `false` | Enable `CREATE`/`ALTER`/`DROP`/`TRUNCATE` (still requires `confirm=true` per call). |
| `allow_dml` | `false` | Enable `INSERT`/`UPDATE`/`DELETE`/`MERGE` (still requires `confirm=true` per call). |
| `audit_log_path` | `"audit-log.jsonl"` | Where the JSON-lines audit log is written. |

> **Honest caveat:** `prevented_fields` / auto-scanning are cooperative guardrails for
> the AI agent, not a hard SQL firewall. Use BigQuery IAM / column-level security for
> a real security boundary; this layer keeps well-behaved agents from surfacing PII
> into an LLM conversation.

## Claude Desktop / Claude Code configuration

**(a) Simple read-only mode, with ADC:**

```json
{
  "mcpServers": {
    "bigquery": {
      "command": "bigquery-mcp",
      "args": ["--project-id", "your-project-id"]
    }
  }
}
```

**(b) Protected mode, with a service account key and field restrictions:**

```json
{
  "mcpServers": {
    "bigquery": {
      "command": "bigquery-mcp",
      "args": [
        "--project-id", "your-project-id",
        "--location", "us-central1",
        "--key-file", "/path/to/service-account-key.json",
        "--config-file", "/path/to/config.json"
      ]
    }
  }
}
```

Where `config.json` sets `"protection_mode": "auto_protect"` and lists `prevented_fields`.

**(c) DDL/DML-enabled mode, for table management:**

```json
{
  "mcpServers": {
    "bigquery": {
      "command": "bigquery-mcp",
      "args": [
        "--project-id", "your-project-id",
        "--config-file", "/path/to/config.json",
        "--allow-ddl"
      ]
    }
  }
}
```

## Available tools

| Tool | Purpose |
|---|---|
| `estimate_query_cost` | Dry-run a query, return bytes/cost estimate. Never bills. |
| `run_query` | Execute a validated, paginated, cached SQL query. |
| `run_parameterized_query` | Execute SQL with bind parameters (avoids injection). |
| `list_datasets` / `list_tables` | Enumerate datasets and tables/views. |
| `get_table_schema` | Column names/types/modes, flagged if restricted. |
| `get_table_metadata` | Row count, size, partitioning, last modified. |
| `preview_table` | Cheap row sample via the tabledata API (no billing). |
| `create_table` / `delete_table` / `create_view` | DDL, gated behind `allow_ddl` + `confirm=true`. |
| `list_jobs` / `get_job_status` / `cancel_job` | Monitor and cancel BigQuery jobs. |
| `scan_sensitive_fields` | Trigger the PII/PHI column auto-scanner. |
| `get_audit_log` | Review recent tool activity, costs, and errors. |

## Running tests

```bash
pip install -e ".[dev]"
pytest -q
```

Tests use a fully mocked BigQuery client — no GCP project or credentials required.

## Why this instead of a typical read-only BigQuery MCP server?

| | Typical read-only BigQuery MCP server | bigquery-mcp |
|---|---|---|
| Language | Node.js | Python — easy to extend/hack on |
| Table & dataset management | Not supported | Full create/inspect/delete/preview |
| Job control | Not supported | List, inspect, cancel running jobs |
| Cost guardrails | Byte limit only | Dry-run estimate before every mutation + byte cap |
| Query caching | Not supported | TTL cache on identical reads |
| PII/PHI protection | Block + guidance | Block + guidance + configurable auto-mask |
| Audit trail | Not supported | JSON-lines log of every call, queryable by Claude |
| Transport | stdio only | stdio or HTTP |

## License

MIT
