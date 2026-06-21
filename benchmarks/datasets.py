"""Synthetic Japanese tool-output generators for benchmarking.

Each generator returns (data, question, gold, answer_ids) where:
  data        : a JSON-serializable list (or dict envelope) = the tool output
  question    : the Japanese question to ask the LLM
  gold        : the expected answer (short string)
  answer_ids  : ids of the item(s) that hold the answer -> lets us check,
                WITHOUT any LLM, whether the compressor kept the answer.
                Empty list = the answer needs all items (aggregation); answer_kept
                is not meaningful there (reported as n/a).

The set is deliberately diverse and includes cases designed to STRESS or BREAK the
compressor (aggregation over all items, answer buried in the middle, rare values
with no keyword signal) so the report shows limits honestly, not just wins.
"""

from __future__ import annotations


# ── single-answer cases (answer_kept is checkable) ─────────────────────────

def gen_rejected_order(n=200, pos=137):
    """Rejected order among paid orders. error-keyword + relevance, mid position."""
    data = [{"id": i, "user": f"user{i}", "amount": 10000 + (i % 50),
             "status": "支払済"} for i in range(n)]
    data[pos] = {"id": pos, "user": "田中", "amount": 8000,
                 "status": "拒否", "msg": "カードが拒否されました"}
    return data, "拒否された注文のユーザー名は誰ですか？", "田中", [pos]


def gen_high_amount(n=200, pos=88):
    """One abnormally large amount. numeric-outlier keep."""
    data = [{"id": i, "user": f"user{i}", "amount": 12000 + (i % 30),
             "status": "支払済"} for i in range(n)]
    data[pos]["amount"] = 980000
    return data, "異常に高額な注文の注文IDは何番ですか？", "88", [pos]


def gen_low_amount(n=200, pos=151):
    """One abnormally SMALL amount. numeric-outlier keep (low side)."""
    data = [{"id": i, "user": f"user{i}", "amount": 12000 + (i % 30),
             "status": "支払済"} for i in range(n)]
    data[pos]["amount"] = 10
    return data, "異常に金額が低い注文の注文IDは何番ですか？", "151", [pos]


def gen_error_log(n=300, pos=210):
    """One ERROR line among INFO logs. error-keyword keep on log rows."""
    data = [{"id": i, "level": "INFO", "msg": f"リクエスト処理 {i}"} for i in range(n)]
    data[pos] = {"id": pos, "level": "ERROR", "msg": "決済サービスへの接続がタイムアウトしました"}
    return data, "最初にエラーになった処理のメッセージは何ですか？", "タイムアウト", [pos]


def gen_last_error(n=200):
    """Three errors; ask for the LAST. Needs all errors kept + ordering reasoning."""
    data = [{"id": i, "level": "INFO", "msg": f"処理 {i} 完了"} for i in range(n)]
    for p, m in [(31, "DB接続に失敗"), (97, "認証に失敗"), (176, "在庫サービスが応答なし")]:
        data[p] = {"id": p, "level": "ERROR", "msg": m}
    return data, "最後（一番後）にエラーになった注文のIDは何番ですか？", "176", [176]


def gen_rare_field(n=200, pos=150):
    """One item carries a rare field. structural-outlier keep."""
    data = [{"id": i, "user": f"user{i}", "amount": 10000} for i in range(n)]
    data[pos] = {"id": pos, "user": "佐藤", "amount": 10000, "refund_reason": "商品不良"}
    return data, "返金理由が付いている注文のユーザー名は誰ですか？", "佐藤", [pos]


def gen_rare_status(n=200, pos=142):
    """Rare status VALUE (no error keyword); answer reachable only via relevance."""
    data = [{"id": i, "user": f"user{i}", "amount": 9000, "status": "支払済"}
            for i in range(n)]
    data[pos] = {"id": pos, "user": "鈴木", "amount": 9000, "status": "返品"}
    return data, "返品ステータスの注文のユーザー名は誰ですか？", "鈴木", [pos]


def gen_search_results(n=150, pos=74):
    """Search hits; one relevant page buried in the middle. relevance keep."""
    topics = ["料理レシピ", "旅行ガイド", "ガーデニング", "映画レビュー", "筋トレ"]
    data = [{"id": i, "title": f"{topics[i % len(topics)]}の記事 {i}",
             "snippet": f"{topics[i % len(topics)]}に関する一般的な解説です。"}
            for i in range(n)]
    data[pos] = {"id": pos, "title": "Python async/awaitの使い方",
                 "snippet": "非同期処理をasyncioで書く方法を具体例とともに説明します。"}
    return data, "Pythonの非同期処理について説明している記事のタイトルは何ですか？", \
        "Python async/awaitの使い方", [pos]


def gen_long_reviews(n=100, pos=63):
    """Product reviews (long JP prose); one reports a specific defect. tokenizer relevance."""
    filler = [
        "梱包が丁寧で満足しています。また購入したいです。",
        "配送が少し遅れましたが品質は良かったです。",
        "値段の割に質が高くおすすめできます。",
        "説明通りの商品で問題ありませんでした。",
    ]
    data = [{"id": i, "user": f"reviewer{i}", "review": filler[i % len(filler)]}
            for i in range(n)]
    data[pos] = {"id": pos, "user": "山本",
                 "review": "届いた商品の画面が割れていた。すぐに交換してほしい。"}
    return data, "画面が割れていたと報告したレビュアーは誰ですか？", "山本", [pos]


def gen_nested_envelope(n=200, pos=120):
    """API envelope {meta, results:[...]} with an error inside. object-of-arrays."""
    results = [{"id": i, "user": f"user{i}", "amount": 5000, "status": "支払済"}
               for i in range(n)]
    results[pos] = {"id": pos, "user": "中村", "amount": 5000,
                    "status": "エラー", "msg": "在庫不足で出荷できません"}
    data = {"total": n, "page": 1, "results": results}
    return data, "出荷できなかった注文のユーザー名は誰ですか？", "中村", [pos]


# ── aggregation cases (answer_kept = n/a; these STRESS lossy compression) ───

def gen_total_sum(n=40):
    """Sum over ALL items. Lossy compression drops items -> expected to be hard."""
    data = [{"id": i, "user": f"user{i}", "amount": 1000 + i} for i in range(n)]
    total = sum(d["amount"] for d in data)  # 40*1000 + (0+..+39) = 40780
    return data, "全注文の合計金額はちょうどいくらですか？数字で答えてください。", str(total), []


def gen_count_status(n=200):
    """Count items with a status. relevance keeps all matching -> may survive."""
    data = [{"id": i, "user": f"user{i}", "status": "支払済"} for i in range(n)]
    cancel_ids = list(range(10, 200, 10))  # 19 cancellations
    for i in cancel_ids:
        data[i]["status"] = "キャンセル"
    return data, "キャンセルされた注文は全部で何件ですか？", str(len(cancel_ids)), []


def gen_filtered_sum(n=60):
    """FILTERED aggregate: sum of amounts for one status only. A generic summary
    (total amount + status counts) cannot answer this — only retrieving the
    originals and summing the matching rows can."""
    data = [{"id": i, "user": f"user{i}", "amount": 1000 + i,
             "status": "キャンセル" if i % 5 == 0 else "支払済"} for i in range(n)]
    total = sum(d["amount"] for d in data if d["status"] == "キャンセル")
    return data, "キャンセルされた注文の合計金額はいくらですか？数字で答えてください。", str(total), []


# ── round C: harder / structurally diverse ─────────────────────────────────

def gen_deep_nested(n=200, pos=88):
    """Array buried two levels deep: {response:{data:{orders:[...]}}}."""
    orders = [{"id": i, "user": f"user{i}", "amount": 5000, "status": "支払済"}
              for i in range(n)]
    orders[pos] = {"id": pos, "user": "高橋", "amount": 5000,
                   "status": "エラー", "msg": "住所が無効です"}
    data = {"response": {"data": {"orders": orders}, "count": n}}
    return data, "住所エラーになった注文のユーザー名は誰ですか？", "高橋", [pos]


def gen_uuid_ids(n=200, pos=130):
    """String (non-integer) ids. Exercises dedup-ignore + id-based answer check."""
    data = [{"id": f"ord-{i:04d}", "user": f"user{i}", "amount": 7000,
             "status": "支払済"} for i in range(n)]
    data[pos] = {"id": f"ord-{pos:04d}", "user": "伊藤", "amount": 7000,
                 "status": "拒否", "msg": "カードが拒否されました"}
    return data, "拒否された注文のユーザー名は誰ですか？", "伊藤", [f"ord-{pos:04d}"]


def gen_vip_flag(n=200, pos=140):
    """Rare boolean/value discriminator. The VIP marker is the *value*, not text -
    targets rare-value detection (relevance can't help: is_vip appears on every item)."""
    data = [{"id": i, "user": f"user{i}", "plan": "free", "is_vip": False}
            for i in range(n)]
    data[pos] = {"id": pos, "user": "渡辺", "plan": "premium", "is_vip": True}
    return data, "プレミアム会員のユーザー名は誰ですか？", "渡辺", [pos]


def gen_mixed_lang(n=150, pos=70):
    """Mixed JP/EN ticket messages; answer reachable via English error keyword."""
    fill = ["Thanks, everything works.", "配送ありがとうございました。",
            "No issues so far.", "問題なく使えています。"]
    data = [{"id": i, "customer": f"cust{i}", "message": fill[i % len(fill)]}
            for i in range(n)]
    data[pos] = {"id": pos, "customer": "松本",
                 "message": "The payment failed and カードが使えませんでした。"}
    return data, "支払いに失敗したと報告した顧客は誰ですか？", "松本", [pos]


def gen_ambiguous(n=200):
    """Two errors with different causes; the question disambiguates by cause."""
    data = [{"id": i, "user": f"user{i}", "status": "支払済"} for i in range(n)]
    data[64] = {"id": 64, "user": "小林", "status": "エラー", "msg": "在庫切れ"}
    data[150] = {"id": 150, "user": "加藤", "status": "エラー", "msg": "住所不備"}
    return data, "在庫切れでエラーになった注文のユーザー名は誰ですか？", "小林", [64]


def gen_cheapest(n=200):
    """The minimum-amount item is at the middle and is only mildly low (not a 2σ
    outlier). Targets always-keep per-field min/max."""
    data = [{"id": i, "user": f"user{i}", "amount": 10000 + ((i + 100) % 200)}
            for i in range(n)]  # min amount (10000) lands at id=100 (middle)
    return data, "最も金額が低い注文の注文IDは何番ですか？", "100", [100]


ALL = [
    gen_rejected_order, gen_high_amount, gen_low_amount, gen_error_log,
    gen_last_error, gen_rare_field, gen_rare_status, gen_search_results,
    gen_long_reviews, gen_nested_envelope, gen_total_sum, gen_count_status,
    gen_deep_nested, gen_uuid_ids, gen_vip_flag, gen_mixed_lang,
    gen_ambiguous, gen_cheapest, gen_filtered_sum,
]


def build(generators=None):
    """Return a list of (name, data, question, gold, answer_ids)."""
    gens = generators or ALL
    out = []
    for g in gens:
        data, q, gold, ids = g()
        out.append((g.__name__, data, q, gold, ids))
    return out
