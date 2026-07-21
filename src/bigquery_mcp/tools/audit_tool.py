"""MCP tool exposing the audit log itself, so Claude can review recent activity
for troubleshooting or cost review without needing filesystem access.
"""
from __future__ import annotations

from typing import Any

from ..audit import AuditLog


def register(mcp, *, audit: AuditLog) -> None:
    @mcp.tool()
    def get_audit_log(limit: int = 50) -> dict[str, Any]:
        """Return the most recent entries from the audit log (tool calls, queries, costs, errors)."""
        entries = audit.tail(limit=limit)
        return {"count": len(entries), "entries": entries}
