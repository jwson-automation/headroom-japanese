"""Lossless columnar compaction — ported from headroom's
crates/.../smart_crusher/compaction (compactor.rs).

An array of uniform objects repeats every key name on every row. Compaction
factors the keys out once into a header and emits each row positionally, which
is fully LOSSLESS (no rows dropped, no CCR retrieval ever needed) and removes the
per-row key-name overhead. headroom tries this BEFORE the lossy row-drop path and
accepts it when it clears `lossless_min_savings_ratio`.

    [{"id":0,"user":"kim","amount":12000}, {"id":1,"user":"lee","amount":9000}, ...]
    -> {"_columns":["id","user","amount"], "_rows":[[0,"kim",12000],[1,"lee",9000], ...]}
"""

from __future__ import annotations


def compact(items: list, core_fraction: float = 0.8):
    """Return a columnar dict if the array is cleanly tabular, else None.

    Lossless: every original key/value is reconstructable from columns + rows.
    Missing fields become null in their column (cost: one token, not the key).
    """
    if not isinstance(items, list) or len(items) < 5:
        return None
    if not all(isinstance(d, dict) for d in items):
        return None  # heterogeneous / scalar arrays don't compact cleanly

    n = len(items)
    freq: dict[str, int] = {}
    for d in items:
        for k in d:
            freq[k] = freq.get(k, 0) + 1

    columns = [k for k, _ in sorted(freq.items(), key=lambda kv: -kv[1])]
    core = [k for k in columns if freq[k] >= core_fraction * n]
    if len(core) < 2:
        return None  # too few shared keys -> not worth a columnar form

    rows = [[d.get(k) for k in columns] for d in items]
    return {"_columns": columns, "_rows": rows}
