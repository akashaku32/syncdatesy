from services.sap.source import _build_metadata_columns, _resolve_edmx_entity_type


SAMPLE_EDMX = {
    "edmx:Edmx": {
        "edmx:DataServices": {
            "Schema": {
                "EntityType": [
                    {
                        "@Name": "BigEntityType",
                        "Key": {"PropertyRef": {"@Name": "ID"}},
                        "Property": [
                            {"@Name": "ID", "@Type": "Edm.String"},
                            {"@Name": "FIELD_A", "@Type": "Edm.String"},
                        ],
                    },
                    {
                        "@Name": "SmallEntityType",
                        "Key": {"PropertyRef": {"@Name": "KEY1"}},
                        "Property": [
                            {"@Name": "KEY1", "@Type": "Edm.String"},
                            {"@Name": "PMATA", "@Type": "Edm.String"},
                        ],
                    },
                ],
                "EntityContainer": {
                    "EntitySet": [
                        {
                            "@Name": "EntityOf0FI_AR_30",
                            "@EntityType": "ServiceNamespace.SmallEntityType",
                        },
                        {
                            "@Name": "OtherEntity",
                            "@EntityType": "ServiceNamespace.BigEntityType",
                        },
                    ]
                },
            }
        }
    }
}


def test_resolve_entity_type_by_entity_set_name():
    entity = _resolve_edmx_entity_type(SAMPLE_EDMX, "EntityOf0FI_AR_30")
    assert entity is not None
    assert entity["@Name"] == "SmallEntityType"


def test_build_metadata_columns_for_resolved_entity():
    entity = _resolve_edmx_entity_type(SAMPLE_EDMX, "EntityOf0FI_AR_30")
    columns = _build_metadata_columns(entity)
    names = [c["name"] for c in columns]
    assert "PMATA" in names
    assert "FIELD_A" not in names
    assert "__operation_type" in names
