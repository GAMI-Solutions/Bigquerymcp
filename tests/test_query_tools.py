from bigquery_mcp.audit import AuditLog
from bigquery_mcp.cache import QueryResultCache
from bigquery_mcp.protection import SensitiveFieldGuard
from bigquery_mcp.tools import query as query_tools

from .conftest import FakeBigQueryClient, FakeQueryJob


def _register(client, tmp_path, ttl=60, protection_mode="off", prevented_fields=None):
    from .conftest import FakeMCP

    mcp = FakeMCP()
    cache = QueryResultCache(ttl_seconds=ttl)
    guard = SensitiveFieldGuard(
        protection_mode=protection_mode,
        prevented_fields=prevented_fields or {},
        sensitive_field_patterns=["%ssn%"],
    )
    audit = AuditLog(path=str(tmp_path / "audit.jsonl"))
    query_tools.register(
        mcp, client,
        maximum_bytes_billed=1_000_000_000,
        allow_ddl=False,
        allow_dml=False,
        cache=cache,
        guard=guard,
        audit=audit,
    )
    return mcp, cache, guard, audit


def test_estimate_query_cost(tmp_path):
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=1024 ** 4))  # 1 TiB
    mcp, *_ = _register(client, tmp_path)
    result = mcp.tools["estimate_query_cost"]("SELECT * FROM t")
    assert result["statement_type"] == "SELECT"
    assert result["total_bytes_processed"] == 1024 ** 4
    assert round(result["estimated_usd"], 2) == 6.25


def test_run_query_returns_rows_and_pagination_token(tmp_path):
    rows = [{"id": i} for i in range(5)]
    client = FakeBigQueryClient(FakeQueryJob(rows=rows, next_page_token="next-123", page_size=3))
    mcp, cache, guard, audit = _register(client, tmp_path)

    result = mcp.tools["run_query"]("SELECT * FROM t", max_rows=3)
    assert result["row_count"] == 3
    assert result["next_page_token"] == "next-123"
    assert result["cache_hit"] is False


def test_run_query_serves_from_cache_on_repeat(tmp_path):
    rows = [{"id": 1}]
    client = FakeBigQueryClient(FakeQueryJob(rows=rows))
    mcp, cache, guard, audit = _register(client, tmp_path)

    first = mcp.tools["run_query"]("SELECT * FROM t")
    assert first["cache_hit"] is False

    second = mcp.tools["run_query"]("SELECT * FROM t")
    assert second["cache_hit"] is True
    # Only one real query call should have hit the fake client (the dry-run + first execution),
    # the second call should be served entirely from cache.
    assert len(client.query_calls) == 2  # dry-run + execute, for the first call only


def test_run_query_blocks_select_star_on_restricted_table(tmp_path):
    client = FakeBigQueryClient(FakeQueryJob(rows=[{"ssn": "123-45-6789"}]))
    mcp, cache, guard, audit = _register(
        client, tmp_path,
        protection_mode="auto_protect",
        prevented_fields={"healthcare.patients": ["ssn"]},
    )

    result = mcp.tools["run_query"]("SELECT * FROM `proj.healthcare.patients`")
    assert "error" in result
    assert "EXCEPT" in result["error"]


def test_run_query_ddl_rejected_by_default(tmp_path):
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=0))
    mcp, *_ = _register(client, tmp_path)
    result = mcp.tools["run_query"]("CREATE TABLE t (a INT64)")
    assert "error" in result
    assert "DDL is disabled" in result["error"]
