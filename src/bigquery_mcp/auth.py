"""Authentication for bigquery-mcp.

Resolution order:
  1. Explicit service account key file (config.key_file).
  2. Application Default Credentials (`gcloud auth application-default login`,
     or GOOGLE_APPLICATION_CREDENTIALS env var, or workload identity on GCP).
  3. Interactive OAuth user credentials (for local/dev setups without gcloud).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery
from google.oauth2 import service_account

logger = logging.getLogger("bigquery_mcp.auth")


class AuthError(RuntimeError):
    """Raised when BigQuery authentication fails, with actionable guidance."""


def _client_from_key_file(key_file: str, project_id: str) -> bigquery.Client:
    path = Path(key_file).expanduser()
    if not path.exists():
        raise AuthError(
            f"Service account key file not found: {path}. "
            "Check the path in your config, or unset key_file to fall back to ADC."
        )
    credentials = service_account.Credentials.from_service_account_file(
        str(path),
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=project_id, credentials=credentials)


def _client_from_adc(project_id: str) -> bigquery.Client:
    try:
        return bigquery.Client(project=project_id)
    except DefaultCredentialsError as exc:
        raise AuthError(
            "No Application Default Credentials found. Run "
            "`gcloud auth application-default login`, or set GOOGLE_APPLICATION_CREDENTIALS, "
            "or provide a service account key via key_file / --key-file."
        ) from exc


def _client_from_interactive_oauth(project_id: str) -> bigquery.Client:
    """Interactive OAuth flow for user credentials, for setups without gcloud installed."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise AuthError(
            "Interactive OAuth requires google-auth-oauthlib. Install it with "
            "`pip install google-auth-oauthlib`, or use ADC / a service account key instead."
        ) from exc

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": "",
                "client_secret": "",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    credentials = flow.run_local_server(port=0)
    return bigquery.Client(project=project_id, credentials=credentials)


def get_bigquery_client(
    project_id: str,
    key_file: Optional[str] = None,
    location: Optional[str] = None,
    use_interactive_oauth: bool = False,
) -> bigquery.Client:
    """Return an authenticated BigQuery client, trying each auth method in order."""
    if key_file:
        logger.info("Authenticating via service account key file.")
        client = _client_from_key_file(key_file, project_id)
    elif use_interactive_oauth:
        logger.info("Authenticating via interactive OAuth.")
        client = _client_from_interactive_oauth(project_id)
    else:
        logger.info("Authenticating via Application Default Credentials.")
        client = _client_from_adc(project_id)

    if location:
        client.location = location

    return client


def test_connection(client: bigquery.Client) -> None:
    """Run a trivial query to confirm credentials and project access work before the server starts."""
    try:
        job = client.query("SELECT 1 AS ok", job_config=bigquery.QueryJobConfig(dry_run=True))
        _ = job.total_bytes_processed
    except Exception as exc:  # noqa: BLE001 - we want to wrap any failure with guidance
        raise AuthError(
            f"BigQuery connection test failed for project '{client.project}': {exc}. "
            "Check that the project ID is correct, the BigQuery API is enabled, "
            "and the authenticated identity has at least 'BigQuery Job User' on the project."
        ) from exc
