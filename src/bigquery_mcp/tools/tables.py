"""Dataset & table management MCP tools — the parts most BigQuery MCP servers skip.

Tools registered here:
  - list_datasets, list_tables, get_table_schema, get_table_metadata, preview_table
    (all read-only, no dry-run needed since they use metadata APIs, not query jobs)
  - create_table, delete_table, create_view (gated behind allow_ddl + confirm=true)
  - scan_sensitive_fields (triggers the PII/PHI auto-scanner from protection.py)
"""
from __future__ import annotations

import time
from typing import Any, Optional

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from ..audit import AuditLog
from ..protection import SensitiveFieldGuard


def register(
    mcp,
    client: bigquery.Client,
    *,
    project_id: str,
    allow_ddl: bool,
    guard: SensitiveFieldGuard,
    audit: AuditLog,
) -> None:
    @mcp.tool()
    def list_datasets() -> dict[str, Any]:
        """List all BigQuery datasets in the configured project."""
        datasets = [d.dataset_id for d in client.list_datasets(project=project_id)]
        audit.record(tool="list_datasets", success=True)
        return {"project_id": project_id, "datasets": datasets}

    @mcp.tool()
    def list_tables(dataset_id: str) -> dict[str, Any]:
        """List all tables and views within a dataset, labeled by type."""
        try:
            items = client.list_tables(f"{project_id}.{dataset_id}")
        except NotFound as exc:
            return {"error": f"Dataset '{dataset_id}' not found: {exc}"}

        tables = [{"table_id": item.table_id, "type": item.table_type} for item in items]
        audit.record(tool="list_tables", success=True, extra={"dataset_id": dataset_id})
        return {"dataset_id": dataset_id, "tables": tables}

    @mcp.tool()
    def get_table_schema(dataset_id: str, table_id: str) -> dict[str, Any]:
        """Return the column schema (name, type, mode, description) for a table or view."""
        table_ref = f"{dataset_id}.{table_id}"
        try:
            table = client.get_table(f"{project_id}.{dataset_id}.{table_id}")
        except NotFound as exc:
            return {"error": f"Table '{table_ref}' not found: {exc}"}

        restricted = guard.restricted_columns_for(table_ref)
        schema = [
            {
                "name": field.name,
                "type": field.field_type,
                "mode": field.mode,
                "description": field.description,
                "restricted": field.name in restricted,
            }
            for field in table.schema
        ]
        audit.record(tool="get_table_schema", success=True, extra={"table_ref": table_ref})
        return {"table_ref": table_ref, "schema": schema}

    @mcp.tool()
    def get_table_metadata(dataset_id: str, table_id: str) -> dict[str, Any]:
        """Return row count, size, partitioning/clustering, and last-modified time for a table."""
        table_ref = f"{dataset_id}.{table_id}"
        try:
            table = client.get_table(f"{project_id}.{dataset_id}.{table_id}")
        except NotFound as exc:
            return {"error": f"Table '{table_ref}' not found: {exc}"}

        audit.record(tool="get_table_metadata", success=True, extra={"table_ref": table_ref})
        return {
            "table_ref": table_ref,
            "num_rows": table.num_rows,
            "num_bytes": table.num_bytes,
            "created": table.created.isoformat() if table.created else None,
            "modified": table.modified.isoformat() if table.modified else None,
            "table_type": table.table_type,
            "partitioning": table.time_partitioning.type_ if table.time_partitioning else None,
            "clustering_fields": table.clustering_fields,
        }

    @mcp.tool()
    def preview_table(dataset_id: str, table_id: str, max_rows: int = 10) -> dict[str, Any]:
        """Cheaply sample rows from a table via the tabledata API (no query job, no billing)."""
        table_ref = f"{dataset_id}.{table_id}"
        blocked = guard.check_columns(table_ref, ["*"])
        if blocked:
            return {"error": guard.guidance_for_blocked(table_ref, blocked)}

        try:
            table = client.get_table(f"{project_id}.{dataset_id}.{table_id}")
        except NotFound as exc:
            return {"error": f"Table '{table_ref}' not found: {exc}"}

        rows = [dict(row.items()) for row in client.list_rows(table, max_results=max_rows)]
        audit.record(tool="preview_table", success=True, extra={"table_ref": table_ref})
        return {"table_ref": table_ref, "rows": rows}

    @mcp.tool()
    def create_table(
        dataset_id: str,
        table_id: str,
        schema: list[dict[str, str]],
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a new table. Requires allow_ddl=true in config and confirm=true.

        `schema` is a list of {"name": ..., "type": ..., "mode": "NULLABLE"|"REQUIRED"|"REPEATED"}.
        """
        if not allow_ddl:
            return {"error": "Table creation is disabled. Set allow_ddl=true in config to enable it."}
        if not confirm:
            return {"error": "This will create a new table. Re-run with confirm=true to proceed."}

        bq_schema = [
            bigquery.SchemaField(f["name"], f["type"], mode=f.get("mode", "NULLABLE"))
            for f in schema
        ]
        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        table = bigquery.Table(table_ref, schema=bq_schema)
        start = time.time()
        try:
            created = client.create_table(table)
        except Exception as exc:  # noqa: BLE001
            audit.record(tool="create_table", success=False, error=str(exc), extra={"table_ref": table_ref})
            return {"error": str(exc)}

        audit.record(
            tool="create_table", success=True,
            duration_ms=(time.time() - start) * 1000,
            extra={"table_ref": table_ref},
        )
        return {"created": f"{dataset_id}.{table_id}", "num_fields": len(created.schema)}

    @mcp.tool()
    def delete_table(dataset_id: str, table_id: str, confirm: bool = False) -> dict[str, Any]:
        """Delete a table. Requires allow_ddl=true in config and confirm=true. Irreversible."""
        if not allow_ddl:
            return {"error": "Table deletion is disabled. Set allow_ddl=true in config to enable it."}
        if not confirm:
            return {
                "error": f"This will permanently delete '{dataset_id}.{table_id}'. "
                         "Re-run with confirm=true to proceed."
            }

        table_ref = f"{project_id}.{dataset_id}.{table_id}"
        start = time.time()
        try:
            client.delete_table(table_ref, not_found_ok=False)
        except Exception as exc:  # noqa: BLE001
            audit.record(tool="delete_table", success=False, error=str(exc), extra={"table_ref": table_ref})
            return {"error": str(exc)}

        audit.record(
            tool="delete_table", success=True,
            duration_ms=(time.time() - start) * 1000,
            extra={"table_ref": table_ref},
        )
        return {"deleted": f"{dataset_id}.{table_id}"}

    @mcp.tool()
    def create_view(
        dataset_id: str,
        view_id: str,
        view_query: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Create a view backed by a SQL query. Requires allow_ddl=true in config and confirm=true."""
        if not allow_ddl:
            return {"error": "View creation is disabled. Set allow_ddl=true in config to enable it."}
        if not confirm:
            return {"error": "This will create a new view. Re-run with confirm=true to proceed."}

        table_ref = f"{project_id}.{dataset_id}.{view_id}"
        view = bigquery.Table(table_ref)
        view.view_query = view_query
        start = time.time()
        try:
            client.create_table(view)
        except Exception as exc:  # noqa: BLE001
            audit.record(tool="create_view", success=False, error=str(exc), extra={"table_ref": table_ref})
            return {"error": str(exc)}

        audit.record(
            tool="create_view", success=True,
            duration_ms=(time.time() - start) * 1000,
            extra={"table_ref": table_ref},
        )
        return {"created_view": f"{dataset_id}.{view_id}"}

    @mcp.tool()
    def scan_sensitive_fields() -> dict[str, Any]:
        """Scan every dataset/table for columns matching sensitive-field patterns (PII/PHI/secrets).

        Updates the in-memory protection list for this session. Only meaningful when
        protection_mode is 'auto_protect' or 'allowlist'.
        """
        start = time.time()
        result = guard.scan(client, project_id)
        audit.record(
            tool="scan_sensitive_fields", success=True,
            duration_ms=(time.time() - start) * 1000,
            extra={"scanned_tables": result.scanned_tables, "flagged": len(result.flagged_columns)},
        )
        return {
            "scanned_tables": result.scanned_tables,
            "flagged_columns": result.flagged_columns,
        }
