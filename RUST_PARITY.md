# Rust core ↔ Python fallback parity

The selection engine has two implementations: the Rust core
(`headroom_ja_core.crush_indices`, the default when the wheel is built) and the
pure-Python fallback (`crusher._crush_array_py`, used when the wheel is absent).

## Verified equivalence (deterministic, no LLM)
Across all 23 benchmark datasets, comparing `_crush_array_rust` vs
`_crush_array_py` on the same input:

- **answer_kept mismatches: 0** — both implementations retain the answer-bearing
  item(s) in every dataset.
- **index sets differ: 21/23** — the exact *filler* items differ (see below).
- Deterministic bench is identical either way: **87% mean savings, 95% answer_kept**.
- Full test suite (44) is green under the Rust path.

So the two are **equivalent on correctness** (what gets the answer kept), and
differ only in which non-critical filler rows round out the budget.

## Why the filler differs (by design)
The Rust core is the faithful headroom port; the Python fallback predates it:

| | Rust core (faithful headroom) | Python fallback |
|---|---|---|
| anchors | first **3** / last **2** (fixed, headroom) | `first_fraction`/`last_fraction × n` |
| fill | interleaved **stride** sampling (orchestration.rs) | even-sample stride |
| dedup hash | MD5 of sorted-key JSON, `[:16]` (+ `dedup_ignore_keys` seam) | MD5 of sorted-key JSON, `[:16]` (ignore keys) |

Both keep the critical set (errors, structural + rare-value outliers, relevance,
and our numeric extras) past budget — the quality guarantee — so the answer
survives regardless; only the leftover slots are filled differently.

## Faithful vs simplified (current state)
- **Faithful to headroom**: dedup-by-content, structural rare-field outliers,
  Pareto rare-**value** outliers, `prioritize_indices` (dedup → stride fill →
  under-budget early return → critical-first first-3/last-2 → ascending fill),
  quality-guarantee overshoot.
- **Our extras (not in headroom)**, kept via the `force_keep` seam: numeric
  z-score/MAD outliers and numeric min/max + `keep_top_k`. These exist because the
  Japanese tool-output benchmarks ask "highest/lowest/Nth" — headroom has no
  numeric-outlier rule.
- **Japanese seam (Python)**: error-keyword detection and relevance scoring run in
  Python (JP tokenizer) and are passed into Rust as `is_error[]` / `relevance[]`.
- **Still simplified** (refinement TODOs): `effective_max` is fixed at `max_items`
  (headroom's adaptive `compute_optimal_k` not yet ported); the dedup hash is not
  byte-parity with headroom's `python_json_dumps_sort_keys` (separators/ascii) —
  consistent within our system but not cross-impl identical.

Reproduce: see the comparison snippet in git history / run `_crush_array_rust` vs
`_crush_array_py` over `benchmarks.datasets.build()` and diff `answer_kept`.
