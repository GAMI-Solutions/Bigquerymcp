# Configuration reference

Config is a `BigQueryMCPConfig` pydantic model (`config.py`). Sources are merged in this
order, **later wins**:

```
1. Built-in defaults          (safe: read-only, no protection, no DDL/DML)
2. --config-file <path>.json  (must exist if passed, else FileNotFoundError)
3. BQMCP_* environment variables
4. CLI flags (--project-id, --allow-ddl, etc.)
```

This is implemented in `load_config()`: file contents are loaded into a dict first, then each
`BQMCP_*` env var overwrites the matching key if set, then any non-`None` CLI override
overwrites again. `project_id` is required by the end of this chain — if it's still missing,
`load_config` raises `ValueError` before the server starts.

## Fields

| Field | Env var | CLI flag | Default | Notes |
|---|---|---|---|---|
| `project_id` | `BQMCP_PROJECT_ID` | `--project-id` | *(required)* | GCP project to query against. |
| `location` | `BQMCP_LOCATION` | `--location` | `"US"` | BigQuery dataset location/region (e.g. `us-central1`, `EU`). |
| `key_file` | `BQMCP_KEY_FILE` | `--key-file` | `null` | Path to a service account key JSON. Unset → ADC. |
| `maximum_bytes_billed` | `BQMCP_MAXIMUM_BYTES_BILLED` | `--maximum-bytes-billed` | `1000000000` (1 GB) | Hard cap on bytes billed per query; must be positive. |
| `protection_mode` | `BQMCP_PROTECTION_MODE` | *(config file only)* | `"off"` | `off` / `allowlist` / `auto_protect`. |
| `prevented_fields` | *(config file only)* | — | `{}` | `{"dataset.table": ["col1", "col2"]}`. |
| `sensitive_field_patterns` | *(config file only)* | — | built-in list (see below) | SQL `LIKE`-style patterns for the auto-scanner. |
| `sensitive_field_scan_frequency_days` | *(config file only)* | — | `1` | Not currently wired to an automatic scheduler — informational field; trigger scans manually via `scan_sensitive_fields`. `0` would disable auto-scan if a scheduler is added later. |
| `cache_ttl_seconds` | `BQMCP_CACHE_TTL_SECONDS` | — | `300` | `0` disables the result cache entirely. |
| `allow_ddl` | `BQMCP_ALLOW_DDL` | `--allow-ddl` | `false` | Enables `CREATE`/`ALTER`/`DROP`/`TRUNCATE` (still needs `confirm=true` per call). |
| `allow_dml` | `BQMCP_ALLOW_DML` | `--allow-dml` | `false` | Enables `INSERT`/`UPDATE`/`DELETE`/`MERGE` (still needs `confirm=true` per call). |
| `audit_log_path` | `BQMCP_AUDIT_LOG_PATH` | — | `"audit-log.jsonl"` | JSON-lines file, appended to on every tool call. |

Boolean env vars (`BQMCP_ALLOW_DDL`, `BQMCP_ALLOW_DML`) accept `1`/`true`/`yes` (case-insensitive)
as truthy; anything else is falsy.

`--allow-ddl`/`--allow-dml` are CLI *flags* (`action="store_true"`, default `None`) — passing
them sets `true`; omitting them means "no override," falling through to the env var or file
value rather than forcing `false`.

## `protection_mode` values

- **`off`** — no restrictions. `SensitiveFieldGuard.is_active()` returns `False`; no column is
  ever blocked.
- **`allowlist`** — intended for "only listed tables/fields are queryable"; in the current
  implementation this behaves like `auto_protect` for column-level blocking via
  `prevented_fields` (there is no separate table-allowlist enforcement yet — see
  [security.md](security.md) for exact behavior).
- **`auto_protect`** — same column blocking, plus `scan_sensitive_fields` will discover and
  merge new sensitive columns into `prevented_fields` as it finds them.

## Default sensitive-field patterns

Used by the auto-scanner (`protection.py: DEFAULT_SENSITIVE_PATTERNS`) unless overridden by
`sensitive_field_patterns` in the config file:

```
%first_name%   %last_name%    %full_name%      %email%
%ssn%          %social_security%  %date_of_birth%  %dob%
%password%     %secret%       %api_key%        %token%
%credit_card%  %card_number%  %bank_account%   %iban%
%phone_number% %address%      %passport%
```

These are SQL `LIKE`-style patterns matched case-insensitively against column names
(`%` → any substring, `_` → any single character), not full SQL — translated internally to
regex by `_like_to_regex()`.

## Example `config.json`

See [`config.example.json`](../config.example.json) in the repo root for a working example with
`prevented_fields` set on a healthcare and a billing table.

## Claude Code plugin config

When installed via `/plugin install bigquery-mcp@gami-solutions`, Claude Code prompts for the
`userConfig` fields declared in [`.claude-plugin/plugin.json`](../.claude-plugin/plugin.json)
(`project_id`, `location`, `key_file`, `protection_mode`, `allow_ddl`, `allow_dml`) and injects
them as `BQMCP_*` environment variables per [`.mcp.json`](../.mcp.json), which runs the server
via `uvx --from git+https://github.com/GAMI-Solutions/Bigquerymcp.git bigquery-mcp`. Fields not
exposed in `userConfig` (`prevented_fields`, `sensitive_field_patterns`, `cache_ttl_seconds`,
`maximum_bytes_billed`, `audit_log_path`) fall back to defaults unless you also pass
`--config-file` manually.
