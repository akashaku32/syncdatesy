import pytest

from services.source_file_helpers.excel_condition_formula import (
    ExcelConditionFormulaError,
    compile_multiple_conditions_formula,
)


def test_compiles_nested_conditions_against_final_columns_and_row():
    conditions = [
        {"column": "IS_ACTIVE", "operator": "equal", "value": "TRUE"},
        {
            "column": "UNIT_PRICE",
            "operator": "greaterThan",
            "value": "300",
            "logic": "and",
            "open_paren": "(",
        },
        {
            "column": "QUANTITY",
            "operator": "greaterThanOrEqual",
            "value": "5",
            "logic": "or",
            "open_paren": "(",
        },
        {
            "column": "TAX_RATE",
            "operator": "equal",
            "value": "0.0525",
            "logic": "and",
            "close_paren": "))",
        },
    ]

    # The source order is deliberately different from the condition order.
    formula = compile_multiple_conditions_formula(
        conditions,
        ["OWNER", "IS_ACTIVE", "UNIT_PRICE", "QUANTITY", "TAX_RATE"],
        4,
    )

    assert formula == (
        "AND($B4=TRUE,OR($C4>300,AND($D4>=5,$E4=0.0525)))"
    )


def test_rejects_unknown_column_and_unbalanced_parentheses():
    with pytest.raises(ExcelConditionFormulaError, match="not found"):
        compile_multiple_conditions_formula(
            [{"column": "MISSING", "operator": "equal", "value": "x"}],
            ["STATUS"],
            2,
        )

    with pytest.raises(ExcelConditionFormulaError, match="unbalanced"):
        compile_multiple_conditions_formula(
            [
                {
                    "column": "STATUS",
                    "operator": "equal",
                    "value": "x",
                    "open_paren": "(",
                }
            ],
            ["STATUS"],
            2,
        )

