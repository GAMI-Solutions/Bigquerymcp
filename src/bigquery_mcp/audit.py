"""Structured JSON-lines audit logging for every tool call.

Every entry records what was asked, whether it touched restricted data, how much it cost,
and whether it succeeded — so an admin (or Claude itself, via get_audit_log) can review
activity for troubleshooting, cost review, or compliance.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Redact anything that looks like it might contain sensitive literal values in logged SQL.
_STRING_LITERAL = re.compile(r"'[^']*'")


def _redact_sql(sql: str, touched_sensitive: bool) -> str:
    if not touched_sensitive:
        return sql
    return _STRING_LITERAL.sub("'***REDACTED***'", sql)


class AuditLog:
    def __init__(self, path: str = "audit-log.jsonl"):
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()

    def record(
        self,
        *,
        tool: str,
        sql: Optional[str] = None,
        touched_sensitive_fields: bool = False,
        bytes_billed: Optional[int] = None,
        estimated_usd: Optional[float] = None,
        duration_ms: Optional[float] = None,
        success: bool = True,
        error: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool": tool,
            "sql": _redact_sql(sql, touched_sensitive_fields) if sql else None,
            "touched_sensitive_fields": touched_sensitive_fields,
            "bytes_billed": bytes_billed,
            "estimated_usd": estimated_usd,
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
        }
        if extra:
            entry.update(extra)

        line = json.dumps(entry, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def tail(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        tail_lines = lines[-limit:]
        results = []
        for line in tail_lines:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results
