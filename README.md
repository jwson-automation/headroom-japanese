# headroom-japanese

**English** · [日本語](README.ja.md) · [한국어](README.ko.md)

A from-scratch, rule-based reimplementation of [headroom](https://github.com/chopratejas/headroom)'s
**core ideas** (ContentRouter + SmartCrusher + CCR) for **Japanese** context compression.
Both data and query are assumed to be Japanese. Built to be versioned and benchmarked by you.

> headroom's core is ported to Rust and has a large surface area. This project lifts only the
> minimal core, in **pure Python with the standard library**, and adapts it to Japanese.

## How it works (6 steps to shrink an array before it reaches the LLM)

| # | Rule | Method | Japanese handling |
|---|------|--------|-------------------|
| 1 | Drop duplicates | same MD5 content hash -> keep one | language-agnostic |
| 2 | **Keep errors** | keyword containment check | Japanese keywords in `lexicon_ja.py` |
| 3 | Numeric outliers | per-field z-score > 2σ | language-agnostic |
| 4 | Structural outliers | items with rare fields | language-agnostic |
| 5 | **Query relevance** | keyword overlap | rule-based Japanese tokenizer |
| 6 | First/last + fill | position + even sampling | language-agnostic |

Critical items (2–5) are kept **even past the budget** (quality guarantee).
Dropped originals live in a CCR cache and are fetched back via `retrieve(key, query)`.

## Japanese tokenizer (no morphological analyzer)

Function words (particles / conjunctions / verb endings / punctuation) are used as **delimiters**;
the content words between them become keywords.

```
新しいパソコンが欲しい、そして安いキーボードも探している
→ keyword: 新しいパソコン / 安いキーボード / 探
  particle: が / も   ending: 欲しい / している   conj: そして
```

Limitation: a particle embedded inside a word gets mis-split (e.g. the が in `長い`).
Risk is low for kanji-noun-heavy data. For real accuracy, install `pip install '.[accurate]'`
and swap in `fugashi`.

## Install / run

```bash
cd headroom-japanese
pip install -e .              # core only (zero dependencies)
pip install -e '.[accurate]'  # tiktoken (exact tokens) + fugashi (morphology)

python examples/demo.py       # 500 items -> compression demo
pytest                        # tests
```

## Usage

```python
from headroom_ja import compress, retrieve

r = compress(tool_output_json, query="拒否された注文はある？")
print(r)               # [json] 18000 -> 1200 tok (93% saved, 15 kept / 485 dropped)
send_to_llm(r.text)

# when the LLM needs more
more = retrieve(r.cache_key, query="拒否")
```

## Tuning surface

- `headroom_ja/lexicon_ja.py` — particle / conjunction / ending / error-keyword lists.
  **This is where you adapt to your data.**
- `CrusherConfig` — `max_items`, `variance_threshold`, `first/last_fraction`, etc.

## Benchmarking

Log just the `CompressResult` to compare versions:
`(content_type, original_tokens, compressed_tokens, kept, dropped, ratio)`.
Track `retrieve` call frequency alongside it (= an over-compression signal) to see the
savings-vs-quality trade-off.

## License

Apache-2.0 (same as upstream headroom).
