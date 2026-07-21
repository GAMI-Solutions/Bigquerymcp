"""Query safety layer: statement classification, dry-run cost checks, and DDL/DML gating.

Every query that will actually execute goes through `validate_query()` first. It:
  1. Classifies the statement (SELECT / DDL / DML / other).
  2. Rejects anything not explicitly allowed by the current config.
  3. Runs BigQuery's own dry-run planner to catch syntax errors and get a real
     bytes-processed estimate *before* anything is billed or executed.
  4. Enforces `maximum_bytes_billed`.
  5. For DDL/DML, requires the caller to have passed confirm=True.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from google.cloud import bigquery

_DDL_KEYWORDS = re.compile(
    r"^\s*(CREATE|ALTER|DROP|TRUNCATE)\b", re.IGNORECASE
)
_DML_KEYWORDS = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE
)
_READ_ONLY_KEYWORDS = re.compile(
    r"^\s*(SELECT|WITH)\b", re.IGNORECASE
)
_EXPORT_KEYWORD = re.compile(r"^\s*EXPORT\b", re.IGNORECASE)


class QueryRejected(RuntimeError):
    """Raised when a query is blocked by the safety layer. Message explains why + how to fix it."""


@dataclass
class QueryPlan:
    statement_type: str  # "SELECT" | "DDL" | "DML" | "OTHER"
    total_bytes_processed: int
    estimated_usd: float
    requires_confirmation: bool


# BigQuery on-demand pricing, approx $6.25 per TiB scanned (as of mid-2025 US pricing).
# This is an estimate for guidance only, not a billing guarantee.
_USD_PER_TEBIBYTE = 6.25
_BYTES_PER_TEBIBYTE = 1024 ** 4


def classify_statement(sql: str) -> str:
    if _EXPORT_KEYWORD.match(sql):
        return "OTHER"
    if _DDL_KEYWORDS.match(sql):
        return "DDL"
    if _DML_KEYWORDS.match(sql):
        return "DML"
    if _READ_ONLY_KEYWORDS.match(sql):
        return "SELECT"
    return "OTHER"


def dry_run(client: bigquery.Client, sql: str, query_parameters: Optional[list] = None) -> QueryPlan:
    """Ask BigQuery to plan (but not execute) the query, returning cost/type info."""
    statement_type = classify_statement(sql)

    job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    if query_parameters:
        job_config.query_parameters = query_parameters

    try:
        job = client.query(sql, job_config=job_config)
    except Exception as exc:  # noqa: BLE001
        raise QueryRejected(f"Query failed BigQuery's dry-run planner: {exc}") from exc

    total_bytes = job.total_bytes_processed or 0
    estimated_usd = round((total_bytes / _BYTES_PER_TEBIBYTE) * _USD_PER_TEBIBYTE, 6)

    return QueryPlan(
        statement_type=statement_type,
        total_bytes_processed=total_bytes,
        estimated_usd=estimated_usd,
        requires_confirmation=statement_type in ("DDL", "DML"),
    )


def validate_query(
    client: bigquery.Client,
    sql: str,
    *,
    allow_ddl: bool,
    allow_dml: bool,
    maximum_bytes_billed: int,
    confirm: bool = False,
    query_parameters: Optional[list] = None,
) -> QueryPlan:
    """Validate a query against policy. Returns the QueryPlan if allowed, else raises QueryRejected."""
    statement_type = classify_statement(sql)

    if statement_type == "OTHER":
        raise QueryRejected(
            "Only SELECT, and (if enabled) DDL/DML statements are supported. "
            "EXPORT DATA and other statement types are always rejected."
        )

    if statement_type == "DDL" and not allow_ddl:
        raise QueryRejected(
            "This looks like a DDL statement (CREATE/ALTER/DROP/TRUNCATE). "
            "DDL is disabled by default. Enable it by setting allow_ddl=true in config, "
            "and pass confirm=true on the call."
        )
    if statement_type == "DML" and not allow_dml:
        raise QueryRejected(
            "This looks like a DML statement (INSERT/UPDATE/DELETE/MERGE). "
            "DML is disabled by default. Enable it by setting allow_dml=true in config, "
            "and pass confirm=true on the call."
        )

    plan = dry_run(client, sql, query_parameters=query_parameters)

    if plan.total_bytes_processed > maximum_bytes_billed:
        gb_estimate = plan.total_bytes_processed / (1024 ** 3)
        gb_limit = maximum_bytes_billed / (1024 ** 3)
        raise QueryRejected(
            f"Query would process ~{gb_estimate:.2f} GB, exceeding the configured limit of "
            f"{gb_limit:.2f} GB (maximum_bytes_billed). Narrow the query (add filters, "
            f"select fewer columns, use a smaller date range) or raise the limit in config."
        )

    if plan.requires_confirmation and not confirm:
        raise QueryRejected(
            f"This {plan.statement_type} statement would modify your warehouse "
            f"(estimated cost: ${plan.estimated_usd:.6f}, ~{plan.total_bytes_processed} bytes). "
            "Re-run with confirm=true to proceed."
        )

    return plan
