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


def test_rare_value_kept_by_relevance():
    # A rare status value (no error keyword) is reachable via the query keyword,
    # now that 返品ステータス splits into 返品 + ステータス.
    data = [{"id": i, "user": f"u{i}", "status": "支払済"} for i in range(100)]
    data[60] = {"id": 60, "user": "鈴木", "status": "返品"}
    keep, _ = crush_array(data, query="返品ステータスの注文", cfg=CrusherConfig())
    assert 60 in keep


def test_generic_word_does_not_pollute_relevance():
    # A word in every item (記事) must NOT mark the whole array relevant; the real
    # answer (matched by a rare word) must survive and compression must still happen.
    data = [{"id": i, "title": f"記事{i}", "body": "一般的な内容"} for i in range(60)]
    data[40] = {"id": 40, "title": "Python非同期処理", "body": "asyncioの解説"}
    keep, dropped = crush_array(data, query="Pythonの記事", cfg=CrusherConfig())
    assert 40 in keep            # rare-word match survives
    assert len(keep) < 30        # 記事 did not keep everything


def test_summary_embeds_aggregates():
    # Aggregation answerability: sum and value-counts survive lossy row-drop.
    data = [{"id": i, "user": f"u{i}", "amount": 1000 + i,
             "status": "キャンセル" if i % 10 == 0 else "支払済"} for i in range(40)]
    r = compress(json.dumps(data, ensure_ascii=False),
                 config=CrusherConfig(include_summary=True))
    assert r.dropped > 0
    assert "_集計_全体" in r.text
    assert str(sum(d["amount"] for d in data)) in r.text         # true total present
    n_cancel = sum(1 for d in data if d["status"] == "キャンセル")
    assert f'"キャンセル": {n_cancel}' in r.text                  # true count present


def test_keep_top_k_keeps_runner_up():
    # 2nd-highest at a middle position, not a 2σ outlier. Default (k=1) drops it;
    # keep_top_k=2 keeps it.
    data = [{"id": i, "amount": 10000 + ((i + 100) % 200)} for i in range(200)]
    # max 10199 at id=99, 2nd 10198 at id=98
    keep1, _ = crush_array(data, None, CrusherConfig())
    assert 98 not in keep1                          # documented gap at k=1
    keep2, _ = crush_array(data, None, CrusherConfig(keep_top_k=2))
    assert 98 in keep2 and 99 in keep2              # runner-up kept at k=2


def test_small_array_untouched():
    data = [{"id": i} for i in range(3)]
    keep, dropped = crush_array(data, None, CrusherConfig())
    assert dropped == []  # below min_items, leave it alone
