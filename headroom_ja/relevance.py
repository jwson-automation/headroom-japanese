"""Query-relevance scoring — ported from headroom's ccr/context_tracker.py
(`_calculate_relevance` + `_extract_keywords`), adapted to Japanese.

headroom scores graded keyword overlap, not binary, then thresholds it:
    score = |overlap| / |query_keywords| * 0.5
          + 0.2 for each query keyword (len>=3) that is a substring of the item
    keep when score >= relevance_threshold (headroom default 0.3)

This naturally avoids a single generic word (記事) marking everything relevant:
one matching word out of several gives a small fraction, and short generic words
get no substring bonus. The English split is replaced by the JP tokenizer; we add
Japanese interrogative/filler stopwords to headroom's English stopword set.
"""

from __future__ import annotations

from .tokenizer_ja import keywords

# headroom's English stopwords + Japanese interrogatives / content-free fillers.
_STOPWORDS = {
    # Japanese question words and fillers (carry no content for matching)
    "誰", "何", "何番", "どれ", "どの", "どちら", "いくら", "いつ", "どこ", "なぜ",
    "ですか", "です", "ある", "いる", "全部", "全て", "すべて", "もの", "こと",
    # English (from headroom)
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "and", "or", "but", "if", "this", "that", "these", "those",
    "what", "which", "who", "how", "where", "when", "why", "it", "you",
}


def query_keywords(text: str) -> set[str]:
    return {w for w in keywords(text) if w not in _STOPWORDS}


def score(query_kw: set[str], item_text: str) -> float:
    """headroom's graded relevance of one item to the query."""
    if not query_kw:
        return 0.0
    item_lower = item_text.lower()
    item_kw = {w for w in keywords(item_text) if w not in _STOPWORDS}
    s = 0.0
    if item_kw:
        s += len(query_kw & item_kw) / len(query_kw) * 0.5
    for w in query_kw:
        if len(w) >= 3 and w in item_lower:
            s += 0.2
    return min(s, 1.0)
