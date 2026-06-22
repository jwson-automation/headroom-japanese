# Port Spec 3 — `anchors.rs`, `classifier.rs`, `field_detect.rs`

Faithful PORT SPEC extracted from headroom's Rust source for reimplementation in
`headroom_ja_core`. This is a spec, not Rust to drop in. All constants are
verbatim from the source.

**Source files (headroom `main` branch):**
- `crates/headroom-core/src/transforms/smart_crusher/anchors.rs`
- `crates/headroom-core/src/transforms/smart_crusher/classifier.rs`
- `crates/headroom-core/src/transforms/smart_crusher/field_detect.rs`

Citations are given as `anchors.rs:Lxx` etc. referencing the upstream file (line
numbers are approximate within ±a few lines — they cite the construct, not a
byte offset).

---

## Seam legend

| Marker | Meaning |
|--------|---------|
| 🟥 **SEAM (Python)** | NOT ported into the Rust crate. In our architecture Python supplies relevance/tokenization. Replicate the *interface*, not the regex logic. |
| 🟩 **PORT** | Structural logic we DO port into `headroom_ja_core`. |

---

# 1. `anchors.rs`

## Purpose
Legacy regex-based **query anchor extraction** + **item-matches-anchors** test.
Upstream this is a direct port of Python `extract_query_anchors` and
`item_matches_anchors` (`smart_crusher.py:99-168`). It decides which array items
"survive" compression by checking whether a serialized item contains any anchor
substring derived from the user's query text. Python marks both functions
DEPRECATED in favor of a `RelevanceScorer`, but the live SmartCrusher path still
calls them on every invocation, so upstream ports them faithfully.
(`anchors.rs:1-19`)

> **Naming note for this task.** The task brief calls for "first-N / last-N
> positional anchor logic." That **positional** selection does NOT live in
> `anchors.rs`. `anchors.rs` contains only the **query-anchor regex relevance
> logic**, which is entirely the Python seam. The positional first/last anchor
> selection lives in the field-stats / sampling layer (see §1.4 below and the
> note in §4). Everything regex-based in this file is the seam.

## 1.1 🟥 SEAM (Python): the five query-anchor regexes — DO NOT PORT
These are the "query anchors" (UUID, numeric-id, hostname, email, quoted-string).
In our port, **Python supplies relevance**, so **do NOT port the anchor regexes.**
Documented here only for fidelity / so the Python side stays in parity.

Pattern definitions, verbatim (`anchors.rs:29-66`):

| Anchor | Regex (verbatim) | Notes |
|--------|------------------|-------|
| UUID | `\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b` | match lowercased on insert |
| Numeric ID | `\b\d{4,}\b` | 4+ digit runs; inserted **unchanged** (digits, no case) |
| Hostname | `\b[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})?\b` | lowercased; false-positive filtered |
| Quoted string | `['"]([^'"]{1,50})['"]` | capture group 1; require `trim().len() >= 2`; lowercased |
| Email | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z\|a-z]{2,}\b` | lowercased. **NOTE the literal `\|` inside the char class — a Python typo (`[A-Z\|a-z]`) faithfully preserved; harmless because `\|` never appears in a TLD** (`anchors.rs:57-66`) |

Hostname false-positive blocklist (verbatim) (`anchors.rs:71`):
```
["e.g", "i.e", "etc."]
```
Any hostname match whose lowercased form is in this set is dropped.

**`extract_query_anchors(text) -> HashSet<String>` behavior** (`anchors.rs:78-126`):
1. Empty input → empty set (early return).
2. UUID matches → `.to_lowercase()`, insert.
3. Numeric-ID matches → inserted as-is (no transform).
4. Hostname matches → lowercased; skip if in blocklist.
5. Quoted-string matches → capture group 1; insert lowercased only if
   `inner.trim().len() >= 2`.
6. Email matches → lowercased, insert.
7. Returns a **set** (order not significant — Python returns `set[str]`).

🟥 **Do not port any of the above into the crate.** The Python relevance layer
emits the anchor set across the seam.

## 1.2 🟥 SEAM (Python): `python_repr` serialization — DO NOT PORT (parity helper)
`item_matches_anchors` compares `anchor in str(item).lower()`. Python's
`str(dict)` differs from JSON in three substring-affecting ways, so upstream
re-implements Python's `str()` via `python_repr` (`anchors.rs:128-210`):

| Aspect | Python `str(dict)` | `serde_json::to_string` |
|--------|--------------------|-------------------------|
| String quotes | single `'` | double `"` |
| Booleans / null | `True`, `False`, `None` | `true`, `false`, `null` |
| Spacing | `key: value`, `a, b` | `key:value`, `a,b` |

`write_python_repr` rules (verbatim semantics):
- `Null` → `None`; `Bool(true)` → `True`; `Bool(false)` → `False`.
- `Number` → `n.to_string()`.
- `String(s)` → always single-quoted: `'` + s + `'` (known parity gap: Python
  switches to double quotes if the string contains a `'`; this impl does not —
  pinned by test `python_repr_string_with_single_quote_drift`).
- `Array` → `[` items joined by `", "` `]`.
- `Object` → `{` `'key': value` pairs joined by `", "` `}`.
  - **Requires `serde_json` built with `preserve_order` (IndexMap)** so key
    insertion order matches Python dict order. Without it, ordering silently
    diverges. (`anchors.rs:184-194`)

## 1.3 🟥 SEAM (Python): `item_matches_anchors` — DO NOT PORT
(`anchors.rs:212-228`)
1. Empty anchor set → `false` (early return).
2. `item_str = python_repr(item).to_lowercase()`.
3. Return `anchors.iter().any(|a| item_str.contains(a))` (substring test).

## 1.4 🟩 PORT (positional first-N / last-N anchor selection)
**This file does NOT contain positional anchor logic.** The task's "first-N /
last-N positional anchors" are NOT in `anchors.rs`; that file is purely the
regex relevance seam. The positional first/last selection (keep the first K and
last K elements of an array verbatim) lives in the SmartCrusher sampling /
field-stats path (see Port Spec for `statistics` / `field_detect` callers — file
`smart_crusher.py` array-crush path). **Action item:** when you reach the array
crushing port, capture the exact first-N/last-N fractions there; this file
contributes nothing to that.

> If a later read of `smart_crusher.py` exposes explicit first/last counts (e.g.
> "keep first 3 + last 3"), record them in the array-crush port spec, not here.

## 1.5 Edge cases (anchors.rs)
- Empty query text → empty anchor set (no matching ever succeeds → no items
  forced to survive).
- Numeric ID requires **4+** digits — `123` is NOT an anchor; `1234` is.
- Quoted string requires `trim().len() >= 2` post-trim — `"x"` is NOT an anchor.
- Hostname blocklist removes `e.g`, `i.e`, `etc.` even though they match.
- Email regex `[A-Z\|a-z]` typo is harmless and preserved.
- `item_matches_anchors` with empty anchor set is always `false`.

---

# 2. `classifier.rs`

## Purpose
Classify a JSON array by the element types it contains, to drive the compression
strategy (dict arrays → statistical path, string arrays → string path, etc.).
Direct port of Python `_classify_array` (`smart_crusher.py:341-368`).
(`classifier.rs:1-4`) 🟩 **PORT in full** — this is pure structural logic, no
Python relevance/tokenizer involvement.

## 2.1 `ArrayType` enum (`classifier.rs:46-62`)
Seven variants. `as_str()` must return these **exact lowercase strings** (used in
strategy debug output, e.g. `"dict_array(100->10)"`) (`classifier.rs:67-77`):

| Variant | `as_str()` | Meaning |
|---------|-----------|---------|
| `DictArray` | `"dict_array"` | `[{...}, {...}]` pure objects |
| `StringArray` | `"string_array"` | pure strings |
| `NumberArray` | `"number_array"` | pure numbers, **excludes bools** |
| `BoolArray` | `"bool_array"` | pure bools |
| `NestedArray` | `"nested_array"` | pure arrays-of-arrays |
| `MixedArray` | `"mixed_array"` | heterogeneous, or any null present |
| `Empty` | `"empty"` | empty array |

## 2.2 Classification algorithm (`classify_array`) (`classifier.rs:85-145`)
- **Walk every element** (not a sample) — `is_*` is O(1) so a full walk is cheap
  and avoids missing a deep type transition.
- Empty slice → `Empty` (early return).
- Set six boolean flags by matching each element's `Value` variant:
  `has_bool, has_number, has_string, has_object, has_array, has_null`.
- Then apply **"pure-X" gates** — a type is returned only if its flag is set AND
  **all other flags are unset** (this is the exact discriminator: any second
  kind, including `null`, demotes to `MixedArray`):

| Returns | Condition (all other flags must be false) |
|---------|-------------------------------------------|
| `BoolArray` | `has_bool` && none else |
| `DictArray` | `has_object` && none else |
| `StringArray` | `has_string` && none else |
| `NumberArray` | `has_number` && none else |
| `NestedArray` | `has_array` && none else |
| `MixedArray` | (fallthrough) anything else, incl. any `null` |

**Order of gates (verbatim):** Bool → Dict → String → Number → Nested →
fallthrough Mixed. Because all gates are mutually exclusive ("pure" with all
others false), order does not affect outcome, but preserve it for fidelity.

## 2.3 Uniform vs heterogeneous / discriminator (the thresholds)
There is **no fractional threshold** here. The discriminator is **strict purity**:
the array is "uniform" (one of the five typed variants) iff exactly one type-flag
is set; otherwise it is "heterogeneous" → `MixedArray`. `null` counts as its own
kind, so a single null among objects yields `MixedArray`.

## 2.4 Python parity note: bool vs int (`classifier.rs:6-22`, tests 200-230)
- Python `bool` is an `int` subclass: `[True, False, 1]` has `types == {bool, int}`
  → demoted to `MixedArray`. `[True, False]` → `BoolArray`.
- Rust gets this "for free" because `serde_json::Value` has separate `Bool` and
  `Number` variants (no inheritance): `[true, false, 1]` sets both `has_bool` and
  `has_number` → fails both gates → `MixedArray`. Same outcome, different path.

## 2.5 Edge cases (classifier.rs)
- `[]` → `Empty`.
- `[{...}, null]` → `MixedArray` (null demotes dict purity).
- `[true, false, 1]` → `MixedArray` (bool+number).
- `[1, 2.5, 3]` → `NumberArray` (int+float both Number, no bool).
- Mixed dict+string → `MixedArray`.

---

# 3. `field_detect.rs`

## Purpose
Statistical detectors for **ID-like** and **score-like** fields, run *after*
per-field statistics are computed. They consume a `FieldStats` plus raw values
and decide whether a field is a meaningful ranking signal (score) or just a
unique identifier (ID) that should not drive compression. Direct ports of Python
`_detect_id_field_statistically` and `_detect_score_field_statistically`
(`smart_crusher.py:484-603`). (`field_detect.rs:1-12`) 🟩 **PORT in full** — pure
structural logic. (It depends on `statistics::{calculate_string_entropy,
detect_sequential_pattern, is_uuid_format}` and `types::FieldStats`, which are
ported separately.)

## 3.1 `FieldStats` fields used (from `super::types`, seen in tests ~615-640)
`name: String`, `field_type: String` (`"string"` | `"numeric"`),
`count`, `unique_count`, `unique_ratio: f64`, `is_constant`, `constant_value`,
`min_val: Option<f64>`, `max_val: Option<f64>`, `mean_val`, `variance`,
`change_points`, `avg_length`, `top_values`.

## 3.2 `detect_id_field_statistically(stats, values) -> (bool, f64)`
(`field_detect.rs:42-95`) Returns `(is_id, confidence)`, `confidence ∈ [0,1]`.

Rules, in evaluation order (first match wins / returns):
1. **Hard gate** (`field_detect.rs:44`): `if stats.unique_ratio < 0.9` → return
   `(false, 0.0)`.
2. **String branch** (`stats.field_type == "string"`):
   - Sample = **first 20** values (`values.iter().take(20)`), filtered to those
     that are strings (`as_str()`), preserving order (slice-then-filter, mirroring
     Python `values[:20]` then `isinstance str`).
   - If sample non-empty:
     - `uuid_count` = count where `is_uuid_format(s)`.
     - If `uuid_count / sample.len() > 0.8` → return `(true, 0.95)`.
     - `avg_entropy` = mean of `calculate_string_entropy(s)` over the sample.
       If `avg_entropy > 0.7 && unique_ratio > 0.95` → return `(true, 0.8)`.
3. **Numeric branch** (`stats.field_type == "numeric"`):
   - If `detect_sequential_pattern(values, /*check_order=*/true) && unique_ratio
     > 0.95` → return `(true, 0.9)`. (Passes the **full** values list, which may
     include non-numbers — Python parity.)
   - Else if both `min_val` and `max_val` present: `range = max - min`; if
     `range > 0.0 && unique_ratio > 0.95` → return `(true, 0.85)`.
4. **Catch-all** (`field_detect.rs:90`): if `unique_ratio > 0.98` → return
   `(true, 0.7)`.
5. Otherwise → `(false, 0.0)`.

**Constants verbatim:** uniqueness hard gate `0.9`; UUID-fraction `0.8` → conf
`0.95`; entropy `0.7` + uniqueness `0.95` → conf `0.8`; sequential + `0.95` →
conf `0.9`; range>0 + `0.95` → conf `0.85`; catch-all uniqueness `0.98` → conf
`0.7`. Sample size **20**.

## 3.3 `detect_score_field_statistically(stats, items) -> (bool, f64)`
(`field_detect.rs:97-185`) Returns `(is_score, confidence)`. `items` is the
list of original-array **dict** items (so values can be pulled in array order).

Rules:
1. If `stats.field_type != "numeric"` → `(false, 0.0)`.
2. If `min_val`/`max_val` not both present → `(false, 0.0)`.
3. **Range bucket** — `if/elif` chain, **first match wins** (`field_detect.rs:124-146`):

   | Condition (verbatim) | confidence += | bounded? |
   |----------------------|---------------|----------|
   | `(0.0..=1.0).contains(min) && (0.0..=1.0).contains(max)` | `+0.4` | yes |
   | `(0.0..=10.0).contains(min) && (0.0..=10.0).contains(max)` | `+0.3` | yes |
   | `(0.0..=100.0).contains(min) && (0.0..=100.0).contains(max)` | `+0.25` | yes |
   | `min >= -1.0 && max <= 1.0` | `+0.35` | yes |
   | else | — | **no → return `(false, 0.0)`** |

   ⚠️ **Parity subtlety** (`field_detect.rs:138-142`): the signed-similarity bucket
   is Python `elif -1 <= min_val and max_val <= 1` — note the chained `<=` binds
   only on the `max_val` side; `min_val` is checked separately. Pinned exactly as
   `min_val >= -1.0 && max_val <= 1.0`. Because it's the **4th** `elif`, the
   `[0,1]` / `[0,10]` / `[0,100]` buckets are tried first; a `[0,1]` range hits
   the first bucket (`+0.4`), never this one.

4. **Sequential rejection:** pull this field's values from the **first 50**
   items (`items.iter().take(50)`, dict-style `m.get(&stats.name)`, skipping
   items lacking the key). Clone to owned `Value`s. If
   `detect_sequential_pattern(sample, /*check_order=*/true)` → return
   `(false, 0.0)`. (IDs are sequential; scores are not.)
5. **Descending-sort bonus** (`field_detect.rs:165-178`): build `values_in_order`
   = field's values across **all** items, filtered to finite numbers
   (`as_f64()`, `is_finite()`), preserving order. If `len >= 5`:
   - `num_pairs = len - 1`
   - `descending_count` = count of adjacent pairs `w` where `w[0] >= w[1]`
     (`windows(2)`).
   - If `num_pairs > 0 && descending_count / num_pairs > 0.7` → `confidence += 0.3`.
6. **Float-fraction bonus** (`field_detect.rs:180-188`): take **first 20** of
   `values_in_order`. `float_count` = count where `v.is_finite() && v != v.trunc()`
   (has a fractional part). If `!first_20.is_empty() && float_count > first_20.len()
   * 0.3` → `confidence += 0.1`. (Strict `>`.)
7. **Result** (`field_detect.rs:190-192`): `is_score = confidence >= 0.4`;
   `bounded_confidence = confidence.min(0.95)`; return `(is_score, bounded_confidence)`.

**Constants verbatim:** range buckets `+0.4 / +0.3 / +0.25 / +0.35`; sequential
sample size **50**; descending window threshold `> 0.7` (needs `len >= 5`) →
`+0.3`; float-fraction first **20**, threshold `> len*0.3` (strict) → `+0.1`;
accept threshold `>= 0.4`; confidence cap `0.95`.

## 3.4 Edge cases (field_detect.rs)
- ID: `unique_ratio < 0.9` short-circuits to `(false, 0.0)` regardless of type.
- ID numeric catch-all: a constant numeric field (zero range) with
  `unique_ratio > 0.98` still returns `(true, 0.7)` via catch-all even though the
  range branch fails (test `id_field_high_uniqueness_alone_triggers_catchall`).
- Score: a `[0,1]` range plus descending sort = `0.4 + 0.3 = 0.7` (accepted).
- Score: bounded but **sequential** (e.g. `1..=10`) → rejected at step 4.
- Score: unbounded range `[0,1000]` → rejected at step 3 (no bucket).
- Score: `[0,100]` alone (`+0.25`) with no other bonus stays below `0.4` →
  rejected.
- Max achievable confidence with current rules is `0.4+0.3+0.1 = 0.8` (< cap);
  the `0.95` cap is defensive (test `score_field_confidence_capped_at_95`).
- Empty sample lists guard every fraction division (`!is_empty()` / `len >= 5` /
  `num_pairs > 0`).

---

# 4. Note on the requested "positional first-N / last-N anchors"

The task asked specifically for the **positional** first/last anchor selection
(exact fractions/counts → index mapping). **None of the three files supplied
contains it.** `anchors.rs` is exclusively the query-anchor **regex relevance
seam** (🟥 Python), and `classifier.rs` / `field_detect.rs` are type/field
detectors. The positional first-N/last-N "keep the head and tail verbatim"
sampling logic lives elsewhere in the SmartCrusher array-crush path
(`smart_crusher.py`, the `_crush_array` / sampling helpers). **Recommendation:**
fetch that module next and capture the exact head/tail counts (and any fraction)
in a dedicated array-crush port spec — do not infer them here.

---

# 5. Suggested idiomatic Rust fn stubs (PORT targets only)

These are stubs for the parts we WILL port (`classifier.rs`, `field_detect.rs`).
The regex anchor functions are the Python seam and are intentionally omitted.

```rust
// ---- classifier (PORT in full) ----

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ArrayType {
    DictArray,
    StringArray,
    NumberArray, // excludes bools
    BoolArray,
    NestedArray,
    MixedArray,  // heterogeneous, or any null present
    Empty,
}

impl ArrayType {
    pub fn as_str(self) -> &'static str {
        match self {
            ArrayType::DictArray => "dict_array",
            ArrayType::StringArray => "string_array",
            ArrayType::NumberArray => "number_array",
            ArrayType::BoolArray => "bool_array",
            ArrayType::NestedArray => "nested_array",
            ArrayType::MixedArray => "mixed_array",
            ArrayType::Empty => "empty",
        }
    }
}

/// Walk every element (not a sample). "Pure-X" gate: exactly one type-flag set.
/// Any second kind — including null — demotes to MixedArray.
pub fn classify_array(items: &[serde_json::Value]) -> ArrayType {
    todo!()
}

// ---- field_detect (PORT in full) ----
// Depends on ported helpers:
//   statistics::is_uuid_format(&str) -> bool
//   statistics::calculate_string_entropy(&str) -> f64
//   statistics::detect_sequential_pattern(&[Value], check_order: bool) -> bool
//   types::FieldStats

/// Returns (is_id, confidence ∈ [0,1]).
/// Gate unique_ratio < 0.9 → (false, 0.0). String/numeric branches and a
/// unique_ratio > 0.98 catch-all (0.7). First-match-wins.
pub fn detect_id_field_statistically(
    stats: &FieldStats,
    values: &[serde_json::Value],
) -> (bool, f64) {
    todo!()
}

/// Returns (is_score, confidence). Numeric + both min/max required.
/// Range bucket (+0.4/+0.3/+0.25/+0.35, first match), reject if unbounded;
/// reject if sequential (first 50 items); +0.3 descending bonus (len>=5,
/// >0.7 of pairs non-increasing); +0.1 float-fraction bonus (first 20,
/// >30% fractional). Accept if confidence >= 0.4; cap at 0.95.
pub fn detect_score_field_statistically(
    stats: &FieldStats,
    items: &[serde_json::Value],
) -> (bool, f64) {
    todo!()
}
```

**Not ported (Python seam) — interface reference only:**

```rust
// 🟥 SEAM (Python supplies relevance). DO NOT PORT the regexes/logic.
// pub fn extract_query_anchors(text: &str) -> HashSet<String>;
// pub fn item_matches_anchors(item: &Value, anchors: &HashSet<String>) -> bool;
// fn python_repr(value: &Value) -> String;  // Python str() parity helper
```
