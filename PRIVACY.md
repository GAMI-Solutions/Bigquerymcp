# Privacy Policy — bigquery-mcp

**Last updated:** 2026-07-21

`bigquery-mcp` ("the server") is an open-source Model Context Protocol (MCP) server,
published by Gami Solutions, that connects an MCP client (such as Claude Desktop or Claude
Code) to a Google BigQuery project. This document describes what data the server processes,
where it goes, and what is stored, based on the software's actual behavior.

## Summary

- The server runs **locally on your own machine** (or on infrastructure you control, if you
  deploy it with `--http`). It is not a hosted service operated by Gami Solutions.
- It connects **directly to Google Cloud / Google BigQuery** using credentials you provide.
  Gami Solutions does not receive, proxy, or have visibility into your queries, your data,
  your BigQuery project, or your credentials.
- The only persistent local record it creates is a JSON-lines **audit log** on your own disk.
- The server does not phone home, does not send telemetry or analytics to Gami Solutions or
  any third party, and does not include any tracking code.

## What data the server processes

When you use the server through an MCP client, it handles:

- **SQL query text** you (or the AI agent acting on your behalf) submit.
- **Query results** returned by BigQuery (rows, schemas, table/dataset metadata, job status).
- **Google Cloud credentials** needed to authenticate to your BigQuery project — a service
  account key file path, Application Default Credentials, or an interactive OAuth token,
  depending on how you configure it (see [`docs/authentication.md`](docs/authentication.md)).

All of this data flows in a single direction: **MCP client ↔ local server process ↔ Google
BigQuery API**. It is never sent to Gami Solutions or any endpoint other than Google's own
BigQuery API.

## Where data is stored

| Data | Storage | Persisted across restarts? | Sent anywhere else? |
|---|---|---|---|
| Query results | In-memory TTL cache (default 5 min, configurable, disable with `cache_ttl_seconds: 0`) | No | No |
| Audit log (tool calls, query text, cost, duration, success/failure) | Local file, default `audit-log.jsonl`, path configurable via `audit_log_path` | Yes, on disk where you run the server | No — readable only by you, or by the AI agent via the `get_audit_log` tool, which returns data from that same local file |
| Credentials (service account key, OAuth token, ADC) | Wherever you already store them (your filesystem, `gcloud` config, or GCP workload identity) | N/A — the server reads, does not copy or relocate them | No — used only to authenticate directly to Google's BigQuery API |

### Audit log redaction

The audit log is designed to be safe to review, but it is not a redacted-by-default dump of
raw data: it records the SQL text of each query, not the result rows. If a query is flagged as
touching a sensitive/restricted column (see [`docs/security.md`](docs/security.md)), any
single-quoted string literal in the logged SQL is automatically replaced with
`'***REDACTED***'` before being written. Queries that don't touch a flagged column are logged
verbatim. You control what counts as "sensitive" via `prevented_fields` /
`sensitive_field_patterns` in your configuration — review that configuration if you plan to run
this server against tables containing personal data, health data, or other regulated
information.

## Third parties

The only third party this server communicates with is **Google Cloud / Google BigQuery**,
using the credentials you configure. Google's own data handling for BigQuery is governed by
your organization's agreement with Google Cloud, not by Gami Solutions. Gami Solutions has no
access to that traffic or its contents.

If you enable the interactive OAuth flow, Google's standard OAuth consent screen is used
directly between your machine and Google — no intermediary server is involved.

## Data protection features (not a compliance guarantee)

This server includes cost guardrails, opt-in DDL/DML gating, and PII/PHI column-blocking
features (see [`docs/security.md`](docs/security.md)). These are **cooperative safeguards for
an AI agent's behavior** — they help prevent an honestly-behaving agent from accidentally
pulling sensitive columns into a conversation, or from running away with an unexpectedly
expensive query. They are not a substitute for Google Cloud IAM, column-level security, or your
organization's own compliance controls, and Gami Solutions makes no warranty that they satisfy
any particular regulatory requirement (HIPAA, GDPR, etc.). You are responsible for configuring
IAM permissions and column-level protections appropriate to the sensitivity of your data.

## Your control over your data

Because the server runs on infrastructure you control and stores data only where you configure
it to:

- You can delete the audit log file at any time; it will simply start fresh on the next call.
- You can disable the query result cache entirely (`cache_ttl_seconds: 0`).
- You can restrict or revoke the server's access at any time by rotating/removing the
  underlying Google Cloud credentials — this immediately cuts off all access to BigQuery.
- Uninstalling the server (or the Claude Code plugin) removes the running process; any local
  files it created (audit log, config) remain on disk under your control until you delete them.

## Changes to this policy

If the server's data-handling behavior changes in a future version (for example, a new caching
or logging feature), this file will be updated in the same repository and release as that
change.

## Contact

Questions about this policy or the server's data handling can be directed to Gami Solutions via
the project repository: https://github.com/GAMI-Solutions/Bigquerymcp
