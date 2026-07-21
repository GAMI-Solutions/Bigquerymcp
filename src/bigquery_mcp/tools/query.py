"""Query execution & cost-estimation MCP tools.

Tools registered here:
  - estimate_query_cost     : dry-run only, no billing, no data returned.
  - run_query               : validated, paginated, cached SELECT execution.
  - run_parameterized_query : same as run_query but with named/positional bind
                              parameters instead of string-formatted SQL.
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

from google.cloud import bigquery

from ..audit import AuditLog
from ..cache import QueryResultCache
from ..protection import SensitiveFieldGuard
from ..safety import QueryRejected, validate_query

_TABLE_REF_PATTERN = re.compile(
    r"(?:FROM|JOIN)\s+`?([a-zA-Z0-9_\-]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+|[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)`?",
    re.IGNORECASE,
)


def _guess_table_refs(sql: str) -> list[str]:
    """Best-effort extraction of dataset.table references from FROM/JOIN clauses.

    This is a heuristic, not a real SQL parser — good enough to flag likely sensitive-table
    access for the protection layer, but callers should not rely on it for hard security
    guarantees (see README's cooperative-guardrail caveat).
    """
    refs = []
    for match in _TABLE_REF_PATTERN.finditer(sql):
        parts = match.group(1).split(".")
        refs.append(".".join(parts[-2:]))  # normalize to dataset.table
    return refs


def _rows_to_dicts(rows_iterator, max_rows: int) -> tuple[list[dict], Optional[str]]:
    rows = []
    page_token = None
    pages = rows_iterator.pages
    for page in pages:
        for row in page:
            rows.append(dict(row.items()))
            if len(rows) >= max_rows:
                page_token = rows_iterator.next_page_token
                return rows, page_token
    return rows, None


def register(
    mcp,
    client: bigquery.Client,
    *,
    maximum_bytes_billed: int,
    allow_ddl: bool,
    allow_dml: bool,
    cache: QueryResultCache,
    guard: SensitiveFieldGuard,
    audit: AuditLog,
) -> None:
    @mcp.tool()
    def estimate_query_cost(sql: str) -> dict[str, Any]:
        """Dry-run a SQL query against BigQuery to estimate bytes scanned and USD cost.

        Never executes the query and never bills anything. Use this before run_query
        on anything that might scan a large table.
        """
        start = time.time()
        try:
            from ..safety import dry_run

            plan = dry_run(client, sql)
            audit.record(
                tool="estimate_query_cost",
                sql=sql,
                bytes_billed=plan.total_bytes_processed,
                estimated_usd=plan.estimated_usd,
                duration_ms=(time.time() - start) * 1000,
                success=True,
            )
            return {
                "statement_type": plan.statement_type,
                "total_bytes_processed": plan.total_bytes_processed,
                "estimated_usd": plan.estimated_usd,
                "would_require_confirmation": plan.requires_confirmation,
            }
        except QueryRejected as exc:
            audit.record(tool="estimate_query_cost", sql=sql, success=False, error=str(exc))
            return {"error": str(exc)}

    @mcp.tool()
    def run_query(
        sql: str,
        max_rows: int = 1000,
        page_token: Optional[str] = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Run a SQL query against BigQuery and return results, paginated.

        Read-only (SELECT) by default. DDL/DML require allow_ddl/allow_dml in config
        plus confirm=true. Large results are paginated via max_rows/page_token so this
        never dumps an entire multi-GB table into one response. Identical read queries
        are served from cache within the configured TTL.
        """
        table_refs = _guess_table_refs(sql)
        touched_sensitive = False
        for table_ref in table_refs:
            blocked = guard.check_columns(table_ref, ["*"])
            if blocked and re.search(r"\bSELECT\s+\*", sql, re.IGNORECASE):
                touched_sensitive = True
                msg = guard.guidance_for_blocked(table_ref, blocked)
                audit.record(tool="run_query", sql=sql, touched_sensitive_fields=True, success=False, error=msg)
                return {"error": msg}

        cached = cache.get(sql, page_token=page_token)
        if cached is not None:
            return {**cached, "cache_hit": True}

        start = time.time()
        try:
            plan = validate_query(
                client,
                sql,
                allow_ddl=allow_ddl,
                allow_dml=allow_dml,
                maximum_bytes_billed=maximum_bytes_billed,
                confirm=confirm,
            )
        except QueryRejected as exc:
            audit.record(tool="run_query", sql=sql, success=False, error=str(exc))
            return {"error": str(exc)}

        job_config = bigquery.QueryJobConfig(maximum_bytes_billed=maximum_bytes_billed)
        job = client.query(sql, job_config=job_config)
        rows_iterator = job.result(page_size=max_rows, start_index=None)
        rows, next_token = _rows_to_dicts(rows_iterator, max_rows)

        result = {
            "rows": rows,
            "row_count": len(rows),
            "next_page_token": next_token,
            "bytes_processed": plan.total_bytes_processed,
            "estimated_usd": plan.estimated_usd,
            "cache_hit": False,
        }

        if plan.statement_type == "SELECT":
            cache.set(sql, result, page_token=page_token)

        audit.record(
            tool="run_query",
            sql=sql,
            touched_sensitive_fields=touched_sensitive,
            bytes_billed=plan.total_bytes_processed,
            estimated_usd=plan.estimated_usd,
            duration_ms=(time.time() - start) * 1000,
            success=True,
        )
        return result

    @mcp.tool()
    def run_parameterized_query(
        sql: str,
        parameters: dict[str, Any],
        max_rows: int = 1000,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Run a SQL query using named bind parameters (@name syntax) instead of string-formatted SQL.

        Prefer this over run_query whenever any part of the query is derived from user input,
        to avoid SQL injection. `parameters` maps bind-parameter names to Python values
        (str, int, float, bool).
        """
        query_parameters = []
        for name, value in parameters.items():
            if isinstance(value, bool):
                param_type = "BOOL"
            elif isinstance(value, int):
                param_type = "INT64"
            elif isinstance(value, float):
                param_type = "FLOAT64"
            else:
                param_type = "STRING"
            query_parameters.append(bigquery.ScalarQueryParameter(name, param_type, value))

        start = time.time()
        try:
            plan = validate_query(
                client,
                sql,
                allow_ddl=allow_ddl,
                allow_dml=allow_dml,
                maximum_bytes_billed=maximum_bytes_billed,
                confirm=confirm,
                query_parameters=query_parameters,
            )
        except QueryRejected as exc:
            audit.record(tool="run_parameterized_query", sql=sql, success=False, error=str(exc))
            return {"error": str(exc)}

        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=maximum_bytes_billed,
            query_parameters=query_parameters,
        )
        job = client.query(sql, job_config=job_config)
        rows_iterator = job.result(page_size=max_rows)
        rows, next_token = _rows_to_dicts(rows_iterator, max_rows)

        audit.record(
            tool="run_parameterized_query",
            sql=sql,
            bytes_billed=plan.total_bytes_processed,
            estimated_usd=plan.estimated_usd,
            duration_ms=(time.time() - start) * 1000,
            success=True,
        )
        return {
            "rows": rows,
            "row_count": len(rows),
            "next_page_token": next_token,
            "bytes_processed": plan.total_bytes_processed,
            "estimated_usd": plan.estimated_usd,
        }
