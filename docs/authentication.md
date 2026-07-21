# Authentication

`auth.py: get_bigquery_client()` resolves credentials in this order:

1. **Service account key file** — if `key_file` (config) / `--key-file` (CLI) /
   `BQMCP_KEY_FILE` (env) is set. Loaded via
   `service_account.Credentials.from_service_account_file(...)` with scope
   `https://www.googleapis.com/auth/bigquery`. Raises `AuthError` if the path doesn't exist,
   with guidance to check the path or unset `key_file` to fall back to ADC.
2. **Application Default Credentials (ADC)** — the default when no `key_file` is set. Works
   with `gcloud auth application-default login`, a `GOOGLE_APPLICATION_CREDENTIALS` env var
   pointing at a key file, or workload identity when running on GCP infrastructure. Raises
   `AuthError` with setup instructions if no ADC is found.
3. **Interactive OAuth** — only used if the caller explicitly passes
   `use_interactive_oauth=True` (not currently exposed via CLI/config/env — a code-level option
   for embedding this server in a local/dev tool without `gcloud`). Requires
   `google-auth-oauthlib`; raises `AuthError` with an install hint if it's missing. Runs an
   `InstalledAppFlow` local server flow.

If `location` is set in config, it's applied to the client after construction
(`client.location = location`) regardless of which auth method produced it.

## Connection test at startup

Immediately after building the client, `server.py: build_server()` calls
`test_connection(client)`, which runs a dry-run `SELECT 1 AS ok`. This is a zero-cost,
zero-billing check that fails fast — if the project ID is wrong, the BigQuery API isn't
enabled, or the identity lacks permissions, the server logs the error and exits (`sys.exit(1)`)
**before** registering any tools or accepting a connection, rather than surfacing the failure
on the first real tool call.

## Required IAM roles

| Mode | Minimum roles |
|---|---|
| Read-only (default: no `allow_ddl`/`allow_dml`) | `roles/bigquery.jobUser` (run/dry-run queries) + `roles/bigquery.dataViewer` on the relevant datasets |
| DDL/DML enabled | `roles/bigquery.jobUser` + `roles/bigquery.dataEditor` on the relevant datasets |
| `list_jobs(all_users=true)` | Additional project-level job-visibility permission (e.g. `roles/bigquery.admin` or an equivalent custom role) — the default (`all_users=false`) only needs the identity's own job history |

Grant roles at the dataset level where possible rather than project-wide, especially in
`auto_protect`/`allowlist` deployments — IAM is the actual security boundary; see
[security.md](security.md#protection-is-cooperative-not-a-hard-boundary).

## Choosing an auth method

- **Local development:** ADC via `gcloud auth application-default login` — no key file to
  manage or leak.
- **Claude Code plugin install:** either leave `key_file` empty (ADC) or point it at a service
  account key via the plugin's `userConfig` prompt — see
  [configuration.md](configuration.md#claude-code-plugin-config).
- **Shared/hosted deployment (`--http`):** a dedicated service account key scoped to only the
  datasets/roles the deployment needs, rather than relying on whatever ADC happens to be active
  on the host.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `No Application Default Credentials found` | Run `gcloud auth application-default login`, or set `GOOGLE_APPLICATION_CREDENTIALS`, or configure `key_file`. |
| `Service account key file not found: <path>` | Check the path passed to `key_file`/`--key-file`; confirm it's absolute or correctly relative to the server's working directory. |
| `BigQuery connection test failed for project '<id>'` | Confirm the project ID is correct, the BigQuery API is enabled on it, and the authenticated identity has at least `BigQuery Job User`. |
| Server exits immediately on startup with an auth error | Expected behavior — `test_connection` intentionally fails fast rather than starting in a broken state. Fix the underlying credential/permission issue and restart. |
