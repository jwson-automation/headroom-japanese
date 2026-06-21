import json

from headroom_ja import compress, retrieve
from headroom_ja.crusher import CrusherConfig, crush_array


def _orders(n=500):
    data = [{"id": i, "user": "kim", "amount": 12000, "status": "支払済"}
            for i in range(n)]
    data[400]["amount"] = 980000                       # numeric outlier
    data[499] = {"id": 499, "user": "choi", "amount": 8000,
                 "status": "エラー", "msg": "カードが拒否されました"}  # error
    return data


def test_error_item_always_kept():
    data = _orders()
    keep, _ = crush_array(data, query=None, cfg=CrusherConfig())
    assert 499 in keep  # error item is always preserved


def test_outlier_kept():
    data = _orders()
    keep, _ = crush_array(data, query=None, cfg=CrusherConfig())
    assert 400 in keep  # the 980000 outlier is preserved (MAD-robust)


def test_outlier_at_threshold_kept():
    # B3 regression: majority-identical values make MAD==0; the mean+std fallback
    # must still flag the lone outlier. Distinct names keep dedup from collapsing.
    data = [{"id": i, "name": f"u{i}", "v": 10} for i in range(10)]
    data[5]["v"] = 1000
    keep, _ = crush_array(data, query=None, cfg=CrusherConfig())
    assert 5 in keep


def test_dedup_ignores_identity_keys():
    # B4 regression: records differing only by id must collapse as duplicates.
    data = [{"id": i, "user": "kim", "amount": 12000} for i in range(50)]
    keep, dropped = crush_array(data, query=None, cfg=CrusherConfig())
    assert len(dropped) > 40  # nearly everything is a duplicate


def test_compresses_and_reversible():
    data = _orders()
    r = compress(json.dumps(data, ensure_ascii=False), query="拒否された注文")
    assert r.dropped > 0
    assert r.compressed_tokens < r.original_tokens
    assert r.cache_key is not None
    hits = retrieve(r.cache_key, query="拒否")
    assert any("拒否" in json.dumps(h, ensure_ascii=False) for h in hits)


def test_retrieve_no_content_query_falls_back():
    # B2 regression: a query with no content words (bare particle) must not
    # silently return nothing — it falls back to the originals.
    data = _orders()
    r = compress(json.dumps(data, ensure_ascii=False))
    assert r.cache_key is not None
    assert retrieve(r.cache_key, query="を") != []  # を tokenizes to no keyword


def test_object_of_arrays_compressed():
    # B5: the common {"results": [...]} envelope must be compressed, not passed through.
    payload = {"total": 500, "results": _orders()}
    r = compress(json.dumps(payload, ensure_ascii=False), query="拒否")
    assert r.dropped > 0
    assert r.compressed_tokens < r.original_tokens


def test_small_input_passthrough():
    # min-token gate: tiny payloads are returned untouched.
    data = [{"id": i} for i in range(6)]
    r = compress(json.dumps(data, ensure_ascii=False))
    assert r.dropped == 0
    assert r.ratio == 0.0


def test_small_array_untouched():
    data = [{"id": i} for i in range(3)]
    keep, dropped = crush_array(data, None, CrusherConfig())
    assert dropped == []  # below min_items, leave it alone
