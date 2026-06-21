# Lossless vs lossy — which compression mode?

Two compression strategies, a real trade-off. Numbers below are from a uniform
200-row order array (`--no-llm`, approx token backend).

| | tokens | savings | rows dropped | retrieve needed? | when it wins |
|---|---|---|---|---|---|
| **Lossy** (default) | 3695 → 282 | **92%** | 185/200 | for aggregation / dropped rows | most savings; you only need a few representative + critical rows |
| **Lossless** (`lossless_first=True`) | 3695 → 2009 | **46%** | 0 | **never** | you need every row available, or want zero retrieve round-trips |

```python
from headroom_ja import compress
from headroom_ja.crusher import CrusherConfig

compress(content)                                          # lossy (default)
compress(content, config=CrusherConfig(lossless_first=True))  # lossless columnar
```

## How each works
- **Lossy** keeps ~15 rows (errors, outliers, min/max, rare values, query-relevant,
  first/last) and drops the rest, caching originals so the LLM can `retrieve` them.
  Aggregation (sum/count/median) is answered via that retrieve round-trip.
- **Lossless** factors the repeated key names out of every row into a single
  header (`{"_columns":[...], "_rows":[[...]]}`). All data stays; nothing to
  retrieve; but the output is larger because every row is still present.

## Rule of thumb
- **Default to lossy.** It is the bigger win and, with the retrieve loop, loses no
  correctness — the model pulls back what it needs.
- **Use lossless** when: the downstream task will scan/aggregate the *whole* array
  every turn (so retrieve would fire constantly), or you cannot wire a retrieve
  tool, or the array is cleanly tabular and ≥15% is enough savings on its own.
- Lossless only triggers when the array is uniform enough and clears
  `lossless_min_savings_ratio` (0.15); otherwise it falls through to lossy.

See `LIMITATIONS.md` for aggregation/retrieve details and `ARCHITECTURE.md` for
the pipeline.
