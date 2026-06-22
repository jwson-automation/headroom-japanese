# Rust port plan (8h autonomous, parallel agents)

Goal: make the compression CORE a faithful Rust port of headroom's
`crates/headroom-core/src/transforms/smart_crusher/`, exposed to Python via
PyO3/maturin — **no headroom dependency**, we write our own Rust using their
source as the reference. This closes the biggest equivalence gap (their core is
Rust; my pure-Python paraphrase diverged and grew bugs).

## Build pipeline — PROVEN (Phase 0 ✅)
`crates/headroom_ja_core` (PyO3 0.23) builds a wheel via maturin and imports in
Python (`headroom_ja_core.ping()`). Rebuild: `crates/headroom_ja_core/build.ps1`.

## Seam: what stays Python vs moves to Rust
- **Rust core** (faithful port): dedup/hashing, numeric outliers (median+MAD,
  mean+std), structural outliers, rare-value, numeric extremes/top-k, anchors,
  budget selection / orchestration, lossless compaction.
- **Python (Japanese seam)**: the JP tokenizer, error-keyword matching, and
  relevance scoring stay in Python. Python precomputes per item: `is_error: bool`
  and `relevance: f32`, and passes them + the items into Rust.
- **Rust API**: `crush_indices(items_json, error_flags, relevance_scores, config_json)
  -> {keep: [usize], dropped: [usize]}`; `compact(items_json, core_fraction) -> json|null`.

## Phases
- [x] **P0** build pipeline proven.
- [ ] **P1 (parallel agents)** extract faithful PORT SPECS from headroom's Rust
      into `crates/headroom_ja_core/PORT_SPECS/*.md` (distinct files, read-only):
      1 config+types+hashing+error_keywords · 2 statistics+stats_math+outliers ·
      3 anchors+classifier+field_detect · 4 orchestration+crusher+planning+constraints ·
      5 compaction/* .
- [ ] **P2** implement Rust bottom-up, `cargo check` after each: types/hashing →
      statistics/outliers → rare-value/extremes → anchors → orchestration/crusher.
- [ ] **P3** PyO3 API (`crush_indices`, `compact`) + rebuild + import smoke test.
- [ ] **P4** wire `crusher.py` to call the Rust core (Python computes is_error +
      relevance, Rust selects). Keep a Python fallback behind a flag.
- [ ] **P5** parity: full test suite green + deterministic bench unchanged/better.

## Rules
- Faithful to headroom's logic + defaults (read their `.rs` first; cite in specs).
- Commit + push after each compiling milestone. Tests must stay green at P4/P5.
- No paid LLM API (agent inference is fine — owner opted in).

## Progress log
- P0: PyO3/maturin pipeline proven on Windows (rustc 1.95, py3.13).
- P1: 5 port specs via parallel agents.
- P2/P3: Rust crush_indices implemented (dedup MD5 sorted-keys[:16], stride fill,
  structural + Pareto rare-value outliers, first-3/last-2 critical-first) + PyO3.
  Validated on datasets: answer_kept holds (rejected/rare_status/count_status).
  Faithful to headroom (NO z-score; rare-value is the real mechanism).
- NEXT (P4): wire crusher.py to call core.crush_indices (Python supplies is_error+
  relevance); keep Python fallback if module missing. Then P5 parity + compaction port.
- P4: crusher.py now calls the Rust core (headroom_ja_core.crush_indices) by
  default; Japanese seam (is_error, relevance) + extras (numeric outliers/extremes
  via force_keep, dedup_ignore_keys) passed from Python. Pure-Python fallback kept
  if the wheel is absent. Full suite 44/44 GREEN, bench 87%/95% unchanged.
- NEXT (P5): parity doc + port compact() to Rust (PORT_SPECS/5).
