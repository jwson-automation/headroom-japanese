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
    "あなたはツール出力のJSONデータを読み、ユーザーの質問に日本語で簡潔に答えるアシスタントです。"
    "答えがデータに含まれていない場合は「不明」とだけ答えてください。余計な説明はしないでください。"
)


def answer(question: str, context: str) -> str:
    """Answer the question using the given (full or compressed) context."""
    resp = _get_client().messages.create(
        model=ANSWERER,
        max_tokens=1024,
        system=_ANSWER_SYSTEM,
        messages=[{"role": "user",
                   "content": f"# データ\n{context}\n\n# 質問\n{question}"}],
    )
    return _text(resp)


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
