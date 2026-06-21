"""Claude wrappers for the benchmark: an answerer and a judge.

The API key is read from the ANTHROPIC_API_KEY environment variable by the SDK.
NEVER hardcode a key here. Model ids are overridable via env vars so this file
never pins a stale id.

    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...   # use a key you can rotate
"""

from __future__ import annotations

import json
import os

# Answerer: strong + fluent in Japanese. Judge: cheap, equality-style grading.
ANSWERER = os.environ.get("HEADROOM_JA_ANSWERER", "claude-opus-4-8")
JUDGE = os.environ.get("HEADROOM_JA_JUDGE", "claude-haiku-4-5")

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def _text(resp) -> str:
    return "".join(b.text for b in resp.content if b.type == "text").strip()


_ANSWER_SYSTEM = (
    "あなたはツール出力のJSONデータを読み、質問に日本語で簡潔に答えるアシスタントです。"
    "まず、表示されている行だけで答えられるか必ず確認してください。"
    "特定の1件を探す質問（誰が・どれが・何番）は、その行が表示されていれば"
    "そのまま答え、retrieve_original は絶対に呼ばないでください。"
    "retrieve_original を呼んでよいのは次の2つの場合だけです: "
    "(1) 合計・件数・平均などの集計で『N件 省略』により全件が必要なとき、"
    "(2) 探している具体的な項目が表示行のどこにも見当たらないとき。"
    "フィルタ付き集計（例: ある条件の合計）では query を付けずに全件取得し、"
    "自分で条件を絞って計算してください。"
    "答えが見つからなければ「不明」とだけ答えてください。余計な説明はしないでください。"
)

_RETRIEVE_TOOL = {
    "name": "retrieve_original",
    "description": (
        "圧縮で省略された元データを取り戻す。合計・件数・フィルタ集計など、"
        "表示中の一部の行だけでは正確に答えられない時に呼ぶ。"
        "query を渡すと関連行だけ、省略すると全件を返す。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "絞り込みキーワード（任意・全件なら省略）"},
        },
    },
}


def answer(question: str, context: str, cache_key: str | None = None):
    """Answer the question. If cache_key is given, expose a retrieve tool so the
    model can pull back the dropped originals when the compressed view is
    insufficient. Returns (answer_text, retrieve_called)."""
    from headroom_ja import retrieve as _retrieve

    client = _get_client()
    tools = [_RETRIEVE_TOOL] if cache_key else []
    messages = [{"role": "user",
                 "content": f"# データ\n{context}\n\n# 質問\n{question}"}]
    retrieve_called = False

    for _ in range(4):  # bounded agentic loop
        kwargs = dict(model=ANSWERER, max_tokens=1024,
                      system=_ANSWER_SYSTEM, messages=messages)
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type == "tool_use" and b.name == "retrieve_original":
                    retrieve_called = True
                    q = (b.input or {}).get("query")
                    items = _retrieve(cache_key, q, limit=100000)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(items, ensure_ascii=False)})
            messages.append({"role": "user", "content": results})
            continue

        return _text(resp), retrieve_called

    return _text(resp), retrieve_called


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "correct": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["correct", "reason"],
    "additionalProperties": False,
}


def judge(question: str, gold: str, candidate: str) -> dict:
    """Return {'correct': bool, 'reason': str} comparing candidate to gold."""
    resp = _get_client().messages.create(
        model=JUDGE,
        max_tokens=256,
        output_config={"format": {"type": "json_schema", "schema": _JUDGE_SCHEMA}},
        messages=[{"role": "user", "content": (
            f"質問: {question}\n"
            f"正解: {gold}\n"
            f"回答: {candidate}\n\n"
            "回答が正解と意味的に一致していれば correct=true、そうでなければ false。"
            "表現や言い回しの違いは許容してください。"
        )}],
    )
    return json.loads(_text(resp))
