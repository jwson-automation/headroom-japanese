import json

from headroom_ja import compress, proactive_expand
from headroom_ja.context_tracker import get_context_tracker, reset_context_tracker


def _data(n=100):
    data = [{"id": i, "name": f"レコード{i}", "category": "一般"} for i in range(n)]
    data[40] = {"id": 40, "name": "認証エラー記録", "category": "セキュリティ"}
    return data


def test_proactive_expansion_returns_relevant_originals():
    reset_context_tracker()
    content = json.dumps(_data(), ensure_ascii=False)
    # turn 1: compress and register with the tracker
    r = compress(content, query="記録の一覧を取得",
                 turn=1, workspace="proj", tool_name="search")
    assert r.dropped > 0 and r.cache_key is not None

    # turn 5: a new, related query -> the dropped originals are pre-expanded
    block, recs = proactive_expand("セキュリティ関連の記録は？", turn=5, workspace="proj")
    assert recs, "expected a proactive-expansion recommendation"
    assert recs[0].hash_key == r.cache_key
    assert block and "先読み展開" in block
    assert "セキュリティ" in block  # the expanded content carries the answer-bearing item


def test_workspace_isolation():
    reset_context_tracker()
    content = json.dumps(_data(), ensure_ascii=False)
    compress(content, query="記録の一覧", turn=1, workspace="projA", tool_name="search")
    # a different workspace must not see projA's compressed context (GH #462)
    block, recs = proactive_expand("セキュリティ関連の記録は？", turn=5, workspace="projB")
    assert recs == []
    assert block == ""
    # empty workspace fails closed too
    block2, recs2 = proactive_expand("セキュリティ関連の記録は？", turn=5, workspace="")
    assert recs2 == []


def test_irrelevant_query_does_not_expand():
    reset_context_tracker()
    content = json.dumps(_data(), ensure_ascii=False)
    compress(content, query="記録の一覧", turn=1, workspace="proj", tool_name="search")
    # unrelated query -> below relevance threshold -> no expansion
    _, recs = proactive_expand("天気はどうですか", turn=5, workspace="proj")
    assert recs == []
