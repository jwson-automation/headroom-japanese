"""SmartCrusher (Japanese): shrink a JSON array using 6 rules.

Order:
  1. Drop duplicates    (same content hash -> keep one)
  2. Always keep errors  (keyword match, incl. Japanese)   <- never dropped
  3. Keep numeric outliers (> Nσ from the per-field mean)
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
from .tokenizer_ja import keywords


@dataclass
class CrusherConfig:
    min_items: int = 5               # skip compression below this many items
    max_items: int = 15              # target count after compression
    variance_threshold: float = 2.0  # numeric outlier threshold (σ)
    core_field_fraction: float = 0.8  # appears in >= this fraction -> "common field"
    first_fraction: float = 0.3
    last_fraction: float = 0.15


def _dumps(item) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _item_hash(item) -> str:
    return hashlib.md5(_dumps(item).encode("utf-8")).hexdigest()[:16]


def _looks_error(text: str) -> bool:
    low = text.lower()
    return any(kw.lower() in low for kw in ERROR_KEYWORDS)


def _numeric_outliers(data, pool, threshold) -> set[int]:
    """z-score per shared numeric field; indices exceeding the threshold."""
    keep: set[int] = set()
    dicts = [(i, data[i]) for i in pool if isinstance(data[i], dict)]
    if len(dicts) < 3:
        return keep
    # Collect every numeric key
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
        mu = sum(nums) / len(nums)
        var = sum((x - mu) ** 2 for x in nums) / len(nums)
        sd = var ** 0.5 or 1e-9
        for i, v in vals:
            if abs((v - mu) / sd) > threshold:
                keep.add(i)
    return keep


def _structural_outliers(data, pool, core_fraction) -> set[int]:
    """Items carrying a rare field = items shaped differently from the rest."""
    dicts = [(i, data[i]) for i in pool if isinstance(data[i], dict)]
    if len(dicts) < 3:
        return set()
    # Field frequency
    freq: dict[str, int] = {}
    for _, d in dicts:
        for k in d:
            freq[k] = freq.get(k, 0) + 1
    n = len(dicts)
    rare = {k for k, c in freq.items() if c / n < (1 - core_fraction)}
    return {i for i, d in dicts if rare & set(d.keys())}


def _relevant(data, pool, query) -> set[int]:
    """Items whose keywords overlap the query (Japanese tokenizer)."""
    q = keywords(query)
    if not q:
        return set()
    return {i for i in pool if q & keywords(_dumps(data[i]))}


def _even_sample(pool: list[int], k: int) -> list[int]:
    """Pick k items spread evenly across the pool."""
    if k <= 0 or not pool:
        return []
    if len(pool) <= k:
        return list(pool)
    step = len(pool) / k
    return [pool[int(i * step)] for i in range(k)]


def crush_array(data: list, query: str | None, cfg: CrusherConfig):
    """Shrink the array; return (sorted kept indices, dropped indices)."""
    n = len(data)
    if n < cfg.min_items:
        return list(range(n)), []

    # 1) Dedup -> unique pool
    seen: set[str] = set()
    pool: list[int] = []
    for i, it in enumerate(data):
        h = _item_hash(it)
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
    if query:
        critical |= _relevant(data, pool, query)

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
