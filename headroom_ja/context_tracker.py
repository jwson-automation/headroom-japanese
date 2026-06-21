"""Proactive context expansion — ported from headroom's ccr/context_tracker.py.

Across conversation turns, this tracks what was compressed and, on each new
query, decides via relevance whether to PRE-EXPAND the dropped originals — so the
model gets the data it needs without having to call retrieve (and without the
noisy over-/under-retrieval that LLM-driven retrieval alone produces).

Faithful to headroom:
- workspace_key gates cross-project leaks (GH #462) — fail closed on empty.
- age discount: older compressions are less likely to expand (5-min window).
- relevance via headroom's graded scorer (headroom_ja.relevance).
- expand-full vs search-expand decision, max N expansions per turn.
Relevance/keyword extraction is adapted to Japanese through headroom_ja.relevance.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import relevance as _rel


@dataclass
class CompressedContext:
    hash_key: str
    turn_number: int
    timestamp: float
    tool_name: str | None
    original_item_count: int
    compressed_item_count: int
    query_context: str        # the query when compression happened
    sample_content: str       # preview of compressed content (for relevance)
    workspace_key: str        # stable per-project identity (cross-project gate)


@dataclass
class ExpansionRecommendation:
    hash_key: str
    reason: str
    relevance_score: float
    expand_full: bool = True          # True = expand all, False = search only
    search_query: str | None = None


@dataclass
class ContextTrackerConfig:
    enabled: bool = True
    max_tracked_contexts: int = 100        # LRU cap
    relevance_threshold: float = 0.3       # min score to recommend expansion
    max_context_age_seconds: float = 300.0  # 5 minutes
    proactive_expansion: bool = True
    max_proactive_expansions: int = 2      # per turn


class ContextTracker:
    def __init__(self, config: ContextTrackerConfig | None = None):
        self.config = config or ContextTrackerConfig()
        self._contexts: dict[str, CompressedContext] = {}
        self._turn_order: list[str] = []
        self._current_turn = 0

    # ── record a compression ────────────────────────────────────────────
    def track_compression(self, hash_key, turn_number, tool_name,
                          original_count, compressed_count, *, workspace_key,
                          query_context="", sample_content=""):
        if not self.config.enabled:
            return
        ctx = CompressedContext(
            hash_key=hash_key, turn_number=turn_number, timestamp=time.time(),
            tool_name=tool_name, original_item_count=original_count,
            compressed_item_count=compressed_count, query_context=query_context,
            sample_content=sample_content[:2000], workspace_key=workspace_key,
        )
        if hash_key in self._contexts:
            self._turn_order.remove(hash_key)
        self._contexts[hash_key] = ctx
        self._turn_order.append(hash_key)
        while len(self._contexts) > self.config.max_tracked_contexts:
            del self._contexts[self._turn_order.pop(0)]
        self._current_turn = max(self._current_turn, turn_number)

    # ── decide what to pre-expand for a new query ───────────────────────
    def analyze_query(self, query, current_turn=None, *, workspace_key):
        if not self.config.enabled or not self.config.proactive_expansion:
            return []
        if not workspace_key:  # fail closed: no provenance -> no cross-project match
            return []
        if current_turn is not None:
            self._current_turn = current_turn

        recs: list[ExpansionRecommendation] = []
        now = time.time()
        for hash_key, ctx in self._contexts.items():
            if ctx.workspace_key != workspace_key:
                continue
            age = now - ctx.timestamp
            if age > self.config.max_context_age_seconds:
                continue
            relevance = self._calculate_relevance(query, ctx)
            age_factor = 1.0 - (age / self.config.max_context_age_seconds) * 0.5
            relevance *= age_factor
            if relevance >= self.config.relevance_threshold:
                expand_full, search_query = self._determine_expansion_type(
                    query, ctx, relevance)
                recs.append(ExpansionRecommendation(
                    hash_key=hash_key,
                    reason=self._generate_reason(ctx, relevance),
                    relevance_score=relevance,
                    expand_full=expand_full, search_query=search_query,
                ))
        recs.sort(key=lambda r: r.relevance_score, reverse=True)
        return recs[: self.config.max_proactive_expansions]

    def _calculate_relevance(self, query, ctx):
        qk = _rel.query_keywords(query)
        if not qk:
            return 0.0
        score = _rel.score(qk, ctx.sample_content)  # sample overlap + substring bonus
        if ctx.query_context:
            ck = _rel.query_keywords(ctx.query_context)
            if ck:
                score += len(qk & ck) / len(qk) * 0.3
        if ctx.tool_name:
            tl = ctx.tool_name.lower()
            if any(w in tl for w in ("find", "glob", "search", "grep", "ls")):
                ql = query.lower()
                if any(w in ql for w in ("file", "where", "find", "show", "list")):
                    score += 0.1
        return min(score, 1.0)

    def _determine_expansion_type(self, query, ctx, relevance):
        # high relevance or small original -> just expand everything
        if relevance > 0.6 or ctx.original_item_count <= 50:
            return True, None
        specific = sorted(_rel.query_keywords(query), key=len, reverse=True)
        if specific:
            return False, " ".join(specific[:3])
        return True, None

    def _generate_reason(self, ctx, relevance):
        parts = []
        if ctx.tool_name:
            parts.append(f"{ctx.tool_name}由来")
        parts.append(f"{ctx.original_item_count}件をturn {ctx.turn_number}で圧縮")
        parts.append("高い関連性" if relevance > 0.5 else "関連の可能性")
        return " / ".join(parts)

    # ── fetch the recommended originals + format for the next prompt ─────
    def execute_expansions(self, recommendations, store=None):
        if store is None:
            from . import STORE as store
        results = []
        for rec in recommendations:
            if rec.expand_full:
                items = store.retrieve(rec.hash_key, None, limit=100000)
                if items:
                    results.append({"hash": rec.hash_key, "type": "full",
                                    "content": items, "item_count": len(items),
                                    "reason": rec.reason})
            else:
                items = store.retrieve(rec.hash_key, rec.search_query or "", limit=100000)
                if items:
                    results.append({"hash": rec.hash_key, "type": "search",
                                    "query": rec.search_query, "content": items,
                                    "item_count": len(items), "reason": rec.reason})
        return results

    def format_expansions_for_context(self, expansions, *, workspace_label=None):
        if not expansions:
            return ""
        header = "[関連データの先読み展開 — あなたの質問に関連"
        if workspace_label:
            header += f" | workspace: {workspace_label}"
        header += "]"
        parts = [header]
        for exp in expansions:
            tag = "全件展開" if exp["type"] == "full" else f"検索: '{exp['query']}'"
            parts.append(f"\n--- {tag} ({exp['reason']}) ---")
            parts.append(json.dumps(exp["content"], ensure_ascii=False))
        parts.append("\n[先読み展開ここまで]")
        return "\n".join(parts)

    def get_stats(self):
        return {"tracked": len(self._contexts), "current_turn": self._current_turn}

    def clear(self):
        self._contexts.clear()
        self._turn_order.clear()
        self._current_turn = 0


_tracker: ContextTracker | None = None


def get_context_tracker() -> ContextTracker:
    global _tracker
    if _tracker is None:
        _tracker = ContextTracker()
    return _tracker


def reset_context_tracker() -> None:
    global _tracker
    _tracker = None
