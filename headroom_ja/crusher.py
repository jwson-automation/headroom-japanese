"""SmartCrusher (Japanese): shrink a JSON array using 6 rules.

Order:
  1. Drop duplicates    (same content hash, ignoring identity keys -> keep one)
  2. Always keep errors  (keyword match, incl. Japanese)   <- never dropped
  3. Keep numeric outliers (robust median + MAD, > Nσ-equivalent)
  4. Keep structural outliers (items with rare fields)
  5. Keep query-relevant items (keyword overlap via JP tokenizer)
  6. First/last anchors + fill to the target count (even sampling)

Critical items (2-5) are kept even past the budget = quality guarantee.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .lexicon_ja import ERROR_KEYWORDS
from . import relevance as _rel

# Identity / noise keys excluded from the dedup hash, so records that differ
# only by id/timestamp still collapse as duplicates.
_DEFAULT_IGNORE = (
    "id", "_id", "uuid", "guid", "seq", "index", "_index",
    "timestamp", "ts", "time", "created_at", "updated_at", "date",
)


@dataclass
class CrusherConfig:
    min_items: int = 5                # skip compression below this many items
    max_items: int = 15              # target count after compression
    variance_threshold: float = 2.0  # numeric outlier threshold (MAD-based, ~σ)
    core_field_fraction: float = 0.8  # appears in >= this fraction -> "common field"
    first_fraction: float = 0.3
    last_fraction: float = 0.15
    dedup_ignore_keys: tuple = _DEFAULT_IGNORE
    relevance_threshold: float = 0.3  # headroom's graded-relevance keep threshold
    rare_value_fraction: float = 0.1  # categorical value in <= this fraction = rare
    rare_value_max_distinct: int = 50  # skip high-cardinality (id-like) fields
    keep_numeric_extremes: bool = True  # always keep per-field min & max items
    include_summary: bool = False      # optional cheap aggregates; the general path
                                       # for aggregation is the retrieve() loop
    lossless_first: bool = False       # try lossless columnar compaction before
                                       # the lossy row-drop path (headroom-style)
    lossless_min_savings_ratio: float = 0.15  # accept compaction only above this

    summary_max_distinct: int = 20     # categorical freq only below this cardinality


def _dumps(item) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _item_hash(item, ignore_keys) -> str:
    if isinstance(item, dict) and ignore_keys:
        item = {k: v for k, v in item.items() if k not in ignore_keys}
    return hashlib.md5(_dumps(item).encode("utf-8")).hexdigest()[:16]


def _looks_error(text: str) -> bool:
    low = text.lower()
    return any(kw.lower() in low for kw in ERROR_KEYWORDS)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    m = len(s)
    mid = m // 2
    return s[mid] if m % 2 else (s[mid - 1] + s[mid]) / 2


def _numeric_outliers(data, pool, threshold) -> set[int]:
    """Robust outliers, so an outlier cannot mask itself.

    Primary: median + MAD (modified z = 0.6745 * |x - median| / MAD).
    Fallback: when MAD == 0 (majority of values identical, common after dedup),
    use mean + population std with a >= comparison so a value sitting exactly at
    the threshold is still flagged.
    """
    keep: set[int] = set()
    dicts = [(i, data[i]) for i in pool if isinstance(data[i], dict)]
    if len(dicts) < 3:
        return keep
    num_keys: set[str] = set()
    for _, d in dicts:
        for k, v in d.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                num_keys.add(k)
    for k in num_keys:
        vals = [(i, d[k]) for i, d in dicts
                if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool)]
        if len(vals) < 3:
            continue
        nums = [v for _, v in vals]
        med = _median(nums)
        mad = _median([abs(v - med) for v in nums])
        if mad > 0:
            for i, v in vals:
                if 0.6745 * abs(v - med) / mad > threshold:
                    keep.add(i)
        else:
            mu = sum(nums) / len(nums)
            sd = (sum((x - mu) ** 2 for x in nums) / len(nums)) ** 0.5
            if sd == 0:
                continue
            for i, v in vals:
                if abs(v - mu) / sd >= threshold:
                    keep.add(i)
    return keep


def _rare_value_outliers(data, pool, fraction, max_distinct) -> set[int]:
    """Items carrying a RARE categorical/boolean value (e.g. one status:返品 among
    thousands of 支払済, or is_vip:true on one item). Pareto-style: skip numeric
    fields (handled by outliers) and id-like high-cardinality fields.
    """
    dicts = [(i, data[i]) for i in pool if isinstance(data[i], dict)]
    n = len(dicts)
    if n < 5:
        return set()
    # key -> value -> list of indices (str / bool values only)
    by_key: dict[str, dict] = {}
    for i, d in dicts:
        for k, v in d.items():
            if isinstance(v, bool) or isinstance(v, str):
                by_key.setdefault(k, {}).setdefault(v, []).append(i)
    keep: set[int] = set()
    for k, vmap in by_key.items():
        distinct = len(vmap)
        if distinct < 2 or distinct > max_distinct or distinct > n * 0.5:
            continue  # constant, high-cardinality, or id-like
        for v, idxs in vmap.items():
            if len(idxs) / n <= fraction:
                keep.update(idxs)
    return keep


def _numeric_extremes(data, pool) -> set[int]:
    """Always keep the min and max item of every numeric field (catches
    'cheapest' / 'most expensive' / 'oldest' questions even when not 2σ outliers)."""
    keep: set[int] = set()
    dicts = [(i, data[i]) for i in pool if isinstance(data[i], dict)]
    if len(dicts) < 3:
        return keep
    num_keys: set[str] = set()
    for _, d in dicts:
        for k, v in d.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                num_keys.add(k)
    for k in num_keys:
        vals = [(i, d[k]) for i, d in dicts
                if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool)]
        if len(vals) < 3:
            continue
        keep.add(min(vals, key=lambda x: x[1])[0])
        keep.add(max(vals, key=lambda x: x[1])[0])
    return keep


def _structural_outliers(data, pool, core_fraction) -> set[int]:
    """Items carrying a rare field = items shaped differently from the rest."""
    dicts = [(i, data[i]) for i in pool if isinstance(data[i], dict)]
    if len(dicts) < 3:
        return set()
    freq: dict[str, int] = {}
    for _, d in dicts:
        for k in d:
            freq[k] = freq.get(k, 0) + 1
    n = len(dicts)
    rare = {k for k, c in freq.items() if c / n < (1 - core_fraction)}
    return {i for i, d in dicts if rare & set(d.keys())}


def _values_text(obj) -> str:
    """Concatenate an item's VALUES (recursively), excluding JSON key names.

    Relevance must match content, not structure: a query word like 'tier' that is
    a key present on every item would otherwise mark the whole array relevant and
    defeat compression (key-name pollution, sibling of the 記事 value problem)."""
    parts: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, bool):
            parts.append(str(o))
        elif isinstance(o, (int, float)):
            parts.append(str(o))
        elif isinstance(o, str):
            parts.append(o)

    walk(obj)
    return " ".join(parts)


def _relevant(data, pool, query, threshold) -> set[int]:
    """Items relevant to the query, scored with headroom's graded relevance
    (see headroom_ja.relevance, ported from headroom's ccr/context_tracker.py).
    Scored against item VALUES only, so key names don't pollute the match."""
    qk = _rel.query_keywords(query)
    if not qk:
        return set()
    return {i for i in pool if _rel.score(qk, _values_text(data[i])) >= threshold}


def _even_sample(pool: list[int], k: int) -> list[int]:
    """Pick k items spread evenly across the pool, endpoints included."""
    if k <= 0 or not pool:
        return []
    if len(pool) <= k:
        return list(pool)
    if k == 1:
        return [pool[len(pool) // 2]]
    step = (len(pool) - 1) / (k - 1)
    return [pool[round(i * step)] for i in range(k)]


def summarize(items: list, ignore_keys=()) -> dict:
    """Whole-array aggregates so aggregation questions (sum / count / min / max)
    are answerable even after lossy row-dropping. Computed over EVERY item.

    Numeric fields -> {合計, 最小, 最大, 平均}. Low-cardinality categorical/bool
    fields -> value frequency. id-like / high-cardinality fields are skipped.
    """
    dicts = [d for d in items if isinstance(d, dict)]
    if not dicts:
        return {}
    out: dict = {"件数": len(items)}
    num_keys: set[str] = set()
    cat_keys: set[str] = set()
    for d in dicts:
        for k, v in d.items():
            if k in ignore_keys:
                continue
            if isinstance(v, bool) or isinstance(v, str):
                cat_keys.add(k)
            elif isinstance(v, (int, float)):
                num_keys.add(k)
    for k in num_keys:
        vals = [d[k] for d in dicts
                if isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool)]
        if vals:
            out[k] = {"合計": sum(vals), "最小": min(vals), "最大": max(vals),
                      "平均": round(sum(vals) / len(vals), 2)}
    for k in cat_keys:
        freq: dict = {}
        for d in dicts:
            v = d.get(k)
            if v is None:
                continue
            freq[v] = freq.get(v, 0) + 1
        if 1 < len(freq) <= 20:
            out[f"{k}_値別件数"] = {str(kk): vv
                                    for kk, vv in sorted(freq.items(), key=lambda x: -x[1])}
    return out


def crush_array(data: list, query: str | None, cfg: CrusherConfig):
    """Shrink the array; return (sorted kept indices, dropped indices)."""
    n = len(data)
    if n < cfg.min_items:
        return list(range(n)), []

    # 1) Dedup (ignoring identity keys) -> unique pool
    seen: set[str] = set()
    pool: list[int] = []
    for i, it in enumerate(data):
        h = _item_hash(it, cfg.dedup_ignore_keys)
        if h in seen:
            continue
        seen.add(h)
        pool.append(i)

    # 2-5) Critical items (always preserved)
    critical: set[int] = set()
    for i in pool:
        if _looks_error(_dumps(data[i])):
            critical.add(i)
    critical |= _numeric_outliers(data, pool, cfg.variance_threshold)
    critical |= _structural_outliers(data, pool, cfg.core_field_fraction)
    critical |= _rare_value_outliers(data, pool, cfg.rare_value_fraction,
                                     cfg.rare_value_max_distinct)
    if cfg.keep_numeric_extremes:
        critical |= _numeric_extremes(data, pool)
    if query:
        critical |= _relevant(data, pool, query, cfg.relevance_threshold)

    # 6) Fill the budget — critical first (even past it), then first/last, then even sampling
    selected: set[int] = set(critical)

    first_k = max(1, round(n * cfg.first_fraction))
    last_k = max(1, round(n * cfg.last_fraction))
    anchors = pool[:first_k] + pool[-last_k:]
    for i in anchors:
        if len(selected) >= cfg.max_items:
            break
        selected.add(i)

    if len(selected) < cfg.max_items:
        remaining = [i for i in pool if i not in selected]
        for i in _even_sample(remaining, cfg.max_items - len(selected)):
            selected.add(i)

    keep = sorted(selected)
    dropped = [i for i in range(n) if i not in selected]
    return keep, dropped
