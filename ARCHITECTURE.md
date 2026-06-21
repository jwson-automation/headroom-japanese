# Architecture

A small, pure-Python, Japanese-adapted reimplementation of
[headroom](https://github.com/chopratejas/headroom)'s context-compression ideas.
Every subsystem is grounded in a specific part of headroom; this document maps
each module to its source so the design can be traced rather than guessed.

## Pipeline

```
compress(content, query)
  │
  ├─ detect()  ─ ContentRouter: json | log | search | diff | text   (router.py)
  │
  ├─ if log/search/diff → line compressors                          (text_compress.py)
  │
  └─ if json → locate array (nested-aware)                          (__init__._find_largest_array)
        │
        ├─ [optional] lossless columnar compaction (lossless_first) (compaction.py)
        │
        └─ crush_array() — lossy row selection                      (crusher.py)
              1 dedup (ignore id-like keys)
              2 keep errors        (lexicon_ja.ERROR_KEYWORDS)
              3 numeric outliers   (median+MAD, mean+std fallback)
              4 numeric min/max    (always)
              5 rare categorical values (Pareto)
              6 structural outliers (rare fields)
              7 relevance          (relevance.py — headroom graded scorer)
              + first/last anchors, fill to budget
        │
        ├─ marker "[N/total 件 省略 · retrieve key=...]"
        ├─ originals cached for retrieve                            (cache.py CCRStore)
        └─ [optional] whole-array summary (include_summary)         (crusher.summarize)

retrieve(key, query)        ─ pull dropped originals back          (cache.py)
proactive_expand(query)     ─ pre-expand relevant prior compressions (context_tracker.py)
```

## Module → headroom source

| Module | headroom source | Notes |
|---|---|---|
| `router.py` | `compression/detector.py` (`FallbackDetector`) | heuristic, no Magika; log checked before search (timestamp vs `file:line:`) |
| `crusher.py` | `transforms/smart_crusher.py` + `crates/.../smart_crusher/{orchestration,outliers,statistics}.rs` | dedup→critical-first→fill; critical kept past budget (quality guarantee). Defaults match headroom (max 15, 2σ, 0.3/0.15) |
| `relevance.py` | `ccr/context_tracker.py` `_calculate_relevance` + `_extract_keywords` | graded overlap×0.5 + substring bonus; stopwords (EN + JP interrogatives); fed by the JP tokenizer |
| `context_tracker.py` | `ccr/context_tracker.py` | proactive expansion: age discount, LRU, workspace gate (GH #462), full-vs-search decision |
| `cache.py` | `ccr/` CCR store + `response_handler.py` | LRU store; `retrieve(key, query)` filters by JP keywords |
| `compaction.py` | `crates/.../smart_crusher/compaction/compactor.rs` | lossless columns+rows; accept ≥15% savings |
| `text_compress.py` | content-type handlers (log/search/diff) | template-dedup logs, per-file search cap, diff context trim |
| `tokenizer_ja.py` | (Japanese-specific; not in headroom) | rule-based: function words as delimiters, script-boundary split, okurigana kept |
| `summarize()` | `SmartCrusherConfig.include_summaries` (off by default in headroom too) | optional aggregates; the general aggregation path is retrieve |

## Key design decisions (and why)

- **Lossy + retrieve, not pre-baked summaries.** Aggregation (sum/count/filtered)
  is answered by the LLM calling `retrieve` to pull originals and recompute —
  headroom uses `include_summaries=False` for the same reason. Summaries remain an
  optional cheap shortcut, not the mechanism.
- **Over-retrieval is LLM judgment noise, not prompt-tunable.** The deterministic
  control is `context_tracker` proactive expansion (decide relevance up front),
  not prompt wording.
- **Japanese without a morphological analyzer.** `tokenizer_ja.py` splits on
  function words and script boundaries. Trade-off: under-splits rather than
  destroys words. Swap in `fugashi` (`pip install '.[accurate]'`) for accuracy.

## Verification

- Unit tests: `tests/` (run all via the snippet in `benchmarks/README.md`).
- Quality: `benchmarks/` — savings + LLM-judged retention. `--no-llm` is free
  (deterministic `answer_kept`); the full run needs `ANTHROPIC_API_KEY`. History
  snapshots in `benchmarks/history/`, latest in `benchmark.md`.
