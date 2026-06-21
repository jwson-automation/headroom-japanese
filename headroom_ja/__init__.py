"""headroom-japanese: Japanese context compression.

A from-scratch, rule-based reimplementation of headroom's core ideas
(ContentRouter + SmartCrusher + CCR) with no morphological analyzer.
Both data and query are assumed to be Japanese.

Basic use:
    from headroom_ja import compress
    r = compress(tool_output_json, query="拒否された注文は？")
    print(r)              # prints savings
    send_to_llm(r.text)   # compressed text
"""

from __future__ import annotations

import copy
import json

from .cache import CCRStore
from .crusher import CrusherConfig, crush_array, summarize
from .router import detect
from .tokens import count_tokens
from .types import CompressResult

__version__ = "0.5.0"

# Minimum input tokens worth compressing (matches headroom's min_tokens_to_crush).
MIN_TOKENS_TO_CRUSH = 200

# Global CCR store (used by retrieve to fetch dropped originals)
STORE = CCRStore()


def _find_largest_array(obj):
    """Find the longest list anywhere in a nested dict/list tree.

    Returns (parent, key_or_index, list) where parent[key] is the list, so the
    caller can replace it in place. Handles API envelopes like
    {"response": {"data": {"orders": [...]}}}. Returns None if no list exists.
    """
    best = None

    def walk(node):
        nonlocal best
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list) and (best is None or len(v) > len(best[2])):
                    best = (node, k, v)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return best


def compress(
    content: str,
    query: str | None = None,
    config: CrusherConfig | None = None,
    reversible: bool = True,
) -> CompressResult:
    """Classify the content, then compress per type. v1 compresses JSON arrays,
    including the largest array inside a top-level JSON object (API envelopes)."""
    cfg = config or CrusherConfig()
    kind = detect(content)
    orig_tok = count_tokens(content)

    def passthrough(ct: str) -> CompressResult:
        return CompressResult(content, orig_tok, orig_tok, content_type=ct)

    if kind != "json":
        # v1: pass everything non-JSON through (log/search/diff come later)
        return passthrough(kind)

    # Too small to bother — avoids adding a marker for no benefit.
    if orig_tok < MIN_TOKENS_TO_CRUSH:
        return passthrough("json")

    data = json.loads(content)

    # Locate the array to compress: a top-level list, or the largest list found
    # anywhere inside a nested object (e.g. {"response":{"data":{"orders":[...]}}}).
    if isinstance(data, list):
        arr = data
        rewrap = None
    else:
        work = copy.deepcopy(data)
        found = _find_largest_array(work)
        if not found:
            return passthrough("json")
        parent, key, arr = found
        rewrap = (work, parent, key)

    keep, dropped = crush_array(arr, query, cfg)
    kept_items = [arr[i] for i in keep]

    if rewrap is not None:
        work, parent, key = rewrap
        parent[key] = kept_items
        out = json.dumps(work, ensure_ascii=False)
    else:
        out = json.dumps(kept_items, ensure_ascii=False)

    cache_key = None
    if dropped:
        if reversible:
            cache_key = STORE.put(arr)
            # marker stays Japanese on purpose: it is read by a Japanese LLM
            out += f"\n[{len(dropped)}/{len(arr)}件 省略 · retrieve key={cache_key}]"
        else:
            out += f"\n[{len(dropped)}/{len(arr)}件 省略]"

    # Whole-array aggregates so sum/count/min/max stay answerable after row-drop.
    if cfg.include_summary and dropped:
        summary = summarize(arr, cfg.dedup_ignore_keys)
        if summary:
            out += "\n" + json.dumps({"_集計_全体": summary}, ensure_ascii=False)

    comp_tok = count_tokens(out)

    # Inflation guard: never return something larger than the input.
    if comp_tok >= orig_tok:
        return passthrough("json")

    return CompressResult(
        text=out,
        original_tokens=orig_tok,
        compressed_tokens=comp_tok,
        content_type="json",
        cache_key=cache_key,
        kept=len(keep),
        dropped=len(dropped),
    )


def retrieve(key: str, query: str | None = None, limit: int = 50) -> list:
    """Fetch originals dropped during compression, by key (called when the LLM needs more)."""
    return STORE.retrieve(key, query, limit)


__all__ = [
    "compress", "retrieve", "CompressResult",
    "CrusherConfig", "detect", "count_tokens", "STORE",
]
