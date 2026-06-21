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

import json

from .cache import CCRStore
from .crusher import CrusherConfig, crush_array
from .router import detect
from .tokens import count_tokens
from .types import CompressResult

__version__ = "0.3.0"

# Minimum input tokens worth compressing (matches headroom's min_tokens_to_crush).
MIN_TOKENS_TO_CRUSH = 200

# Global CCR store (used by retrieve to fetch dropped originals)
STORE = CCRStore()


def _largest_array_field(d: dict):
    """Return (key, list) of the longest list-valued field, or None."""
    arrays = [(k, v) for k, v in d.items() if isinstance(v, list)]
    if not arrays:
        return None
    return max(arrays, key=lambda kv: len(kv[1]))


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

    # Locate the array to compress: a top-level list, or the largest list field
    # inside a top-level object (e.g. {"results": [...]}).
    rewrap_key = None
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        found = _largest_array_field(data)
        if not found:
            return passthrough("json")
        rewrap_key, arr = found
    else:
        return passthrough("json")

    keep, dropped = crush_array(arr, query, cfg)
    kept_items = [arr[i] for i in keep]

    if rewrap_key is not None:
        parent = dict(data)
        parent[rewrap_key] = kept_items
        out = json.dumps(parent, ensure_ascii=False)
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
