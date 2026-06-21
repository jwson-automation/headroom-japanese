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
    assert 400 in keep  # the 980000 outlier is preserved


def test_compresses_and_reversible():
    data = _orders()
    r = compress(json.dumps(data, ensure_ascii=False), query="拒否された注文")
    assert r.dropped > 0
    assert r.compressed_tokens < r.original_tokens
    assert r.cache_key is not None
    # retrieve: filtering by 拒否 must surface the error order
    hits = retrieve(r.cache_key, query="拒否")
    assert any("拒否" in json.dumps(h, ensure_ascii=False) for h in hits)


def test_small_array_untouched():
    data = [{"id": i} for i in range(3)]
    keep, dropped = crush_array(data, None, CrusherConfig())
    assert dropped == []  # below min_items, leave it alone
