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

__version__ = "0.1.0"

# Global CCR store (used by retrieve to fetch dropped originals)
STORE = CCRStore()


def compress(
    content: str,
    query: str | None = None,
    config: CrusherConfig | None = None,
    reversible: bool = True,
) -> CompressResult:
    """Classify the content, then compress per type. v1 only compresses JSON arrays."""
    cfg = config or CrusherConfig()
    kind = detect(content)
    orig_tok = count_tokens(content)

    if kind != "json":
        # v1: pass everything non-JSON through (log/search/diff come later)
        return CompressResult(content, orig_tok, orig_tok, content_type=kind,
                              kept=0, dropped=0)

    data = json.loads(content)
    if not isinstance(data, list):
        return CompressResult(content, orig_tok, orig_tok, content_type="json")

    keep, dropped = crush_array(data, query, cfg)
    kept_items = [data[i] for i in keep]
    out = json.dumps(kept_items, ensure_ascii=False)

    cache_key = None
    if dropped:
        if reversible:
            cache_key = STORE.put(data)
            # marker stays Japanese on purpose: it is read by a Japanese LLM
            out += f"\n[{len(dropped)}/{len(data)}件 省略 · retrieve key={cache_key}]"
        else:
            out += f"\n[{len(dropped)}/{len(data)}件 省略]"

    return CompressResult(
        text=out,
        original_tokens=orig_tok,
        compressed_tokens=count_tokens(out),
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
