"""Sensitive-field (PII/PHI) protection layer.

Three modes (config.protection_mode):
  off           - no restrictions at all.
  allowlist     - only tables explicitly listed are queryable; prevented_fields still applies
                  within those tables.
  auto_protect  - scan_sensitive_fields() discovers columns matching sensitive_field_patterns
                  across every dataset/table and merges them into prevented_fields automatically.

When a query references a restricted column, we don't just fail — we return guidance on how
to reformulate the query (aggregate, or SELECT * EXCEPT(...)) so the agent stays useful without
seeing raw sensitive values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from google.cloud import bigquery


def _like_to_regex(pattern: str) -> re.Pattern:
    """Crude LIKE-pattern -> regex translation, good enough for simple %substr% patterns."""
    escaped = re.escape(pattern).replace("%", ".*").replace("_", ".")
    return re.compile(f"^{escaped}$", re.IGNORECASE)


@dataclass
class ScanResult:
    scanned_tables: int
    flagged_columns: dict[str, list[str]] = field(default_factory=dict)  # "dataset.table" -> [columns]


class SensitiveFieldGuard:
    def __init__(
        self,
        protection_mode: str,
        prevented_fields: dict[str, list[str]],
        sensitive_field_patterns: list[str],
    ):
        self.protection_mode = protection_mode
        self.prevented_fields: dict[str, set[str]] = {
            table: set(cols) for table, cols in prevented_fields.items()
        }
        self.patterns = [_like_to_regex(p) for p in sensitive_field_patterns]

    def is_active(self) -> bool:
        return self.protection_mode != "off"

    def restricted_columns_for(self, table_ref: str) -> set[str]:
        return self.prevented_fields.get(table_ref, set())

    def check_columns(self, table_ref: str, requested_columns: list[str]) -> list[str]:
        """Return the subset of requested_columns that are blocked for this table."""
        if not self.is_active():
            return []
        restricted = self.restricted_columns_for(table_ref)
        if not restricted:
            return []
        if "*" in requested_columns:
            return sorted(restricted)
        return [c for c in requested_columns if c in restricted]

    def guidance_for_blocked(self, table_ref: str, blocked_columns: list[str]) -> str:
        cols = ", ".join(blocked_columns)
        return (
            f"Columns [{cols}] on '{table_ref}' are restricted by data protection policy "
            f"(protection_mode={self.protection_mode}) and cannot be returned as raw values. "
            f"Try instead: SELECT * EXCEPT({cols}) FROM `{table_ref}` to get the rest "
            f"of the row, or use an aggregate (COUNT, COUNT(DISTINCT ...), etc.) over the "
            f"restricted column instead of selecting it directly."
        )

    def scan(self, client: bigquery.Client, project_id: str) -> ScanResult:
        """Scan every dataset/table in the project for columns matching sensitive patterns."""
        flagged: dict[str, list[str]] = {}
        scanned = 0

        for dataset in client.list_datasets(project=project_id):
            dataset_id = dataset.dataset_id
            for table_item in client.list_tables(dataset.reference):
                table = client.get_table(table_item.reference)
                scanned += 1
                table_ref = f"{dataset_id}.{table_item.table_id}"
                matches = [
                    field_schema.name
                    for field_schema in table.schema
                    if any(p.match(field_schema.name) for p in self.patterns)
                ]
                if matches:
                    flagged[table_ref] = matches
                    existing = self.prevented_fields.setdefault(table_ref, set())
                    existing.update(matches)

        return ScanResult(scanned_tables=scanned, flagged_columns=flagged)
