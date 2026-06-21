"""Synthetic Japanese tool-output generators for benchmarking.

Each generator returns (data, question, gold, answer_ids) where:
  data        : a JSON-serializable list (the tool output)
  question    : the Japanese question to ask the LLM
  gold        : the expected answer (short string)
  answer_ids  : ids of the item(s) that hold the answer -> lets us check,
                without any LLM, whether the compressor kept the answer.

The answer always lives in a *known* item, so answer_kept is deterministic.
"""

from __future__ import annotations


def gen_rejected_order(n: int = 200, pos: int = 137):
    """A rejected order hidden among many paid orders. Targets error-keep + relevance."""
    data = [{"id": i, "user": f"user{i}", "amount": 10000 + (i % 50),
             "status": "支払済"} for i in range(n)]
    data[pos] = {"id": pos, "user": "田中", "amount": 8000,
                 "status": "拒否", "msg": "カードが拒否されました"}
    return data, "拒否された注文のユーザー名は誰ですか？", "田中", [pos]


def gen_high_amount(n: int = 200, pos: int = 88):
    """One abnormally large amount. Targets numeric-outlier keep."""
    data = [{"id": i, "user": f"user{i}", "amount": 12000 + (i % 30),
             "status": "支払済"} for i in range(n)]
    data[pos]["amount"] = 980000
    return data, "異常に高額な注文の注文IDは何番ですか？", str(pos), [pos]


def gen_error_log(n: int = 300, pos: int = 210):
    """One ERROR line among INFO logs. Targets error-keep on log-shaped rows."""
    data = [{"id": i, "level": "INFO", "msg": f"リクエスト処理 {i}"} for i in range(n)]
    data[pos] = {"id": pos, "level": "ERROR", "msg": "決済サービスへの接続がタイムアウトしました"}
    return data, "最初にエラーになった処理のメッセージは何ですか？", "タイムアウト", [pos]


def gen_rare_field(n: int = 200, pos: int = 150):
    """One item carries a rare field. Targets structural-outlier keep."""
    data = [{"id": i, "user": f"user{i}", "amount": 10000} for i in range(n)]
    data[pos] = {"id": pos, "user": "佐藤", "amount": 10000, "refund_reason": "商品不良"}
    return data, "返金理由が付いている注文のユーザー名は誰ですか？", "佐藤", [pos]


ALL = [gen_rejected_order, gen_high_amount, gen_error_log, gen_rare_field]


def build(generators=None):
    """Return a list of (name, data, question, gold, answer_ids)."""
    gens = generators or ALL
    out = []
    for g in gens:
        data, q, gold, ids = g()
        out.append((g.__name__, data, q, gold, ids))
    return out
