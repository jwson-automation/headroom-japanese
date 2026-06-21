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

__version__ = "0.12.0"

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
    *,
    turn: int | None = None,
    workspace: str = "",
    tool_name: str | None = None,
) -> CompressResult:
    """Classify the content, then compress per type. v1 compresses JSON arrays,
    including the largest array inside a top-level JSON object (API envelopes).

    Pass turn + workspace to register the compression with the proactive-expansion
    tracker (multi-turn), so a later query can pre-expand the dropped originals."""
    cfg = config or CrusherConfig()
    kind = detect(content)
    orig_tok = count_tokens(content)

    def passthrough(ct: str) -> CompressResult:
        return CompressResult(content, orig_tok, orig_tok, content_type=ct)

    # Too small to bother — avoids adding a marker for no benefit.
    if orig_tok < MIN_TOKENS_TO_CRUSH:
        return passthrough(kind)

    # Line-based content types (logs / search results / diffs / code).
    if kind in ("log", "search", "diff", "code"):
        from .text_compress import HANDLERS
        out, total, kept = HANDLERS[kind](content)
        comp_tok = count_tokens(out)
        if comp_tok >= orig_tok:
            return passthrough(kind)
        return CompressResult(out, orig_tok, comp_tok, content_type=kind,
                              kept=kept, dropped=total - kept)

    if kind != "json":
        return passthrough(kind)

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

    # Lossless-first: if the array is cleanly tabular and columnar compaction
    # saves enough, use it — no rows dropped, no retrieve ever needed.
    if cfg.lossless_first:
        from .compaction import compact
        packed = compact(arr, cfg.core_field_fraction)
        if packed is not None:
            if rewrap is not None:
                work, parent, key = rewrap
                parent[key] = packed
                packed_text = json.dumps(work, ensure_ascii=False)
            else:
                packed_text = json.dumps(packed, ensure_ascii=False)
            packed_tok = count_tokens(packed_text)
            if packed_tok < orig_tok and \
                    1 - packed_tok / orig_tok >= cfg.lossless_min_savings_ratio:
                return CompressResult(packed_text, orig_tok, packed_tok,
                                      content_type="json", kept=len(arr), dropped=0)

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

    # Register with the proactive-expansion tracker (multi-turn) if asked.
    if turn is not None and cache_key is not None:
        from .context_tracker import get_context_tracker
        get_context_tracker().track_compression(
            cache_key, turn, tool_name, len(arr), len(keep),
            workspace_key=workspace, query_context=query or "", sample_content=out,
        )

    return CompressResult(
        text=out,
        original_tokens=orig_tok,
        compressed_tokens=comp_tok,
        content_type="json",
        cache_key=cache_key,
        kept=len(keep),
        dropped=len(dropped),
    )


def proactive_expand(query: str, *, turn: int | None = None, workspace: str = ""):
    """Multi-turn: given a new query, pre-expand dropped originals that are now
    relevant. Returns (context_block_text, recommendations). Prepend the block to
    the next turn's context so the model rarely needs to call retrieve."""
    from .context_tracker import get_context_tracker
    t = get_context_tracker()
    recs = t.analyze_query(query, current_turn=turn, workspace_key=workspace)
    exps = t.execute_expansions(recs)
    return t.format_expansions_for_context(exps), recs


def retrieve(key: str, query: str | None = None, limit: int = 50) -> list:
    """Fetch originals dropped during compression, by key (called when the LLM needs more)."""
    return STORE.retrieve(key, query, limit)


__all__ = [
    "compress", "retrieve", "proactive_expand", "CompressResult",
    "CrusherConfig", "detect", "count_tokens", "STORE",
]
