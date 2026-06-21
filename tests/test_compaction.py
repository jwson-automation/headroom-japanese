import json

from headroom_ja import compress
from headroom_ja.compaction import compact
from headroom_ja.crusher import CrusherConfig


def test_compact_is_lossless():
    items = [{"id": i, "user": f"u{i}", "amount": 1000 + i} for i in range(20)]
    packed = compact(items)
    assert packed is not None
    cols = packed["_columns"]
    # reconstruct and compare to originals (lossless)
    recon = [dict(zip(cols, row)) for row in packed["_rows"]]
    assert recon == items


def test_compact_rejects_heterogeneous():
    assert compact([1, 2, 3, 4, 5]) is None
    assert compact([{"a": 1}, "x", {"b": 2}, 3, 4]) is None


def test_lossless_first_keeps_all_rows_and_saves():
    data = [{"id": i, "user": f"user{i}", "amount": 12000 + i, "status": "支払済"}
            for i in range(200)]
    content = json.dumps(data, ensure_ascii=False)
    r = compress(content, config=CrusherConfig(lossless_first=True))
    assert r.dropped == 0                       # lossless: nothing dropped
    assert r.compressed_tokens < r.original_tokens
    assert "_columns" in r.text and "_rows" in r.text
    # every id is still present (no data loss)
    assert '199' in r.text and '0' in r.text


def test_lossless_first_off_by_default():
    data = [{"id": i, "user": f"user{i}", "amount": 12000 + i, "status": "支払済"}
            for i in range(200)]
    r = compress(json.dumps(data, ensure_ascii=False))
    assert r.dropped > 0                          # default path is lossy row-drop
    assert "_columns" not in r.text
