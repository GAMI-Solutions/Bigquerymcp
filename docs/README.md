# bigquery-mcp documentation

Detailed reference docs for the `bigquery-mcp` server. Start with the main
[project README](../README.md) for install/quickstart; come here for depth.

| Doc | Covers |
|---|---|
| [architecture.md](architecture.md) | How a tool call flows through auth, safety, protection, cache, and audit. Module map. |
| [configuration.md](configuration.md) | Every config field, env var, CLI flag, and the precedence order between them. |
| [tools.md](tools.md) | Full reference for all 15 MCP tools — parameters, return shape, gating, examples. |
| [security.md](security.md) | Cost guardrails, DDL/DML gating, PII/PHI protection modes, audit logging, and their limits. |
| [authentication.md](authentication.md) | The three auth methods, required IAM roles, and troubleshooting. |
| [use-cases.md](use-cases.md) | Worked example prompts by scenario (analytics, cost control, PII-safe queries, governance). |

## At a glance

`bigquery-mcp` is a Python MCP server (`src/bigquery_mcp/`) built on `FastMCP`. It wraps the
`google-cloud-bigquery` client with four layers before any tool touches BigQuery:

1. **auth** — resolves credentials (service account key, ADC, or interactive OAuth).
2. **safety** — classifies every statement (SELECT/DDL/DML), dry-runs it for a cost estimate,
   enforces `maximum_bytes_billed`, and gates DDL/DML behind config + `confirm=true`.
3. **protection** — blocks or masks configured/auto-discovered sensitive columns.
4. **cache** + **audit** — TTL-caches identical read queries and JSON-lines-logs every call.

Tools are grouped into four registration modules under `tools/`: `query.py`, `tables.py`,
`jobs.py`, `audit_tool.py` — each takes the shared `client`/`cache`/`guard`/`audit` objects and
registers its tools onto the one `FastMCP("bigquery-mcp")` instance built in `server.py`.
