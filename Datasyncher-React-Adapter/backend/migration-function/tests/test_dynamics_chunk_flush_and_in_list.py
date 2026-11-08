import pandas as pd

from services.dynamics.source_common import take_flush_slice


def test_take_flush_slice_caps_at_chunk_count():
    buf = pd.DataFrame({"ID": list(range(245666))})
    flush, remainder = take_flush_slice(buf, 150000)
    assert len(flush.index) == 150000
    assert len(remainder.index) == 95666
    assert flush["ID"].tolist() == list(range(150000))
    assert remainder["ID"].tolist() == list(range(150000, 245666))


def test_take_flush_slice_exact_chunk_has_empty_remainder():
    buf = pd.DataFrame({"ID": list(range(150000))})
    flush, remainder = take_flush_slice(buf, 150000)
    assert len(flush.index) == 150000
    assert remainder.empty


def test_take_flush_slice_none_and_undersize():
    empty, remainder = take_flush_slice(None, 150000)
    assert empty.empty and remainder.empty
    buf = pd.DataFrame({"ID": [1, 2, 3]})
    flush, remainder = take_flush_slice(buf, 150000)
    assert len(flush.index) == 3
    assert remainder.empty
