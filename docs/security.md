# Security model

Four independent layers, each opt-in/configurable except the cost guardrail which is always on.

## 1. Cost guardrails (`safety.py`) — always on

Every statement that will actually execute goes through `validate_query()`:

1. `classify_statement(sql)` — regex match on the leading keyword: `SELECT`/`WITH` → `SELECT`;
   `CREATE`/`ALTER`/`DROP`/`TRUNCATE` → `DDL`; `INSERT`/`UPDATE`/`DELETE`/`MERGE` → `DML`;
   `EXPORT` or anything else → `OTHER` (always rejected — `EXPORT DATA` and other statement
   types have no supported path).
2. Statement-type gating — DDL/DML rejected unless `allow_ddl`/`allow_dml` is set in config.
3. `dry_run()` — BigQuery's own dry-run planner runs the query with `dry_run=True,
   use_query_cache=False`, returning a real `total_bytes_processed` (not a guess) and catching
   syntax errors before anything executes.
4. Cost is estimated at **$6.25 per TiB scanned** (`_USD_PER_TEBIBYTE` — approximate on-demand
   US pricing as of mid-2025; not a billing guarantee, and not updated automatically if Google's
   pricing changes).
5. Byte cap — if `total_bytes_processed > maximum_bytes_billed`, the query is rejected with the
   GB estimate and limit spelled out, plus suggestions (add filters, select fewer columns,
   narrow date range, or raise the config limit).
6. `maximum_bytes_billed` is also passed into the actual `QueryJobConfig` at execution time —
   a second, server-enforced backstop independent of the pre-flight estimate.
7. If the statement is DDL/DML, it additionally requires `confirm=true` on the call — the
   rejection message includes the estimated cost so the caller (Claude) has cost info before
   deciding to confirm.

## 2. DDL/DML gating

Off by default (`allow_ddl`/`allow_dml` both `false`). Even when enabled in config, **every
individual mutating call still requires `confirm=true`** — enabling the config flag only lifts
the "statement type not supported" rejection; it does not bypass the per-call confirmation.
This two-key design (server-level opt-in + per-call confirmation) means a compromised or
overly-eager agent session can't silently start dropping tables just because the operator once
set `allow_ddl: true` for a legitimate table-creation task.

## 3. PII/PHI protection (`protection.py`)

`SensitiveFieldGuard` has three modes (`protection_mode` in config):

- **`off`** — `is_active()` is `False`; `check_columns()` always returns `[]` (nothing blocked).
- **`allowlist`** / **`auto_protect`** — `check_columns(table_ref, requested_columns)` looks up
  `prevented_fields[table_ref]`; if the request is `SELECT *` (`"*"` in requested columns), *all*
  restricted columns for that table are flagged; otherwise only the explicitly-requested
  restricted columns are flagged.
- Blocked requests get `guidance_for_blocked()` text back instead of a dead end: e.g.
  `SELECT * EXCEPT(email, ssn) FROM ...`, or use an aggregate (`COUNT`, `COUNT(DISTINCT ...)`)
  over the restricted column instead of selecting it directly.

**Where the guard is actually invoked:**
- `run_query` — pre-check on bare `SELECT *` against a table with restricted columns, before
  any BigQuery call.
- `preview_table` — same check, since a row preview implicitly requests every column.
- `get_table_schema` — annotates each column with `"restricted": true/false` for visibility,
  but does **not** block the schema call itself (column names/types are metadata, not values).
- `run_parameterized_query` — **not currently checked** by the guard; treat parameterized
  queries against sensitive tables the same as any other query when auditing your protection
  coverage.

**`scan_sensitive_fields`** walks every dataset/table in the project, matches column names
against `sensitive_field_patterns` (SQL `LIKE`-style, translated to regex — see
[configuration.md](configuration.md#default-sensitive-field-patterns) for the default list),
and merges matches into the live `prevented_fields` dict. This is in-memory only for the
running server process — it is not written back to `config.json`, so a restart reverts to
whatever `prevented_fields` is configured on disk until the scan is re-run.

### Protection is cooperative, not a hard boundary

> **From the project README:** `prevented_fields`/auto-scanning are cooperative guardrails for
> the AI agent, not a hard SQL firewall. Use BigQuery IAM / column-level security for a real
> security boundary; this layer keeps well-behaved agents from surfacing PII into an LLM
> conversation.

Concretely, this means:
- The table-reference extraction the guard relies on (`_guess_table_refs` in `query.py`) is a
  regex over `FROM`/`JOIN`, not a SQL parser — it can miss references inside unusually
  formatted CTEs, subqueries, or non-standard quoting.
- The `SELECT *` pre-check only fires on a literal `SELECT *`; a query that names the restricted
  column explicitly (`SELECT ssn FROM ...`) is caught by the *column-name* match, but a query
  that derives the same value indirectly (e.g. via a self-join or a computed alias) is not
  something this layer can detect.
- If you need a real enforcement boundary — e.g. compliance requirements, external/untrusted
  agents — set column-level security or row-level access policies directly in BigQuery IAM.
  This layer's job is to keep an honestly-behaving LLM agent from accidentally surfacing PII
  into a chat transcript, not to stop a deliberately adversarial query.

## 4. Query result caching (`cache.py`)

`QueryResultCache` is an in-memory, thread-safe (single `threading.Lock`), TTL-based cache keyed
on `sha256(sql || json(params) || page_token)`. Set `cache_ttl_seconds: 0` to disable entirely.
Only `SELECT` results are cached (`run_query` only calls `cache.set()` when
`plan.statement_type == "SELECT"`); DDL/DML results are never cached, and
`run_parameterized_query` never reads or writes the cache at all. The cache is process-local —
it does not persist across server restarts, and is not shared across multiple server instances.

## Audit logging (`audit.py`)

Every tool call — success or failure — is appended as one JSON line to `audit_log_path`
(default `audit-log.jsonl`), with this shape:

```json
{
  "timestamp": "2026-07-21T14:32:01Z",
  "tool": "run_query",
  "sql": "SELECT * FROM `sales.orders` WHERE region = '***REDACTED***'",
  "touched_sensitive_fields": true,
  "bytes_billed": 102400,
  "estimated_usd": 0.0000006,
  "duration_ms": 184.2,
  "success": true,
  "error": null
}
```

- **Redaction:** if `touched_sensitive_fields` is `True` for that call, every single-quoted
  string literal in the logged `sql` is replaced with `'***REDACTED***'` (`_redact_sql`) — this
  hides literal values (e.g. a filtered SSN or email) while preserving the query's shape for
  debugging. If a query never touches a flagged column, the SQL is logged verbatim.
- **Extra fields:** tool-specific context (`table_ref`, `job_id`, `dataset_id`, scan counts,
  etc.) is merged in via the `extra` dict per call site — see [tools.md](tools.md) for which
  tools attach what.
- **Access:** the log is a plain file on disk (reviewable by an operator directly), and also
  queryable by Claude itself via the `get_audit_log` tool, which returns the last N entries
  (`AuditLog.tail()`).
- **Concurrency:** writes are guarded by a `threading.Lock`; reads (`tail()`) take the same lock
  and read the whole file, then return the last `limit` lines — fine for the JSON-lines file
  sizes this is designed for, not optimized for huge audit logs.

## Authentication surface

See [authentication.md](authentication.md) for credential resolution order and required IAM
roles. In short: **BigQuery Job User** at minimum (to run/dry-run queries), plus **BigQuery Data
Viewer** for read-only mode or **BigQuery Data Editor** if `allow_ddl`/`allow_dml` is enabled.
