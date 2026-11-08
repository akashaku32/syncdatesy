"""Tests for generic source-side script validation and cursor execution."""

import pytest

from services.transform.script_runner import ScriptRuntimeError
from services.transform.source_script_transform import (
    TransformingCursor,
    validate_source_script,
)


class FakeCursor:
    description = [("A",), ("B",)]

    def __init__(self):
        self.rows = [(1, 2), (3, 4), (5, 6)]

    def fetchmany(self, size):
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def close(self):
        return None


def test_validate_source_script_uses_existing_script_checks():
    result = validate_source_script(
        "import os\n\ndef transform(df):\n    return df\n",
        "transform",
    )

    assert result["valid"] is False
    assert any("Import 'os'" in error["message"] for error in result["errors"])


def test_transforming_cursor_reuses_writer_cursor_contract():
    cursor = TransformingCursor(
        FakeCursor(),
        fetch_size=2,
        script_text=(
            "def transform(df):\n"
            "    df = df.copy()\n"
            "    df['TOTAL'] = df['A'] + df['B']\n"
            "    return df\n"
        ),
        entry_point="transform",
    )

    assert [column[0] for column in cursor.description] == ["A", "B", "TOTAL"]
    assert cursor.fetchmany(2) == [(1, 2, 3), (3, 4, 7)]
    assert cursor.fetchmany(2) == [(5, 6, 11)]


def test_transforming_cursor_rejects_changing_output_schema():
    class ChangingCursor(FakeCursor):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def fetchmany(self, size):
            self.calls += 1
            if self.calls == 1:
                return [(1, 2)]
            return [(3, 4)] if self.calls == 2 else []

    cursor = TransformingCursor(
        ChangingCursor(),
        fetch_size=1,
        script_text=(
            "def transform(df):\n"
            "    if df['A'].iloc[0] == 3:\n"
            "        df['C'] = 1\n"
            "    return df\n"
        ),
        entry_point="transform",
    )
    cursor.fetchmany(1)
    with pytest.raises(ScriptRuntimeError, match="consistent DataFrame schema"):
        cursor.fetchmany(1)


def test_transforming_cursor_continues_after_an_empty_transformed_batch():
    class FilteringCursor(FakeCursor):
        def __init__(self):
            super().__init__()
            self.rows = [(0, 0), (1, 1), (2, 2)]

    cursor = TransformingCursor(
        FilteringCursor(),
        fetch_size=1,
        script_text=(
            "def transform(df):\n"
            "    return df[df['A'] > 0]\n"
        ),
        entry_point="transform",
    )

    assert cursor.fetchmany(1) == [(1, 1)]
    assert cursor.fetchmany(1) == [(2, 2)]
    assert cursor.fetchmany(1) == []


def test_transforming_cursor_primes_output_header_after_empty_first_batch():
    class HeaderCursor(FakeCursor):
        def __init__(self):
            super().__init__()
            self.rows = [(0, 0), (1, 1)]

    cursor = TransformingCursor(
        HeaderCursor(),
        fetch_size=1,
        script_text=(
            "def transform(df):\n"
            "    df = df[df['A'] > 0].copy()\n"
            "    df['TOTAL'] = df['A'] + df['B']\n"
            "    return df\n"
        ),
        entry_point="transform",
    )

    assert [column[0] for column in cursor.description] == ["A", "B", "TOTAL"]
    assert cursor.fetchmany(1) == [(1, 1, 2)]


def test_transforming_cursor_infers_header_for_empty_source():
    class EmptyCursor(FakeCursor):
        def __init__(self):
            self.rows = []

    cursor = TransformingCursor(
        EmptyCursor(),
        fetch_size=2,
        script_text=(
            "def transform(df):\n"
            "    return df.rename(columns={'A': 'ID'})[['ID']]\n"
        ),
        entry_point="transform",
    )

    assert [column[0] for column in cursor.description] == ["ID"]
    assert cursor.fetchmany(2) == []


def test_transforming_cursor_preserves_nullable_integer_values():
    class NullableCursor(FakeCursor):
        description = [("A",)]

        def __init__(self):
            self.rows = [(5,), (None,)]

    cursor = TransformingCursor(
        NullableCursor(),
        fetch_size=2,
        script_text="def transform(df):\n    return df\n",
        entry_point="transform",
    )
    rows = cursor.fetchmany(2)

    assert rows == [(5,), (None,)]
    assert type(rows[0][0]) is int


def test_empty_source_script_error_is_not_silently_converted_to_source_schema():
    class EmptyCursor(FakeCursor):
        def __init__(self):
            self.rows = []

    with pytest.raises(ScriptRuntimeError):
        TransformingCursor(
            EmptyCursor(),
            fetch_size=2,
            script_text=(
                "def transform(df):\n"
                "    df['VALUE'] = df.iloc[0]['A']\n"
                "    return df\n"
            ),
            entry_point="transform",
        )
