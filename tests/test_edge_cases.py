"""Edge cases — guard against the failure classes headroom hit (malformed input,
scalar arrays, empty content, non-JSON that looks structural)."""

import json

from headroom_ja import compress
from headroom_ja.crusher import crush_array, CrusherConfig


def test_empty_and_whitespace():
    for s in ["", "   ", "\n\n"]:
        r = compress(s)
        assert r.text == s
        assert r.dropped == 0


def test_empty_json_array():
    r = compress("[]")
    assert r.dropped == 0  # nothing to do, no crash


def test_array_of_scalars():
    data = list(range(300))  # non-dict items
    r = compress(json.dumps(data))
    assert r.compressed_tokens <= r.original_tokens  # dedup/anchors, no crash


def test_malformed_json_falls_back_to_text():
    s = "{ this is not valid json, just braces " * 20
    r = compress(s)
    assert r.content_type in ("text", "json")
    assert r.text  # no exception


def test_mixed_scalar_and_dict_array():
    data = [{"id": i, "v": i} for i in range(50)]
    data[10] = "stray string"
    data[20] = 12345
    keep, dropped = crush_array(data, query=None, cfg=CrusherConfig())
    assert isinstance(keep, list)  # tolerates non-dict items without crashing


def test_unicode_roundtrip_preserved():
    data = [{"id": i, "name": f"商品{i}", "emoji": "🍎"} for i in range(60)]
    r = compress(json.dumps(data, ensure_ascii=False), query="商品10")
    # kept items are still valid JSON and keep their unicode
    head = r.text.split("\n")[0]
    json.loads(head)
    assert "商品" in r.text and "🍎" in r.text
