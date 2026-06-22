# PORT SPEC — Statistics, Stats-Math & Outlier Detection

Faithful port spec extracted from headroom's Rust source (`crates/headroom-core/src/transforms/smart_crusher/`).
Reimplement **verbatim** in `headroom_ja_core`. Every formula, constant, threshold and comparison
operator below is taken directly from the cited source. This is a spec only — do not paste headroom
Rust into the crate; write your own implementation that matches these semantics byte-for-byte so
parity fixtures continue to match.

Source files covered:
- `statistics.rs` — field-characterization helpers (UUID, entropy, sequential-pattern).
- `stats_math.rs` — numeric statistics (mean / sample variance / stdev / median) + `format_g`.
- `outliers.rs` — structural & categorical (Pareto) outlier detection + error-keyword preservation.
- `error_keywords.rs` — the `ERROR_KEYWORDS` constant used by `outliers.rs`.

> **IMPORTANT SCOPE NOTE (read before implementing).** The port-spec request asked about
> mean/std-vs-median/MAD **z-score numeric outlier detection** and **sliding-window mean-shift
> change-point detection**. **Neither of those algorithms exists in these three files.** There is
> **no z-score, no modified-z, no MAD, no `variance_threshold`, no change-point / winnowing logic**
> anywhere in `statistics.rs`, `stats_math.rs`, or `outliers.rs`. The numeric path that *does* exist
> is **sequential-pattern detection** (avg pairwise-diff band), and the outlier path that *does* exist
> is **structural rare-field** + **categorical Pareto rare-value** detection. Those z-score / MAD /
> change-point routines presumably live in other modules (e.g. an `anomaly` / `changepoint`
> transform) not provided here. Sections below document **exactly what is present**; the "Absent"
> subsections explicitly call out what was searched for and not found, so the next porter does not
> assume it was missed.

---

## FILE 1 — `statistics.rs`

### Purpose
Heuristic field-characterization helpers used by the analyzer to classify fields (ID-like,
score-like, sequential, high-entropy/random). Direct port of Python `smart_crusher.py:378-481`.
Numeric drift vs Python changes classifications and breaks fixtures, so the math mirrors Python
step-by-step.

Module imports: `serde_json::Value`, `std::collections::HashMap`.

---

### `pub fn is_uuid_format(value: &str) -> bool`
Source: `statistics.rs` lines ~16-46. Port of Python `_is_uuid_format` (`smart_crusher.py:378-392`).

Format-only check (no version/variant bit validation). Hex chars may be upper or lower case.

Steps:
1. If `value.len() != 36` (byte length) → return `false`.
2. Split `value` on `'-'`. If the number of parts `!= 5` → return `false`.
3. Expected segment lengths array: `[8, 4, 4, 4, 12]`.
4. Zip parts with expected lengths; for each `(part, expected_len)`:
   - If `part.len() != expected_len` → return `false`.
   - For each char in `part`: if not `is_ascii_hexdigit()` → return `false`.
5. Return `true`.

Edge cases: empty string → `false` (length check). Wrong segment count (e.g. no dashes) → `false`.
Non-hex char anywhere → `false`.

---

### `pub fn calculate_string_entropy(s: &str) -> f64`
Source: `statistics.rs` lines ~57-91. Port of Python `_calculate_string_entropy`
(`smart_crusher.py:395-423`). Returns normalized Shannon entropy in `[0, 1]`.
High (>0.7) ⇒ random/ID-like; low (<0.3) ⇒ repetitive/predictable.

Steps (character-level; iterate Unicode scalar values via `chars()`):
1. `n = s.chars().count()`. If `n < 2` → return `0.0`.
2. Build frequency map `freq: HashMap<char, usize>` by counting each `char`.
3. `length = n as f64`.
4. `entropy = 0.0`. For each `count` in `freq.values()`:
   - `p = count as f64 / length`.
   - If `p > 0.0`: `entropy -= p * p.log2()`  (base-2 log).
5. Normalize: `max_entropy = (freq.len().min(n) as f64).log2()`.
   - Note: `min(len(freq), n)` mirrors Python `log2(min(len(freq), length))`.
6. If `max_entropy > 0.0` → return `entropy / max_entropy`; else return `0.0`.

Edge cases (matched to Python):
- Empty or single-char string → `0.0` (step 1).
- All-identical chars (e.g. `"aaaa"`): `freq.len() == 1`, `max_entropy = log2(1) = 0.0` → return `0.0`
  (degenerate-guard, avoids divide-by-zero).
- Two distinct chars 50/50 (`"ab"`): raw entropy `1.0`, max `log2(2) = 1.0` → `1.0`.

---

### `fn python_int_parse(s: &str) -> Option<i64>` (private helper)
Source: `statistics.rs` lines ~110-148. Mirrors CPython `int(v)` for plain integer literals so that
"is this string numeric?" agrees with Python. Rust's `str::parse::<i64>()` alone would diverge.

Accepted (matching CPython default-base `int()`):
- Leading/trailing ASCII whitespace (stripped).
- Leading sign `+` or `-`.
- PEP 515 underscore digit separators (`"3_000"` → `3000`).

Deliberately **NOT** supported (unreachable from the call site): base prefixes (`"0x10"`),
scientific notation, float strings (`"3.14"` → `None`).

Steps:
1. `trimmed = s.trim()`. If `trimmed.is_empty()` → `None`.
2. If `trimmed` contains `'_'`:
   - Let `starts_or_ends = first byte == '_' OR last byte == '_' OR contains "__"`.
   - If `starts_or_ends` → return `None` (Python rejects `_5`, `5_`, `3__000`).
   - Else `cleaned = trimmed.replace('_', "")`.
   - Else `cleaned = trimmed.to_string()`.
3. Return `cleaned.parse::<i64>().ok()`.

Examples (pinned by tests): `"5"`→5, `"-5"`→-5, `"+5"`→5, `"  5  "`→5, `"\t-3\n"`→-3,
`"3_000"`→3000, `"_5"`/`"5_"`/`"3__000"`→None, `"3.14"`→None, `"abc"`/`""`/`"   "`→None.

---

### `pub fn detect_sequential_pattern(values: &[Value], check_order: bool) -> bool`
Source: `statistics.rs` lines ~170-262. Port of Python `_detect_sequential_pattern`
(`smart_crusher.py:426-481`) **with BUG #2 FIXED** (see below). Detects whether numeric values form
a sequential pattern (IDs: 1,2,3,…).

Args:
- `values`: items to inspect.
- `check_order`: when true, additionally require ascending order in **original array order**
  (IDs ascend; scores typically descend).

Steps:
1. If `values.len() < 5` → return `false`.
2. Collect numerics into `nums: Vec<f64>`, tracking flag `had_non_string_numeric = false`:
   - `Value::Number(n)`: if `n.as_f64()` is `Some(f)` → push `f`; set `had_non_string_numeric = true`.
   - `Value::Bool(_)`: **skip entirely** — bools are NOT numeric (Python:
     `isinstance(v, int|float) and not isinstance(v, bool)`).
   - `Value::String(s)`: if `python_int_parse(s)` is `Some(p)` → push `p as f64`.
     **Do NOT set `had_non_string_numeric`** (this is the BUG #2 gate).
   - other → skip.
3. If `nums.len() < 5` → return `false`.
4. **BUG #2 fix gate:** if `!had_non_string_numeric` (every numeric value originated as a string) →
   return `false`. (Zero-padded codes like `["001",…,"100"]` are categorical, not sequential.)
5. Redundant guard: if `nums.len() < 2` → return `false`.
6. Sort a clone of `nums` ascending (`partial_cmp`, NaN → `Equal`).
   Compute pairwise diffs: `diffs[i] = sorted[i+1] - sorted[i]` over `windows(2)`.
   If `diffs.is_empty()` → return `false`.
7. `avg_diff = sum(diffs) / diffs.len()`. If `avg_diff` **not in** `0.5..=2.0` (inclusive) →
   return `false`. (Rust: `!(0.5..=2.0).contains(&avg_diff)`.)
8. `consistent_count = count of diffs d where (0.5..=2.0).contains(&d)` (inclusive both ends).
   `is_sequential = (consistent_count as f64 / diffs.len() as f64) > 0.8`  (**strictly greater**).
   If `!is_sequential` → return `false`.
9. If `check_order`:
   - `ascending_count = count over original (UNSORTED) nums windows(2) where w[0] <= w[1]`.
   - `n_pairs = nums.len() - 1`.
   - `is_ascending = (ascending_count as f64 / n_pairs as f64) > 0.7`  (**strictly greater**).
   - return `is_ascending`.
10. Else return `is_sequential` (== `true` at this point).

**Constants verbatim:** min items `5`; min numerics `5`; diff band `[0.5, 2.0]` inclusive;
consistency ratio threshold `0.8` (`>`); ascending ratio threshold `0.7` (`>`).

**Comparison-operator precision:** `avg_diff` band membership is **inclusive** (`0.5..=2.0`).
Consistency `> 0.8` and ascending `> 0.7` are **strict** (not `>=`).

#### Bug #2 (string-padding misclassification) — documented behavior to preserve
Python originally did `int("001") == 1`, losing zero-padding, so padded string IDs looked
sequential. Fix: track whether **any** value was a genuine (non-string) numeric. If **all** parsed
numerics came from strings → refuse `sequential`. Mixed numeric+string still detected because the
unambiguous numerics flip the gate. The flag fires on **ANY** non-string numeric, not a majority
(one real `Number` among string-encoded numerics is enough).

Edge cases (pinned by tests):
- `<5` items → `false`.
- All-`Bool` → `false` (bools excluded).
- All-unparseable strings → `false` (fails `nums.len() < 5`).
- Descending `10..=1` with `check_order=true` → `false`; with `check_order=false` → `true`.
- Floats with unit step (`1.0..=10.0`, or `1.5,2.5,…`) → `true`.
- Zero-padded strings only (`"001".."010"`) → `false` (bug-2 gate).
- Whitespace-padded numeric strings mixed with real ints (`1,"  2  ",3,…`) → `true`.

---

## FILE 2 — `stats_math.rs`

### Purpose
Numeric statistics matching Python's `statistics` module semantics used by `SmartAnalyzer`.
**Critical:** Python's `statistics` uses **sample** variance/stdev (**n−1** denominator), NOT
population. Mismatching the denominator silently shifts every variance-based decision. All helpers
also return `None` on non-finite (Inf/NaN) results, mirroring Python's
`try/except (OverflowError, ValueError)` path in `_analyze_field` that resets stats to `None`.

No external imports beyond `f64`.

---

### `pub fn mean(values: &[f64]) -> Option<f64>`
Source: `stats_math.rs` lines ~21-33.
1. If `values.is_empty()` → `None` (Python `statistics.mean([])` raises `StatisticsError`).
2. `sum = Σ values`; `m = sum / values.len() as f64`.
3. If `m.is_finite()` → `Some(m)`; else `None`.

Edge: overflow (e.g. four × `f64::MAX/2`) → sum `+Inf` → `m` non-finite → `None`.

---

### `pub fn sample_variance(values: &[f64]) -> Option<f64>`
Source: `stats_math.rs` lines ~39-52. **Sample variance, n−1 denominator.**
1. If `values.len() < 2` → `None` (Python raises for n<2).
2. `m = mean(values)?` (propagates `None`).
3. `sum_sq_diff = Σ (v - m)²` (use `.powi(2)`).
4. `var = sum_sq_diff / (values.len() - 1) as f64`.
5. If `var.is_finite()` → `Some(var)`; else `None`.

Pinned: `variance([1,2,3,4,5]) == 2.5` (population would be 2.0). Constant values → `0.0`.
`[1e200, -1e200]` overflows → `None`.

---

### `pub fn sample_stdev(values: &[f64]) -> Option<f64>`
Source: `stats_math.rs` lines ~57-59.
`sample_variance(values).map(f64::sqrt)`. Same n≥2 requirement; `None` propagates (incl. non-finite).
Pinned: `stdev([1,2,3,4,5]) == sqrt(2.5)`.

---

### `pub fn median(values: &[f64]) -> Option<f64>`
Source: `stats_math.rs` lines ~69-87. Python `statistics.median`.
1. If `values.is_empty()` → `None`.
2. Clone to `sorted`; sort with `f64::total_cmp` (deterministic NaN ordering).
3. `n = sorted.len()`.
4. If `n` even: `(sorted[n/2 - 1] + sorted[n/2]) / 2.0` (mean of two middles).
5. Else (odd): `sorted[n/2]`.

Pinned: `median([3,1,2]) == 2.0`; `median([1,2,3,4]) == 2.5`; single → that element.
Caller must pre-filter NaN/Inf if undesired.

---

### `pub fn format_g(x: f64) -> String`
Source: `stats_math.rs` lines ~107-141 (+ helper `normalize_scientific_exp` lines ~143-163).
Approximates Python `f"{x:.4g}"` (4 significant digits). Used for crusher strategy debug strings.

Rules:
- 4 significant digits.
- Scientific notation when decimal exponent `< -4` OR `>= 4`.
- Trailing zeros stripped (and dangling `.`).
- Scientific exponent padded to ≥2 digits with explicit sign (`1.234e+04`, `1e-05`).
- Round half-to-even (banker's) — both CPython and Rust `format!` use IEEE round-to-nearest-even.

Steps:
1. `x.is_nan()` → `"nan"`.
2. `x.is_infinite()` → `"inf"` if `x > 0.0` else `"-inf"`.
3. `x == 0.0` → `"0"` (covers `-0.0` too).
4. `abs = x.abs()`; `exp = abs.log10().floor() as i32`.
5. If `!(-4..4).contains(&exp)` (i.e. `exp < -4 || exp >= 4`): scientific path.
   - `s = format!("{:.3e}", x)` (3 digits after decimal in mantissa = 4 sig figs).
   - return `normalize_scientific_exp(&s)`.
6. Else fixed path:
   - `digits_after = (3 - exp).max(0) as usize`.
   - `s = format!("{:.*}", digits_after, x)`.
   - If `s.contains('.')` → `s.trim_end_matches('0').trim_end_matches('.')`; else `s`.

`normalize_scientific_exp(s)` helper:
1. Find `'e'`; if absent return `s` unchanged.
2. Split into `mantissa` and `rest` at the `e`; `exp_part = &rest[1..]`; `exp_num = exp_part.parse::<i32>().unwrap_or(0)`.
3. `mantissa_clean`: if contains `'.'`, trim trailing `0` then trailing `.`; else as-is.
4. `sign = if exp_num >= 0 {"+"} else {"-"}`.
5. return `format!("{}e{}{:02}", mantissa_clean, sign, exp_num.abs())`.

Pinned: `1.5`→`"1.5"`, `1.0`→`"1"`, `1234.0`→`"1234"`, `0.123456`→`"0.1235"`,
`12345.678`→`"1.235e+04"`, `0.00001234`→`"1.234e-05"`, `-12345.678`→`"-1.235e+04"`.

---

## FILE 3 — `outliers.rs`

### Purpose
Outlier detectors that mark items as "must preserve" during compression. Direct port of
`_detect_structural_outliers`, `_detect_rare_status_values`, and
`_detect_error_items_for_preservation` from `smart_crusher.py:606-748`.
(`_detect_items_by_learned_semantics` deferred — depends on un-ported TOIN `FieldSemantics`.)

Imports: `serde_json::Value`; `std::collections::{BTreeMap, BTreeSet, HashSet}`;
`super::error_keywords::ERROR_KEYWORDS`.

> **Numeric z-score / MAD / change-point: ABSENT.** This file contains no mean/std or median/MAD
> z-score test, no `variance_threshold`, and no sliding-window mean-shift / winnowing. The only
> outlier notions here are (a) **structural rare-field** (frequency threshold on field presence) and
> (b) **categorical Pareto rare-value**. Do not invent a numeric z-score path here.

---

### `pub fn detect_structural_outliers(items: &[Value]) -> Vec<usize>`
Source: `outliers.rs` lines ~73-126. Port of `_detect_structural_outliers`
(`smart_crusher.py:606-650`). Returns **deduplicated, ascending-sorted** indices.

Steps:
1. If `items.len() < 5` → return empty `Vec`.
2. Build `field_counts: BTreeMap<&str, usize>`: for each item that `.as_object()`, increment the
   count for every key present.
3. `n = items.len()`.
4. `common_fields: HashSet<String>` = keys where `count as f64 >= n as f64 * 0.8`
   (present in **≥80%** of items, `>=`).
5. `rare_fields: HashSet<&str>` = keys where `(count as f64) < n as f64 * 0.2`
   (present in **<20%** of items, strict `<`).
6. `outlier_set: BTreeSet<usize>` (BTreeSet pins ascending order; Python used `set()` → list, which
   is non-deterministic).
7. **Rare-field outliers:** for each `(i, item)`: if item is an object and **any** of its keys is in
   `rare_fields` → insert `i`.
8. **Rare-status outliers:** for each `idx` returned by
   `detect_rare_status_values(items, &common_fields)` → insert `idx`.
9. Return `outlier_set.into_iter().collect()` (ascending).

**Constants verbatim:** min items `5`; common-field threshold `0.8` (`>=`); rare-field threshold
`0.2` (`<`).

Edge cases: non-object items silently skipped (no panic). Field present in exactly 20% is NOT rare
(strict `<`).

---

### `pub fn detect_rare_status_values(items: &[Value], common_fields: &HashSet<String>) -> Vec<usize>`
Source: `outliers.rs` lines ~140-244. **BUG #3 fix** — Pareto-based replacement for Python's
cap-and-dominance approach. Returns indices in discovery order (caller dedupes via BTreeSet).

Algorithm per status-like field:
1. Iterate `common_fields` in **sorted order** (collect to `Vec<&String>`, `sort()`) for determinism.
2. For each `field_name`:
   a. Collect `values: Vec<&Value>` = for each item that is an object and `.get(field_name)` is
      `Some` → that value. (Mirrors Python comprehension; items lacking the field are skipped.)
   b. Stringify rule (`stringify(v)`), used for both cardinality and frequency so they stay
      internally consistent:
      - `Null` → unreachable (filtered before stringify).
      - `Bool(b)` → `b.to_string()` (`"true"`/`"false"`).
      - `Number(n)` → `n.to_string()`.
      - `String(s)` → `s.clone()`.
      - other (array/object) → `v.to_string()` (serde_json serialization).
   c. `unique_values: BTreeSet<String>` = stringify of each non-`Null` value. **Nulls excluded from
      cardinality** (Python `{str(v) for v in values if v is not None}`).
   d. **Cardinality gate (BUG #3 FIX):** if `unique_values.len()` **not in** `2..=50` (inclusive) →
      `continue`. (Was `2..=10` in Python; raised to 50 to catch large error-code domains. Above 50
      ⇒ almost certainly an ID/free-form column — skip.)
   e. Build `value_counts: BTreeMap<String, usize>`: for each value, key = `"__none__"` if `Null`
      else `stringify(v)`; increment. (Nulls DO count here as a distinct `"__none__"` bucket — they
      were only excluded from the cardinality set.) If `value_counts.is_empty()` → `continue`.
   f. `total = values.len()`.
   g. **Pareto check:** `sorted_counts` = `value_counts` entries sorted by **count descending**,
      tiebreak **key ascending** (`b.1.cmp(a.1).then(a.0.cmp(b.0))`).
      `threshold = (total as f64 * 0.8).ceil() as usize`  (≥80%, ceiling).
      Walk `sorted_counts` accumulating `cumulative += count` and inserting the value into
      `top_k_values: HashSet<String>`; **break** once `cumulative >= threshold` (`>=`).
   h. **Winnowing/uniformity rule:** if `top_k_values.len() > 5` → `continue`
      (distribution too uniform to call anything "rare"; **K ≤ 5** required, strict `>`).
   i. **Flag outliers:** for each `(i, item)`: if object and `.get(field_name)` is `Some(field_value)`:
      `item_value = "__none__"` if `Null` else `stringify(field_value)`. If
      `!top_k_values.contains(&item_value)` → `push i` to `outlier_indices`.
3. Return `outlier_indices` (discovery order; may contain duplicates across fields — caller dedupes).

**Constants verbatim:** cardinality range `2..=50` inclusive (bug-3: was `2..=10`); Pareto coverage
`0.8` with `.ceil()`; cumulative break `>=`; top-K cap `5` (`> 5` ⇒ skip).

**Pareto / rare-fraction summary:** "rare" = any value NOT among the smallest set of top-frequency
values whose cumulative count reaches ≥80% (ceil) of items, *provided that* top set has ≤5 members.

**High-cardinality / id-like skip:** cardinality > 50 → skip (id/free-form). Uniform distributions
(top-K can't reach 80% with ≤5 values) → skip. Cardinality < 2 (single value) → skip.

Worked cases (pinned by tests):
- 95×"ok" + 5 mixed errors → top-1 covers 95%, K=1 → 5 outliers.
- 60×"INFO" + 25×"WARN" + 15 distinct singleton errors (cardinality 17) → top-2 covers 85%, K=2 →
  15 outliers. (Old `≤10` cap would skip; bug-3 fixes this.)
- 50 distinct values ×1 each (cardinality 50) → top-K never reaches 80% with ≤5 → 0 outliers.
- 60 distinct values → cardinality 60 > 50 → skip → 0.
- 100×"ok" → cardinality 1 → skip → 0.
- 95×"ok" + 5×null → unique set `{"ok"}` (nulls excluded) → cardinality 1 → skip → 0.
- 90×"ok" + 5×"warn" + 5×null → unique set `{"ok","warn"}` cardinality 2 passes; counts ok=90,
  warn=5, __none__=5; top-1 "ok"=90% ≥80%, K=1; warn & null items flagged → 10 outliers.

---

### `pub fn detect_error_items_for_preservation(items: &[Value], item_strings: Option<&[String]>) -> Vec<usize>`
Source: `outliers.rs` lines ~258-296. Port of `_detect_error_items_for_preservation`
(`smart_crusher.py:711-748`). Ensures error items are NEVER dropped.

Args:
- `items`: items to scan.
- `item_strings`: optional pre-computed JSON serializations to avoid re-serializing. When provided,
  used per-index only while `i < item_strings.len()` (Python's `i < len(item_strings)` bounds-check
  mirrored); beyond that, fall back to fresh serialization.

Steps:
1. `error_indices: Vec<usize>` empty.
2. For each `(i, item)`:
   a. If `!item.is_object()` → `continue` (Python skips non-dicts).
   b. Compute `serialized: String` (lowercased haystack):
      - If `item_strings` is `Some(arr)` AND `i < arr.len()` → `arr[i].to_lowercase()`.
      - Else → `serde_json::to_string(item)`; on `Ok(s)` → `s.to_lowercase()`; on `Err` → `continue`.
   c. If **any** keyword in `ERROR_KEYWORDS` is a substring of `serialized`
      (`ERROR_KEYWORDS.iter().any(|kw| serialized.contains(kw))`) → `push i`.
3. Return `error_indices` (discovery order, ascending, no dedup needed — one push per item max).

Matching is **case-insensitive substring** (haystack lowercased; keywords are lowercase by
construction). Note this matches keywords appearing **anywhere** in the serialized JSON (keys or
values), e.g. a `"msg":"request failed"` matches via `"failed"`.

Edge cases (pinned): non-dict items skipped; cached string can force/override a hit; cache shorter
than items falls back to fresh serialize for the overflow indices.

---

### `ERROR_KEYWORDS` constant
Source: `error_keywords.rs`. Port of Python `error_detection.py:18-33`. **Exactly 12 keywords, all
lowercase.** Callers MUST lowercase the haystack before substring matching.

```
"error", "exception", "failed", "failure", "critical", "fatal",
"crash", "panic", "abort", "timeout", "denied", "rejected"
```

Invariants to preserve: `len() == 12`; all lowercase; exact membership set above (pinned in CI).

---

## Cross-cutting edge cases (summary)

| Condition | Behavior | Source |
|---|---|---|
| `detect_sequential_pattern`: n < 5 items OR < 5 numerics | `false` | statistics.rs |
| `detect_sequential_pattern`: all numerics from strings | `false` (bug #2 gate) | statistics.rs |
| Bools in sequential detection | excluded (never numeric) | statistics.rs |
| `mean`/`median` empty | `None` | stats_math.rs |
| `sample_variance`/`sample_stdev` n < 2 | `None` | stats_math.rs |
| Any non-finite (Inf/NaN) stat result | `None` | stats_math.rs |
| `detect_structural_outliers`: < 5 items | empty | outliers.rs |
| `detect_rare_status_values`: cardinality ∉ 2..=50 | field skipped | outliers.rs |
| `detect_rare_status_values`: top-K > 5 | field skipped (too uniform) | outliers.rs |
| nulls in status field | excluded from cardinality set; counted as `"__none__"` in freq | outliers.rs |
| non-object items everywhere | silently skipped | all |

---

## Suggested idiomatic Rust skeletons

```rust
// ----- statistics.rs -----
use serde_json::Value;
use std::collections::HashMap;

pub fn is_uuid_format(value: &str) -> bool { todo!() }

pub fn calculate_string_entropy(s: &str) -> f64 { todo!() }

/// CPython `int(v)` for plain integer literals: trims ASCII whitespace,
/// allows +/- sign and PEP 515 underscores; rejects floats/base prefixes.
fn python_int_parse(s: &str) -> Option<i64> { todo!() }

/// Sequential numeric pattern (IDs). `check_order` adds ascending-order
/// requirement over the ORIGINAL (unsorted) sequence. Includes bug #2 gate.
pub fn detect_sequential_pattern(values: &[Value], check_order: bool) -> bool { todo!() }

// ----- stats_math.rs -----
/// Mean; None on empty or non-finite.
pub fn mean(values: &[f64]) -> Option<f64> { todo!() }

/// SAMPLE variance, n-1 denominator. None for n<2 or non-finite.
pub fn sample_variance(values: &[f64]) -> Option<f64> { todo!() }

/// sqrt of sample_variance.
pub fn sample_stdev(values: &[f64]) -> Option<f64> { todo!() }

/// Median: middle (odd) or mean-of-two-middles (even). None on empty.
/// Sort with f64::total_cmp.
pub fn median(values: &[f64]) -> Option<f64> { todo!() }

/// Approximation of Python `f"{x:.4g}"`.
pub fn format_g(x: f64) -> String { todo!() }
fn normalize_scientific_exp(s: &str) -> String { todo!() }

// ----- outliers.rs -----
use std::collections::HashSet;

/// Structural outliers: rare-field (<20% presence) + rare-status (Pareto).
/// Returns deduped ascending indices. Empty if <5 items.
pub fn detect_structural_outliers(items: &[Value]) -> Vec<usize> { todo!() }

/// Pareto rare-value detection over common (>=80% present) status-like
/// fields. Cardinality 2..=50; top-K (cum >=80%, ceil) with K<=5; values
/// outside top-K are outliers. Discovery-order indices (caller dedupes).
pub fn detect_rare_status_values(
    items: &[Value],
    common_fields: &HashSet<String>,
) -> Vec<usize> { todo!() }

/// Error-keyword preservation: case-insensitive substring match of any
/// ERROR_KEYWORDS over the lowercased JSON serialization. Skips non-objects.
pub fn detect_error_items_for_preservation(
    items: &[Value],
    item_strings: Option<&[String]>,
) -> Vec<usize> { todo!() }

// ----- error_keywords.rs -----
pub const ERROR_KEYWORDS: &[&str] = &[
    "error", "exception", "failed", "failure", "critical", "fatal",
    "crash", "panic", "abort", "timeout", "denied", "rejected",
];
```

---

## Implementation notes / parity warnings
1. **Sample (n−1) variance is non-negotiable** — population variance breaks every downstream
   variance decision.
2. **Strict vs inclusive operators** are load-bearing:
   - sequential: diff band `0.5..=2.0` **inclusive**; ratios `> 0.8` / `> 0.7` **strict**.
   - structural: common `>= 0.8`, rare `< 0.2`.
   - Pareto: cardinality `2..=50` **inclusive**; cumulative break `>=`; top-K cap `> 5` skips.
3. **Determinism:** use `BTreeMap`/`BTreeSet` and explicit sorts where the source does, to keep
   fixture output stable (Python sets are unordered).
4. **Null handling in `detect_rare_status_values` is asymmetric:** excluded from the cardinality
   set but counted as `"__none__"` in the frequency map. Reproduce both.
5. **`format_g`** is fixture-locked at parity stage; match CPython `:.4g` including scientific
   threshold (`exp < -4 || exp >= 4`), 4 sig figs, trailing-zero strip, 2-digit signed exponent.
6. **What's NOT here:** z-score/modified-z, MAD, `variance_threshold`, sliding-window mean-shift /
   change-point / winnowing. If the target crate needs those, they come from a different headroom
   module not in this batch.
```
