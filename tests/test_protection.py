from bigquery_mcp.protection import SensitiveFieldGuard


def _guard(mode="auto_protect"):
    return SensitiveFieldGuard(
        protection_mode=mode,
        prevented_fields={"healthcare.patients": ["ssn", "email"]},
        sensitive_field_patterns=["%ssn%", "%email%"],
    )


def test_off_mode_never_blocks():
    guard = _guard(mode="off")
    assert guard.check_columns("healthcare.patients", ["*"]) == []


def test_blocks_select_star_when_restricted_fields_exist():
    guard = _guard()
    blocked = guard.check_columns("healthcare.patients", ["*"])
    assert set(blocked) == {"ssn", "email"}


def test_allows_explicit_safe_columns():
    guard = _guard()
    blocked = guard.check_columns("healthcare.patients", ["patient_id", "diagnosis"])
    assert blocked == []


def test_blocks_explicit_restricted_column():
    guard = _guard()
    blocked = guard.check_columns("healthcare.patients", ["patient_id", "ssn"])
    assert blocked == ["ssn"]


def test_guidance_message_mentions_except_clause():
    guard = _guard()
    msg = guard.guidance_for_blocked("healthcare.patients", ["ssn", "email"])
    assert "EXCEPT" in msg
    assert "healthcare.patients" in msg


def test_scan_flags_matching_columns(fake_client):
    class FakeField:
        def __init__(self, name):
            self.name = name

    class FakeTable:
        def __init__(self, schema):
            self.schema = schema

    class FakeTableItem:
        def __init__(self, table_id):
            self.table_id = table_id
            self.reference = table_id

    class FakeDatasetRef:
        pass

    class FakeDataset:
        def __init__(self, dataset_id):
            self.dataset_id = dataset_id
            self.reference = FakeDatasetRef()

    fake_client.list_datasets = lambda project=None: [FakeDataset("healthcare")]
    fake_client.list_tables = lambda dataset_ref: [FakeTableItem("patients")]
    fake_client.get_table = lambda ref: FakeTable(
        [FakeField("patient_id"), FakeField("ssn"), FakeField("email")]
    )

    guard = _guard()
    result = guard.scan(fake_client, "test-project")
    assert result.scanned_tables == 1
    assert set(result.flagged_columns["healthcare.patients"]) == {"ssn", "email"}
