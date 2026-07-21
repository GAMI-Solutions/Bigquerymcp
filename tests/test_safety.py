from bigquery_mcp.safety import QueryRejected, classify_statement, validate_query

from .conftest import FakeBigQueryClient, FakeQueryJob


def test_classify_statement():
    assert classify_statement("SELECT * FROM t") == "SELECT"
    assert classify_statement("  with x as (select 1) select * from x") == "SELECT"
    assert classify_statement("CREATE TABLE t (a INT64)") == "DDL"
    assert classify_statement("DROP TABLE t") == "DDL"
    assert classify_statement("INSERT INTO t VALUES (1)") == "DML"
    assert classify_statement("MERGE INTO t USING s ON true WHEN MATCHED THEN DELETE") == "DML"
    assert classify_statement("EXPORT DATA OPTIONS(...) AS SELECT 1") == "OTHER"


def test_select_allowed_by_default():
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=500))
    plan = validate_query(
        client, "SELECT * FROM t",
        allow_ddl=False, allow_dml=False, maximum_bytes_billed=1_000_000,
    )
    assert plan.statement_type == "SELECT"
    assert plan.requires_confirmation is False


def test_ddl_rejected_when_disabled():
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=0))
    try:
        validate_query(
            client, "CREATE TABLE t (a INT64)",
            allow_ddl=False, allow_dml=False, maximum_bytes_billed=1_000_000,
        )
        assert False, "expected QueryRejected"
    except QueryRejected as exc:
        assert "DDL is disabled" in str(exc)


def test_dml_rejected_when_disabled():
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=0))
    try:
        validate_query(
            client, "DELETE FROM t WHERE true",
            allow_ddl=False, allow_dml=False, maximum_bytes_billed=1_000_000,
        )
        assert False, "expected QueryRejected"
    except QueryRejected as exc:
        assert "DML is disabled" in str(exc)


def test_ddl_requires_confirmation_even_when_enabled():
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=0))
    try:
        validate_query(
            client, "CREATE TABLE t (a INT64)",
            allow_ddl=True, allow_dml=False, maximum_bytes_billed=1_000_000,
            confirm=False,
        )
        assert False, "expected QueryRejected requiring confirmation"
    except QueryRejected as exc:
        assert "confirm=true" in str(exc)

    # With confirm=True it should succeed.
    plan = validate_query(
        client, "CREATE TABLE t (a INT64)",
        allow_ddl=True, allow_dml=False, maximum_bytes_billed=1_000_000,
        confirm=True,
    )
    assert plan.statement_type == "DDL"


def test_maximum_bytes_billed_enforced():
    client = FakeBigQueryClient(FakeQueryJob(total_bytes_processed=10_000_000_000))
    try:
        validate_query(
            client, "SELECT * FROM huge_table",
            allow_ddl=False, allow_dml=False, maximum_bytes_billed=1_000_000,
        )
        assert False, "expected QueryRejected for exceeding byte limit"
    except QueryRejected as exc:
        assert "exceeding the configured limit" in str(exc)


def test_export_and_other_statements_always_rejected():
    client = FakeBigQueryClient(FakeQueryJob())
    try:
        validate_query(
            client, "EXPORT DATA OPTIONS(uri='gs://bucket/*.csv') AS SELECT * FROM t",
            allow_ddl=True, allow_dml=True, maximum_bytes_billed=1_000_000,
        )
        assert False, "expected QueryRejected"
    except QueryRejected as exc:
        assert "Only SELECT" in str(exc)
