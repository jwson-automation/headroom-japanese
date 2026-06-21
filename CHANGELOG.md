# Changelog

Retention = fraction of questions still answered correctly from the compressed
context (LLM-judged), of those answerable on the full context.

## 0.10.0 — lossless columnar compaction
Ported headroom's lossless-first compaction (columns + rows). Off by default;
`CrusherConfig(lossless_first=True)`. 46% savings on uniform data, 0 rows dropped.

## 0.9.0 — log / search / diff compressors
Line-based compressors for non-JSON tool output. Router order fixed (timestamp
vs `file:line:`). Compression no longer JSON-only.

## 0.8.0 — proactive context expansion
Ported headroom `ccr/context_tracker.py`: pre-expand relevant prior compressions
before the LLM has to retrieve. Workspace isolation (GH #462), age discount, LRU.

## 0.7.0 — relevance from headroom
Replaced the invented IDF relevance with headroom's graded scorer
(`_calculate_relevance` + `_extract_keywords`, stopwords). No regression.

## 0.6.0 — retrieve loop is the aggregation mechanism
Answerer gets a `retrieve_original` tool + bounded loop; pulls originals back and
recomputes. Demoted the v0.5 summary to off-by-default. Honest finding: over-
retrieval is LLM noise; the deterministic fix is proactive expansion (→ 0.8.0).
Retention 100% (with retrieve).

## 0.5.0 — whole-array aggregates (later demoted)
Embedded sum/count/freq so aggregation answerable without retrieve. Correct but a
band-aid (can't do filtered aggregates) — replaced by the retrieve loop.

## 0.4.0 — round C datasets + 4 fixes
deep_nested (recursive array find), uuid string ids, rare-value (Pareto), numeric
min/max keep. Report embeds full original + exact compressed text per case.
Retention 89%.

## 0.3.0 — rescue 2 benchmark failures
Tokenizer kanji↔katakana split; IDF relevance (later superseded by 0.7.0).
Retention 83%.

## 0.2.0 — critical fixes + safety rails
Kana-word tokenizer fix, retrieve fallback, robust outliers (median+MAD), dedup
ignores id keys, object-of-arrays, min-token gate, inflation guard. Retention 67%.

## 0.1.0 — initial core
ContentRouter + SmartCrusher (6 rules) + CCR-lite + rule-based JP tokenizer.
Zero dependencies. 97% savings on the demo.
