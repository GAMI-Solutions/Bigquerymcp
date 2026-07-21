"""Shared test fixtures: a fake FastMCP recorder and fake BigQuery client primitives.

We avoid depending on FastMCP's internal tool-registry implementation by using a tiny
stand-in whose .tool() decorator just stores the wrapped function in a dict, keyed by
its name. This lets us call the exact same tool bodies that `register()` defines,
without spinning up a real MCP transport.
"""
from __future__ import annotations

import pytest


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


class FakeRow:
    def __init__(self, data: dict):
        self._data = data

    def items(self):
        return self._data.items()


class FakeRowIterator:
    def __init__(self, rows: list[dict], next_page_token: str | None = None, page_size: int | None = None):
        self._rows = rows
        self.next_page_token = next_page_token
        self._page_size = page_size or len(rows) or 1

    @property
    def pages(self):
        rows = [FakeRow(r) for r in self._rows]
        page_size = self._page_size
        for i in range(0, len(rows), page_size) or [0]:
            yield rows[i:i + page_size]


class FakeQueryJob:
    """Stands in for both a dry-run plan and an executed query job."""

    def __init__(self, total_bytes_processed: int = 1000, rows: list[dict] | None = None,
                 next_page_token: str | None = None, page_size: int | None = None):
        self.total_bytes_processed = total_bytes_processed
        self.total_bytes_billed = total_bytes_processed
        self.cache_hit = False
        self._rows = rows or []
        self._next_page_token = next_page_token
        self._page_size = page_size

    def result(self, page_size: int | None = None, start_index=None):
        return FakeRowIterator(self._rows, self._next_page_token, page_size=self._page_size or page_size)


class FakeBigQueryClient:
    """Minimal stand-in for google.cloud.bigquery.Client used across tests."""

    def __init__(self, query_job: FakeQueryJob | None = None):
        self.project = "test-project"
        self.location = "US"
        self._query_job = query_job or FakeQueryJob()
        self.query_calls: list[tuple] = []

    def query(self, sql, job_config=None):
        self.query_calls.append((sql, job_config))
        return self._query_job


@pytest.fixture
def fake_mcp():
    return FakeMCP()


@pytest.fixture
def fake_client():
    return FakeBigQueryClient()
