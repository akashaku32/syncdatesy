from services.transform.mapping_resolver import apply_overrides


def test_ui_overrides_repoint_and_add():
    base = [
        {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS"},
        {"src_fieldnames": ["DMBTR"], "tgt_fieldname": "DMBTR"},
    ]
    rows = apply_overrides(
        base,
        {
            "addedSources": ["EXTRA"],
            "removedTargets": [],
            "mappings": [
                {
                    "kind": "repoint",
                    "jsonTarget": "DMBTR",
                    "target": "DMBTR",
                    "source": "BUKRS",
                }
            ],
        },
    )
    by_target = {row["tgt_fieldname"]: row for row in rows}
    assert by_target["DMBTR"]["src_fieldnames"] == ["BUKRS"]
    assert by_target["BUKRS"]["src_fieldnames"] == ["BUKRS"]
    assert by_target["EXTRA"]["added_by_user"] is True


def test_script_and_constant_rows_may_omit_source_fields():
    from services.transform.mapping_validator import validate_mapping_structure

    errors = validate_mapping_structure(
        {
            "source": {"objectname": "0FI_GL_4"},
            "target": {},
            "field_mapping": [
                {"tgt_fieldname": "UPD_DATE"},
                {
                    "tgt_fieldname": "GN_R3_SSY",
                    "src_fieldnames": [],
                    "constant_value": "X",
                },
                {"tgt_fieldname": "DIRECT_COL", "mapping_type": "DIRECT"},
            ],
        }
    )
    assert [error["path"] for error in errors] == [
        "$.field_mapping[2].src_fieldnames"
    ]


def test_removed_json_mappings_are_excluded_from_extract_columns():
    from services.transform.mapping_resolver import resolve_effective_mapping

    resolved = resolve_effective_mapping(
        {
            "field_mapping": [
                {"src_fieldnames": ["VALUT"], "tgt_fieldname": "VALUT"},
                {"src_fieldnames": ["BUKRS"], "tgt_fieldname": "BUKRS"},
            ],
            "target": {"fields": [{"fieldname": "UPD_DATE"}]},
        },
        {"removedJsonMappings": ["json:VALUT", "script:UPD_DATE"]},
    )
    targets = [row["tgt_fieldname"] for row in resolved["rows"]]
    assert "VALUT" not in targets
    assert "BUKRS" in targets
    assert resolved["extract_columns"] == ["BUKRS"]
    assert "UPD_DATE" not in resolved["script_columns"]
