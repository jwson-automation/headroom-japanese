# Limitations

Honest list of what this does not do well, and why. Backed by the benchmark
(`benchmark.md`) and `benchmarks/history/`.

## Compression is lossy — aggregation needs retrieve
The default path drops rows. Sum / count / filtered-aggregate questions cannot be
answered from the kept rows alone. They are handled by the LLM calling
`retrieve_original` to pull the originals back and recomputing (verified). If you
do not wire the retrieve tool, enable `include_summary=True` (covers plain
count/sum/min/max, **not** filtered aggregates) or `lossless_first=True` (keeps
all rows, larger output).

## The model itself miscounts / mis-sums
On large arrays the LLM sometimes gets counts/sums wrong **even on the full,
uncompressed context** (e.g. counting 200 items off by one). That is a model
compute limit, not a compression fault — the benchmark has caught the full-context
baseline being wrong while the compressed+retrieve answer was right.

## Over-retrieval is LLM judgment noise
When given a retrieve tool, the model sometimes retrieves for simple lookups whose
answer is already visible (wastes tokens; answer stays correct). Prompt wording
barely moves this (~6–7/19, varies run to run). The deterministic control is
`context_tracker` proactive expansion, not prompting.

## Ranking beyond the extremes
We always keep each numeric field's **min and max**, so "cheapest"/"most
expensive" work. The **2nd/3rd-highest** (runner-up) at a middle position, when
it isn't a 2σ outlier, is dropped by default (`gen_second_highest`). Set
`CrusherConfig(keep_top_k=2)` to keep the runner-up (top-2 + bottom-2 per numeric
field); ranking deeper than `keep_top_k` still needs retrieve. Median and other
order-statistics likewise need retrieve
(`gen_median`); the optional summary carries avg, not median.

## Rule-based Japanese tokenizer
No morphological analyzer. Splits on function words + script boundaries. Fails
when a particle is glued inside a kana word, or for novel segmentation. Mitigated
by: rare-value/outlier rules catching items relevance misses, and
`pip install '.[accurate]'` to swap in `fugashi`.

## 2-character kanji partial matches
Relevance gives a substring bonus only to query words of length ≥ 3, so a 2-char
kanji query word that is a *substring* of a longer value word (`東京` ⊂ `東京都`)
gets no bonus and may score below threshold. Lowering the threshold to 2 for CJK
was tried and reverted: it re-introduces generic-word pollution (`記事`, `店舗`
also get the bonus and keep everything). Length alone can't separate "generic"
from "meaningful" 2-char kanji, so the ≥3 threshold stays. Exact 2-char matches
still work via keyword overlap; only *partial* compound matches are affected.

## Token counter approximation
Without `tiktoken` the counter is a heuristic (CJK≈1 token, ASCII≈4 chars/token).
Savings numbers are approximate; install `tiktoken` for exact counts.

## Scope
JSON arrays (and the largest array in a nested object) + line-based log/search/
diff. Not handled: code (AST) compression, streaming/SSE, a proxy server. These
exist in headroom; this is a library, by design.

## Proactive expansion is single-process / in-memory
The tracker and CCR store are in-memory and per-process. No persistence across
restarts. Workspace key prevents cross-project leaks within a process.
