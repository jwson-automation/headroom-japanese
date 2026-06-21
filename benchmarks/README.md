# benchmarks

Track compression quality across versions. Two axes that must always be reported
together:

- **savings** — `1 - compressed/original` (free, deterministic)
- **quality** — can an LLM still answer from the compressed context?

> An empty string is 100% savings and useless. Savings without quality is meaningless.

## Two ways to run

### 1. Deterministic — no API spend

Every synthetic sample plants the answer in a *known* item, so we can check
`answer_kept` (did the compressor keep that item?) with zero LLM calls.

```bash
pip install -e .
python -m benchmarks.run_eval --no-llm
```

### 2. Full — LLM-as-judge (spends credits)

Asks the same Japanese question twice — once on the **full** context, once on the
**compressed** context — then judges each answer against gold. Headline metric:
`retention = mean(compressed_correct | full_correct)`.

```bash
pip install -e '.[bench]'
export ANTHROPIC_API_KEY=sk-ant-...     # use a key you can ROTATE — never commit it
python -m benchmarks.run_eval
```

- Answerer: `claude-opus-4-8` (fluent Japanese). Judge: `claude-haiku-4-5` (cheap).
- Override via `HEADROOM_JA_ANSWERER` / `HEADROOM_JA_JUDGE` env vars.
- The key is read from the environment by the SDK and is **never** written to a file.

## Output

One JSONL row per sample at `benchmarks/results/latest.jsonl` (gitignored):
`dataset, original_tokens, compressed_tokens, ratio, kept, dropped, answer_kept,
full_correct, compressed_correct, quality_delta, ...`

## Comparing versions

1. Run on the current default config → keep the JSONL as a baseline.
2. Change a knob (`CrusherConfig`, or a `lexicon_ja.py` list) → re-run.
3. Compare `mean savings`, `answer_kept rate`, and `retention`. A change that
   trades a little savings for higher retention is good; a savings gain that
   drops retention is a regression.

## Extending

- Add generators to `datasets.py` (return `(data, question, gold, answer_ids)`).
- Real Japanese QA (JSQuAD/JGLUE): wrap the gold passage as one item, pad with
  distractor passages, record its index as `answer_ids`.
- Wire a `retrieve()` tool loop into `models.answer` to also measure the
  over-compression signal (how often the LLM has to ask for dropped data).
