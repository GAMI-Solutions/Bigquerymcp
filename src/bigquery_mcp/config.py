"""Configuration model for bigquery-mcp.

Config can come from (in increasing priority):
  1. Built-in defaults (safe: read-only, no protection, no DDL/DML).
  2. A JSON config file (--config-file).
  3. Environment variables (BQMCP_*).
  4. CLI flags, applied by the caller after loading this model.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ProtectionMode = Literal["off", "allowlist", "auto_protect"]

DEFAULT_SENSITIVE_PATTERNS = [
    "%first_name%", "%last_name%", "%full_name%", "%email%",
    "%ssn%", "%social_security%", "%date_of_birth%", "%dob%",
    "%password%", "%secret%", "%api_key%", "%token%",
    "%credit_card%", "%card_number%", "%bank_account%", "%iban%",
    "%phone_number%", "%address%", "%passport%",
]


class BigQueryMCPConfig(BaseModel):
    project_id: str = Field(..., description="Google Cloud project ID to query against.")
    location: str = Field(default="US", description="BigQuery dataset location / region.")
    key_file: Optional[str] = Field(
        default=None, description="Path to a service account key JSON file. If unset, ADC is used."
    )

    maximum_bytes_billed: int = Field(
        default=1_000_000_000,
        description="Hard cap on bytes billed per query (default 1GB). Queries exceeding this are refused.",
    )

    protection_mode: ProtectionMode = Field(
        default="off",
        description=(
            "'off': no field restrictions. "
            "'allowlist': only tables/fields explicitly permitted are queryable. "
            "'auto_protect': sensitive columns are auto-discovered and masked/blocked."
        ),
    )
    prevented_fields: dict[str, list[str]] = Field(
        default_factory=dict, description="Map of 'dataset.table' -> list of restricted column names."
    )
    sensitive_field_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
        description="SQL LIKE-style patterns used by the sensitive-field auto-scanner.",
    )
    sensitive_field_scan_frequency_days: int = Field(
        default=1, description="Days between automatic sensitive-field scans (0 disables auto-scan)."
    )

    cache_ttl_seconds: int = Field(
        default=300, description="TTL for cached query results, keyed by query hash. 0 disables caching."
    )

    allow_ddl: bool = Field(
        default=False, description="If True, CREATE/ALTER/DROP TABLE statements are permitted (with confirm=true)."
    )
    allow_dml: bool = Field(
        default=False, description="If True, INSERT/UPDATE/DELETE/MERGE statements are permitted (with confirm=true)."
    )

    audit_log_path: str = Field(
        default="audit-log.jsonl", description="Path to the JSON-lines audit log file."
    )

    @field_validator("maximum_bytes_billed")
    @classmethod
    def _positive_bytes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("maximum_bytes_billed must be positive")
        return v


ENV_PREFIX = "BQMCP_"

# Maps environment variable suffix -> (config field name, type caster)
_ENV_FIELD_MAP = {
    "PROJECT_ID": ("project_id", str),
    "LOCATION": ("location", str),
    "KEY_FILE": ("key_file", str),
    "MAXIMUM_BYTES_BILLED": ("maximum_bytes_billed", int),
    "PROTECTION_MODE": ("protection_mode", str),
    "CACHE_TTL_SECONDS": ("cache_ttl_seconds", int),
    "ALLOW_DDL": ("allow_ddl", lambda v: v.lower() in ("1", "true", "yes")),
    "ALLOW_DML": ("allow_dml", lambda v: v.lower() in ("1", "true", "yes")),
    "AUDIT_LOG_PATH": ("audit_log_path", str),
}


def load_config(config_file: Optional[str] = None, overrides: Optional[dict] = None) -> BigQueryMCPConfig:
    """Load config from file (if given), layer environment variables on top, then explicit overrides."""
    data: dict = {}

    if config_file:
        path = Path(config_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        data = json.loads(path.read_text())

    for env_suffix, (field_name, caster) in _ENV_FIELD_MAP.items():
        env_value = os.environ.get(f"{ENV_PREFIX}{env_suffix}")
        if env_value is not None:
            data[field_name] = caster(env_value)

    if overrides:
        data.update({k: v for k, v in overrides.items() if v is not None})

    if "project_id" not in data or not data["project_id"]:
        raise ValueError(
            "project_id is required. Set it via config.json, BQMCP_PROJECT_ID, or --project-id."
        )

    return BigQueryMCPConfig(**data)
