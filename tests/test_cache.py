import time

from bigquery_mcp.cache import QueryResultCache


def test_cache_miss_then_hit():
    cache = QueryResultCache(ttl_seconds=60)
    assert cache.get("SELECT 1") is None
    cache.set("SELECT 1", {"rows": [{"a": 1}]})
    assert cache.get("SELECT 1") == {"rows": [{"a": 1}]}
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_cache_disabled_when_ttl_zero():
    cache = QueryResultCache(ttl_seconds=0)
    cache.set("SELECT 1", {"rows": []})
    assert cache.get("SELECT 1") is None
    assert cache.enabled() is False


def test_cache_expires_after_ttl():
    cache = QueryResultCache(ttl_seconds=0.05)
    cache.set("SELECT 1", {"rows": [1]})
    assert cache.get("SELECT 1") == {"rows": [1]}
    time.sleep(0.1)
    assert cache.get("SELECT 1") is None


def test_cache_distinguishes_params_and_page_token():
    cache = QueryResultCache(ttl_seconds=60)
    cache.set("SELECT * FROM t WHERE id=@id", {"rows": [1]}, page_token="page-a")
    assert cache.get("SELECT * FROM t WHERE id=@id", page_token="page-b") is None
    assert cache.get("SELECT * FROM t WHERE id=@id", page_token="page-a") == {"rows": [1]}


def test_cache_clear_resets_stats():
    cache = QueryResultCache(ttl_seconds=60)
    cache.set("SELECT 1", {"rows": []})
    cache.get("SELECT 1")
    cache.clear()
    stats = cache.stats()
    assert stats == {"entries": 0, "hits": 0, "misses": 0}
