# Port Spec 4 — Index-Selection Core (`orchestration.rs`, `crusher.rs`, `crushers.rs`, `planning.rs`, `constraints.rs`)

Faithful PORT SPEC extracted from headroom's Rust source for reimplementation in
`headroom_ja_core`. This is a spec, not Rust to drop in. All constants are
**verbatim** from the source. This is the **index-selection core**: the budget
computation, the strategy planners, and `prioritize_indices` (dedup → fill →
critical-first). Implement the formulas and counts here **exactly** — they are
load-bearing for output parity.

**Source files (headroom `main` branch, `crates/headroom-core/src/transforms/`):**
- `smart_crusher/orchestration.rs` — `prioritize_indices`, `deduplicate_indices_by_content`, `fill_remaining_slots`
- `smart_crusher/crusher.rs` — `crush` / `crush_array` / `execute_plan` entry points, gates, budget
- `smart_crusher/crushers.rs` — `compute_k_split` (first/last fractions budget), non-dict crushers
- `smart_crusher/planning.rs` — `create_plan` dispatcher + four `plan_*` planners
- `smart_crusher/constraints.rs` — `KeepErrorsConstraint`, `KeepStructuralOutliersConstraint`, default stack
- supporting: `smart_crusher/config.rs`, `smart_crusher/outliers.rs`, `smart_crusher/error_keywords.rs`, `anchor_selector.rs` (`compute_item_hash`), `adaptive_sizer.rs` (`compute_optimal_k`)

Citations: `orchestration.rs:Lxx` etc. reference the upstream file (line numbers
within ±a few lines — they cite the construct).

---

## Seam legend

| Marker | Meaning |
|--------|---------|
| 🟥 **SEAM (Python)** | NOT ported into the Rust crate. In our architecture **Python computes error-detection and query-relevance and passes them into Rust as input arrays** (`is_error: &[bool]`, `relevance: &[f32]`). Replicate the *consumption interface*, not headroom's keyword-scan / scorer logic. |
| 🟩 **PORT** | Structural logic we DO port verbatim. |

> **The seam in one line.** Wherever headroom calls
> `detect_error_items_for_preservation(...)` (keyword scan) or
> `scorer.score_batch(...)` (relevance), **we instead read a precomputed
> per-item array supplied by Python.** The *index* still enters the same critical
> set / keep set at the same point. Everything else (dedup, fill, first-N/last-N
> anchors, budget, prioritization) is **PORT**.

---

# 0. Config defaults (verbatim — `config.rs:106-137`)

These are consulted everywhere; any drift breaks parity. All values are the
Python `SmartCrusherConfig` defaults (`smart_crusher.py:934-957`).

| Field | Default | Used by |
|-------|---------|---------|
| `enabled` | `true` | master gate |
| `min_items_to_analyze` | `5` | gate: arrays smaller than this are not analyzed (`process_value`) |
| `min_tokens_to_crush` | `200` | gate: object-level crush only (`crush_object`); see §3.2 |
| `variance_threshold` | `2.0` | numeric-anomaly σ multiplier |
| `uniqueness_threshold` | `0.1` | near-constant field detection |
| `similarity_threshold` | `0.8` | string clustering |
| **`max_items_after_crush`** | **`15`** | the budget `max_k` (target output size) |
| `preserve_change_points` | `true` | smart_sample change-point window |
| `factor_out_constants` | `false` | execute_plan constant stripping |
| `include_summaries` | `false` | (no generated text) |
| `use_feedback_hints` | `true` | (feedback — out of scope) |
| `toin_confidence_threshold` | `0.5` | (TOIN — stubbed, out of scope) |
| **`dedup_identical_items`** | **`true`** | gate on dedup pass in `prioritize_indices` |
| **`first_fraction`** | **`0.3`** | `compute_k_split` first-budget fraction |
| **`last_fraction`** | **`0.15`** | `compute_k_split` last-budget fraction |
| `relevance_threshold` | `0.3` | 🟥 SEAM — threshold against `relevance[i]` |
| `lossless_min_savings_ratio` | `0.15` | lossless-vs-lossy dispatch (Rust-only knob) |
| `enable_ccr_marker` | `true` | CCR drop-marker gate (out of scope for index selection) |
| `compaction_*` | `0.8 / 0.6 / 6 / 2 / 8` | compaction heuristics (out of scope) |

For our crate, the index-selection-relevant subset is:
`min_items_to_analyze=5`, `max_items_after_crush=15`, `variance_threshold=2.0`,
`dedup_identical_items=true`, `first_fraction=0.3`, `last_fraction=0.15`,
`relevance_threshold=0.3`.

---

# 1. `orchestration.rs` — the heart of index selection 🟩 PORT

Three functions, all operating on `BTreeSet<usize>` (sorted, deterministic
ascending iteration). Item content hashes use `compute_item_hash` (§1.4) so
identical items collapse identically.

## 1.1 `deduplicate_indices_by_content` (`orchestration.rs:49-68`)

Collapse content-duplicate indices to their **lowest** representative.

```
fn deduplicate_indices_by_content(keep_indices: &BTreeSet<usize>, items: &[Value]) -> BTreeSet<usize>
```

Algorithm, verbatim:
1. If `keep_indices` is empty → return empty.
2. `seen: BTreeMap<String hash, usize idx>`.
3. Iterate `keep_indices` **in ascending order** (BTreeSet guarantees this):
   - If `idx >= items.len()` → **skip** (out-of-bounds tolerated).
   - `h = item_content_hash(items[idx], idx)` (§1.4).
   - `seen.entry(h).or_insert(idx)` — **first insertion wins**, i.e. the lowest
     index for each hash is kept; later duplicates dropped.
4. Return `seen.values()` collected into a `BTreeSet<usize>`.

Because iteration is ascending, the first index recorded for a hash is always the
lowest. Result is sorted by virtue of `BTreeSet`.

## 1.2 `fill_remaining_slots` (`orchestration.rs:82-136`)

Top `keep_indices` back up to `effective_max` with **diverse, content-unique**
items via stride sampling.

```
fn fill_remaining_slots(keep_indices: &BTreeSet<usize>, items: &[Value], n: usize, effective_max: usize) -> BTreeSet<usize>
```

`n` = `items.len()`. Algorithm, verbatim:

1. `remaining = effective_max.saturating_sub(keep_indices.len())`.
   If `remaining == 0` → return `keep_indices` unchanged.
2. Build `seen: HashSet<String>` = content hashes of currently-kept items
   **whose index `< n`** (`for &idx in keep_indices { if idx < n { seen.insert(hash) } }`).
3. `candidates: Vec<usize>` = **every index `0..n` NOT already in `keep_indices`**,
   in ascending order (`(0..n).filter(|i| !keep_indices.contains(i))`).
   If `candidates` empty → return `keep_indices` unchanged.
4. `result = keep_indices.clone()`.
5. **Stride formula (EXACT):**
   ```
   step = max(1, candidates.len() / (remaining + 1))
   ```
   Integer division. (`orchestration.rs:109`)
6. **Interleaved stride scan (EXACT — give start offsets and order):**
   ```
   added = 0
   'outer: for start_offset in 0..step {       // outer offsets 0,1,2,...,step-1
       if added >= remaining { break }
       let mut i = start_offset;
       while i < candidates.len() {
           if added >= remaining { break 'outer }
           idx = candidates[i]
           h   = item_content_hash(items[idx], idx)
           if !seen.contains(h) {
               result.insert(idx)
               seen.insert(h)
               added += 1
           }
           i += step                            // inner walks start_offset, +step, +step, ...
       }
   }
   ```
   The outer loop's `start_offset` ∈ `[0, step)`; the inner loop walks
   `start_offset, start_offset+step, start_offset+2·step, …`. Across all outer
   iterations every candidate is visited **exactly once**, in the interleaved
   order: `0, step, 2·step, …, 1, 1+step, …, 2, …`. Content duplicates are
   skipped (hash already in `seen`). Stop as soon as `added == remaining`.
7. Return `result`.

## 1.3 `prioritize_indices` — the top-level prioritizer (`orchestration.rs:152-230`)

```
fn prioritize_indices(
    config: &SmartCrusherConfig,
    keep_indices: &BTreeSet<usize>,
    items: &[Value],
    n: usize,                       // == items.len()
    analysis: Option<&ArrayAnalysis>,
    effective_max: usize,
) -> BTreeSet<usize>
```

Pipeline, verbatim:

**Step 1 — Dedup pass** (`L160-165`):
```
current = if config.dedup_identical_items { deduplicate_indices_by_content(keep_indices, items) }
          else { keep_indices.clone() }
```

**Step 2 — Fill pass** (`L167-170`):
```
if current.len() < effective_max && current.len() < n {
    current = fill_remaining_slots(&current, items, n, effective_max)
}
```

**Step 3 — Under-budget early return** (`L172-174`):
```
if current.len() <= effective_max { return current }   // ≤, not <
```

**Step 4 — Over-budget critical-first path** (`L176-229`). Reached only when
`current.len() > effective_max`. Build a fresh `prioritized: BTreeSet<usize>`:

4a. **Critical set (union — non-negotiable "quality guarantee"):**
   - `error_indices` 🟥 **SEAM**: upstream calls
     `detect_error_items_for_preservation(items, None)` (`L179-181`).
     **In our port: receive as input array** — `error_indices = { i | is_error[i] }`.
   - `outlier_indices` 🟩 PORT: `detect_structural_outliers(items)` (`L184`).
     See §6.1 — rare-field + rare-status detection. (Structural, statistical; not
     a Python seam unless you choose to move it; faithfully it is PORT.)
   - `anomaly_indices` 🟩 PORT: `numeric_anomaly_indices(config, items, analysis)`
     (`L187`, §1.5).
   - `learned_indices` = **empty** `BTreeSet` (TOIN not ported) (`L190`).
   ```
   prioritized.extend(error_indices)
   prioritized.extend(outlier_indices)
   prioritized.extend(anomaly_indices)
   prioritized.extend(learned_indices)   // empty
   ```

4b. **First-3 / last-2 positional anchors, only if room** (`L199-214`).
   **EXACT counts: first 3, last 2.**
   ```
   remaining = effective_max.saturating_sub(prioritized.len())
   if remaining > 0 {
       // FIRST 3 (indices 0,1,2 bounded by n)
       for i in 0 .. min(3, n) {
           if !prioritized.contains(i) && remaining > 0 {
               prioritized.insert(i); remaining -= 1;
           }
       }
       // LAST 2 (indices n-2, n-1)
       last_start = n.saturating_sub(2)
       for i in last_start .. n {
           if !prioritized.contains(i) && remaining > 0 {
               prioritized.insert(i); remaining -= 1;
           }
       }
   }
   ```

4c. **Fill with other kept indices, ascending** (`L217-227`):
   ```
   if remaining > 0 {
       others = (current \ prioritized) as Vec, sorted ascending
       for i in others {
           if remaining == 0 { break }
           prioritized.insert(i); remaining -= 1;
       }
   }
   ```

4d. **Return `prioritized`.**

> **⚠️ Quality-guarantee overshoot (must mirror).** `prioritized` can exceed
> `effective_max` when the **critical set alone (4a) is already larger than the
> budget** — `remaining` underflows to 0 via `saturating_sub`, steps 4b/4c add
> nothing, but 4a already overshot. This is **documented, intentional behavior**:
> critical items (errors / outliers / anomalies) are *never* dropped to fit
> budget. Do NOT clamp. (`orchestration.rs:14, 148-151, 199`)

## 1.4 Content hash — `item_content_hash` + `compute_item_hash` (byte-exact)

This is the dedup fingerprint. Must match Python **byte-for-byte** or dedup
diverges. (`orchestration.rs:292-310`, `anchor_selector.rs:350-525`)

**`item_content_hash(item, idx)`** (`orchestration.rs:292-310`):
- If `item.is_object() || item.is_array()` → return `compute_item_hash(item)`.
- Else (scalar) compute the hash over a Python-`str()`-style content string:
  - `Value::String(s)` → `s`
  - `Value::Number(n)` → `n.to_string()`
  - `Value::Bool(b)` → `b.to_string()` (`"true"` / `"false"`)
  - `Value::Null` → `"None"` (Python `str(None)`)
  - else → `format!("__idx_{idx}__")` (fallback)
  - then `format!("{:x}", Md5::digest(content_bytes))[..16]` (first 16 hex chars).

**`compute_item_hash(item)`** (`anchor_selector.rs:350-355`):
```
content = python_json_dumps_sort_keys(item)         // see below
digest  = MD5(content.as_bytes())
hash    = lowercase_hex(digest)[..16]               // FIRST 16 HEX CHARS
```
Equivalent to Python `md5(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()[:16]`.

**`python_json_dumps_sort_keys`** = Python `json.dumps(value, sort_keys=True)`
**byte-exact** (`anchor_selector.rs:380-525`). Format flags: `sort_keys=true`,
`compact=false`, `ensure_ascii=true`. Rules:
- Separators **with spaces**: item sep `", "`, key/value sep `": "` (Python
  default, NOT compact).
- Object keys **sorted alphabetically** (byte/codepoint sort of the key strings).
- `null` / `true` / `false` literals; numbers via Rust `n.to_string()` (matches
  serde for finite f64; NaN/Inf impossible from parsed JSON).
- Strings: standard escapes `\"` `\\` `\b`(0x08) `\t`(0x09) `\n`(0x0A)
  `\f`(0x0C) `\r`(0x0D); other control chars `< 0x20` → `\u00xx` (4 lowercase
  hex); ASCII `0x20..=0x7E` literal; **non-ASCII → `\uXXXX`** (4 lowercase hex),
  with **surrogate pairs** for codepoints `> 0xFFFF`
  (`hi = 0xD800 + (cp-0x10000 >> 10)`, `lo = 0xDC00 + (cp-0x10000 & 0x3FF)`).

Reference values (pin these in tests): `md5(json.dumps({"a":1,"b":2},sort_keys=True))[:16]`,
and `compute_item_hash({"k":"café"}) == "6761da28ed7eb489"`
(`anchor_selector.rs:932-941`). `{"b":2,"a":1}` and `{"a":1,"b":2}` hash equal.

> **Note for our crate:** the hash is **MD5**, truncated to the **first 16 hex
> characters**, over the **sorted-keys spaced-separator ascii-escaped JSON**. We
> already pinned `compute_item_hash` in Port Spec 1 — reuse it here unchanged.

## 1.5 `numeric_anomaly_indices` (`orchestration.rs:234-282`) 🟩 PORT

Items > `variance_threshold` σ from a numeric field's mean. Mirrors the planners'
`for_each_anomaly` (§5.5). Algorithm:
```
if analysis is None → empty
if analysis.field_stats empty → empty
for (field_name, stats) in analysis.field_stats:
    require stats.field_type == "numeric" && stats.mean_val.is_some() && stats.variance > 0.0
    let mean = stats.mean_val, var = stats.variance
    if var <= 0.0 { continue }
    std = sqrt(var); if std <= 0.0 { continue }
    threshold = config.variance_threshold * std        // 2.0 * std by default
    for (i, item) in items.enumerate():
        let obj = item.as_object()?; let v = obj.get(field_name)?
        if let Some(num) = v.as_f64():
            if !num.is_nan() && (num - mean).abs() > threshold {   // strict >
                anomalies.insert(i)
            }
```

---

# 2. `crusher.rs` — entry points, gates, budget 🟩 PORT (consumes seam arrays)

## 2.1 Top-level `crush` (`crusher.rs:338-372`)
`crush(content, query, bias) -> CrushResult`. Parses JSON, recursively processes
(`process_value`), re-serializes via `python_safe_json_dumps` (compact `","`/`":"`,
`ensure_ascii=false`, insertion-order keys). Non-JSON content passes through
unchanged. Index selection is downstream — this layer is plumbing.

## 2.2 `process_value` dispatch (`crusher.rs:411-546`)
- `MAX_PROCESS_DEPTH = 50`; beyond → return value as-is.
- For an array of length `n`: **only analyzed when `n >= min_items_to_analyze`
  (default 5)** (`L427`). Otherwise recurse into items (no crush).
- Array classified (`classify_array`): a **DictArray** routes to `crush_array`
  (§2.4 — the index-selection path). String/Number/Mixed arrays route to their own
  crushers (§3). Below threshold → recurse.

## 2.3 `execute_plan` — kept indices → output (`crusher.rs:288-320`) 🟩 PORT
**This is how the selected indices become output rows.**
```
indices = plan.keep_indices.clone()
indices.sort_unstable()                                  // ASCENDING
kept = indices.into_iter()
              .filter(|idx| idx < items.len())           // drop OOB
              .map(|idx| items[idx].clone())
              .collect()
// (optional, default OFF) factor_out_constants:
//   if config.factor_out_constants && !constant_fields.empty() && kept.len() >= 2:
//      strip each constant field where map.get(key) == Some(constant)
//      if anything stripped, PREPEND a sentinel {"_constant_fields": {...}}
return kept
```
For our crate (`factor_out_constants` default false), output = original items at
the kept indices, **in ascending index order**, OOB filtered.

## 2.4 `crush_array` — gates + budget + plan + execute (`crusher.rs:628-760`)

The full lossy index-selection path. Order, verbatim:

1. **Serialize once:** `item_strings[i] = serde_json::to_string(items[i])`
   (used for adaptive sizing and — upstream — relevance/error scans;
   in our port these become inputs, see §SEAM).
2. **Budget:** `max_k = (max_items_after_crush > 0) ? Some(max_items_after_crush) : None`;
   `adaptive_k = compute_optimal_k(item_str_refs, bias, min_k=3, max_k)` (§2.5).
3. **Gate A — already small enough** (`L644`):
   `if items.len() <= adaptive_k` → **passthrough** (`strategy="none:adaptive_at_limit"`),
   return all items, nothing dropped.
4. *(Lossless compaction attempt — out of scope for index selection; only fires
   when a compaction stage is configured and savings ≥ `lossless_min_savings_ratio`.)*
5. `effective_max_items = adaptive_k`. `analysis = analyzer.analyze_array(items)`.
6. **Gate B — Skip strategy** (`L697`):
   `if analysis.recommended_strategy == Skip` → passthrough with
   `strategy="skip:<reason>"`. (Crushability gate — not safe to crush.)
7. **Plan:** `plan = planner.create_plan(analysis, items, query_context,
   preserve_fields=None, Some(effective_max_items), Some(item_strings))` (§4).
8. **Execute:** `result = execute_plan(plan, items)` (§2.3).
9. `dropped_count = items.len() - result.len()`; if `> 0 && enable_ccr_marker`,
   emit CCR drop marker (out of scope). `strategy = analysis.recommended_strategy.as_str()`.

> **Where the budget comes from:** `effective_max` passed into `prioritize_indices`
> is **`adaptive_k`** (`compute_optimal_k` output), NOT the raw
> `max_items_after_crush`. `max_items_after_crush=15` is only the **upper clamp**
> (`max_k`) fed into `compute_optimal_k`.

## 2.5 `compute_optimal_k` — adaptive budget (`adaptive_sizer.rs:54-104`) 🟩 PORT

```
fn compute_optimal_k(items: &[&str], bias: f64, min_k: usize=3, max_k: Option<usize>) -> usize
```
Verbatim:
```
n = items.len(); effective_max = max_k.unwrap_or(n)
if n <= 8 { return n }                                    // Tier 1 fast path
unique = count_unique_simhash(items, 3)
if unique <= 3 { return min(max(min_k, unique), effective_max) }   // near-total redundancy
curve = compute_unique_bigram_curve(items)                // cumulative unique word-bigrams
knee  = find_knee(curve)                                   // Kneedle, returns 1-indexed
diversity_ratio = unique as f64 / n as f64
knee = match knee {
    None              => Some(max(min_k, (n * (0.3 + 0.7*diversity_ratio)) as usize)),
    Some(k) if diversity_ratio > 0.7
                      => Some(max(k, max(min_k, (n*(0.3+0.7*diversity_ratio)) as usize))),
    some              => some,
}.unwrap_or(min_k)
k = max(min_k, (knee as f64 * bias) as usize)             // bias multiplier, truncating
k = min(k, effective_max)
k = validate_with_zlib(items, k, effective_max, 0.15)     // Tier-3 zlib-ratio validation
return max(min_k, min(k, effective_max))                  // final clamp, floor min_k=3
```
Helpers (`find_knee`, `compute_unique_bigram_curve`, `count_unique_simhash`,
`validate_with_zlib`) are detailed in the adaptive-sizer source; `find_knee`
returns `knee_idx+1` (1-indexed keep count), `None` if `max_diff < 0.05`, and
literal `1` for a flat curve.

---

# 3. `crushers.rs` — budget split + non-dict crushers

## 3.1 `compute_k_split` — first/last fractions (`crushers.rs:76-100`) 🟩 PORT
**This is where `first_fraction` / `last_fraction` are applied** (for the
string/number/mixed crushers; the dict-array path uses `prioritize_indices`
instead). Returns `(k_total, k_first, k_last, k_importance)`:
```
max_k   = (max_items_after_crush > 0) ? Some(max_items_after_crush) : None
k_total = compute_optimal_k(items, bias, 3, max_k)
k_first_raw = max(1, round_ties_even(k_total * first_fraction) as usize)   // 0.3
k_last_raw  = max(1, round_ties_even(k_total * last_fraction)  as usize)   // 0.15
// BUG #4 FIX (Rust ahead of Python): clamp so k_first + k_last <= k_total
k_first = min(k_first_raw, k_total)
k_last  = min(k_last_raw, k_total.saturating_sub(k_first))
k_importance = k_total.saturating_sub(k_first + k_last)
```
`round_ties_even` = banker's rounding (Python `round()` / Rust
`f64::round_ties_even`). **Note the upstream deviation:** Python overshoots for
`k_total <= 1`; the Rust clamp is one-sided-correct. For `k_total >= 2`
(the common case) they agree.

## 3.2 Non-dict crushers (summary)
- `crush_string_array` (`L113`): passthrough if `n <= 8`; else `compute_k_split`,
  always keep error-keyword + length-anomaly strings, boundary first-K_first /
  last-K_last, stride-fill diverse uniques, output in original order.
- `crush_number_array`: percentile-based (carries upstream BUG #1 in the debug
  string only — item selection unaffected).
- `crush_object` (`L388`): per-key token estimate
  `tokens = val_str.len()/4 + key.len()/4 + 2`; **`if total_tokens <
  min_tokens_to_crush (200) { passthrough }`** — this is the `min_tokens` gate.
  Keys with `tokens <= 12` (`small_threshold//4`, small_threshold=50) treated as
  trivially-kept.

These are secondary to the dict-array index-selection path that our crate targets;
port if/when string/number/object compression is needed.

---

# 4. `planning.rs` — strategy dispatch + planners 🟩 PORT (consumes seam arrays)

`SmartCrusherPlanner` holds `config`, `anchor_selector`, `scorer` (🟥 seam),
`analyzer`, `constraints: &[Box<dyn Constraint>]`.

## 4.1 `create_plan` dispatcher (`planning.rs:97-164`)
```
max_items = effective_max_items.unwrap_or(config.max_items_after_crush)
plan.strategy = analysis.recommended_strategy
plan.constant_fields = factor_out_constants ? analysis.constant_fields : {}
if recommended_strategy == Skip { plan.keep_indices = 0..items.len(); return }   // keep all
match recommended_strategy {
    TimeSeries    => plan_time_series(...),
    ClusterSample => plan_cluster_sample(...),
    TopN          => plan_top_n(...),
    _ (SmartSample/None/...) => plan_smart_sample(...),     // default/fallback
}
```

## 4.2 Strategy → anchor pattern (`planning.rs:545-553`)
`TimeSeries→TimeSeries`, `TopN→SearchResults`, `ClusterSample→Logs`, else→`Generic`.

## 4.3 `apply_constraints` (`planning.rs:85-94`)
Unions every `Constraint::must_keep(items, item_strings)` into `keep`. The OSS
default stack (§7) is `[KeepErrorsConstraint, KeepStructuralOutliersConstraint]`.
🟥 **SEAM:** `KeepErrorsConstraint` is the keyword scan → **replace with
`is_error` input array**: instead of `apply_constraints` adding error items, add
`{ i | is_error[i] }`. `KeepStructuralOutliersConstraint` (rare fields / rare
status) is 🟩 PORT (§6.1).

## 4.4 `plan_smart_sample` — default planner (`planning.rs:169-229`)
```
keep: BTreeSet<usize> = {}
1. anchors:  keep += anchor_selector.select_anchors(items, max_items, Generic, query_or_none)
2. constraints: apply_constraints(items, item_strings, keep)
     // = error items (🟥 seam: is_error[]) ∪ structural outliers (🟩 port)
3. numeric anomalies: for (name,stats) in field_stats: for_each_anomaly(...) (§5.5)
4. change points (if preserve_change_points): for each stats.change_points cp,
     for offset in -1..=1: keep idx=cp+offset if in [0,n)        // window ±1
5/6. query signals: apply_query_signals(items, query, item_strings, keep, keep_existing_only=false)
     // 🟥 SEAM: anchor match + relevance scoring → replace with relevance[] (see §5.4)
7. preserve_fields: apply_preserve_field_matches(...)  // TOIN — None, no-op
FINAL: keep = prioritize_indices(config, keep, items, n, Some(analysis), max_items)
plan.keep_indices = keep ascending
```

## 4.5 `plan_top_n` (`planning.rs:234-338`)
```
score_field = field with max detect_score_field_statistically confidence; else fall back to plan_smart_sample
scored = [(i, item[score_field] as f64 or 0.0)]; sort by score DESCENDING (NaN→Equal)
top_count = max_items.saturating_sub(3)                 // EXACT: reserve 3
keep += first top_count indices of scored
apply_constraints(...)                                  // errors(🟥) ∪ outliers(🟩)
if query non-empty: keep += items matching query anchors (additive)   // 🟥 seam
if query non-empty:                                                   // 🟥 seam
    high_threshold = max(relevance_threshold * 2.0, 0.5)
    max_relevance_adds = 3                              // EXACT cap
    add up to 3 items with relevance >= high_threshold (not already kept)
apply_preserve_field_matches(...)                       // no-op
// NOTE: plan_top_n does NOT call prioritize_indices — keep is final
plan.keep_count = keep.len(); plan.keep_indices = keep ascending
```
> **Seam note for top_n:** the relevance-add path uses a *raised* threshold
> `max(relevance_threshold*2, 0.5)` and a hard cap of **3** additions. In our
> port, replace the scorer with `relevance[i]` and apply the same threshold/cap.

## 4.6 `plan_cluster_sample` (`planning.rs:343-414`)
```
keep += select_anchors(items, max_items, Logs, query_or_none)
apply_constraints(...)
message_field = string field with max unique_ratio AND unique_ratio > 0.3
if message_field:
    plan.cluster_field = message_field
    cluster key = lowercase_hex(MD5(first 50 chars of message))[..8]
    for each cluster: keep first 2 members (take(2))   // EXACT: 2 reps/cluster
apply_query_signals(..., keep_existing_only=false)     // 🟥 seam → relevance[]
apply_preserve_field_matches(...)                       // no-op
FINAL: prioritize_indices(config, keep, items, n, Some(analysis), max_items)
```

## 4.7 `plan_time_series` (`planning.rs:419-466`)
```
keep += select_anchors(items, max_items, TimeSeries, query_or_none)
// change points window ±2 (WIDER than smart_sample's ±1):
for each stats.change_points cp: for offset in -2..=2: keep cp+offset if in [0,n)
apply_constraints(...)
apply_query_signals(..., keep_existing_only=false)     // 🟥 seam → relevance[]
apply_preserve_field_matches(...)                       // no-op
FINAL: prioritize_indices(config, keep, items, n, Some(analysis), max_items)
```

---

# 5. Shared planner helpers

## 5.1 `query_or_none` (`planning.rs:594-600`): `""` → `None`, else `Some(q)`.

## 5.4 `apply_query_signals` (`planning.rs:474-518`) 🟥 SEAM
Two sub-signals, both gated on non-empty query:
1. **Anchor match** (deterministic): `item_matches_anchors(item, extract_query_anchors(query))`.
2. **Relevance score** (probabilistic): `scores = scorer.score_batch(strs, query)`;
   add `i` where `sc.score >= config.relevance_threshold` (0.3).

`keep_existing_only`: when true, skip indices already in `keep` (top_n's additive
mode); when false (all planners here), add all matches.

> **In our port:** discard the regex anchors + scorer. **Receive `relevance: &[f32]`
> from Python** and add `{ i | relevance[i] >= relevance_threshold }` (default 0.3)
> at this exact point. The Python side is responsible for whatever combination of
> anchor + relevance it wants; Rust just thresholds the array.

## 5.5 `for_each_anomaly` (`planning.rs:602-634`) 🟩 PORT
Identical math to §1.5 but per-field-call form:
```
if stats.field_type != "numeric" { return }
let (mean, var) = (stats.mean_val?, stats.variance?); if var <= 0.0 { return }
std = sqrt(var); if std <= 0.0 { return }
threshold = variance_threshold * std
for (i,item): if let Some(num)=item[field_name].as_f64():
    if !num.is_nan() && (num-mean).abs() > threshold { keep.insert(i) }
```

## 5.6 `apply_preserve_field_matches` / `item_has_preserve_field_match` (TOIN)
Out of scope — callers always pass `preserve_fields = None`, so it's a no-op.
(Semantics retained upstream at `planning.rs:520-592` for the future TOIN surface:
SHA256[:8] field-name hash match + case-insensitive bidirectional containment.)

---

# 6. `outliers.rs` — structural outliers (critical-set member) 🟩 PORT

## 6.1 `detect_structural_outliers` (`outliers.rs:61-109`)
```
if items.len() < 5 { return [] }                         // matches min_items_to_analyze
field_counts = count of each key across all object items
n = items.len()
common_fields = { key | count >= n * 0.8 }               // 80% threshold
rare_fields   = { key | count <  n * 0.2 }               // 20% threshold
outliers (BTreeSet, ascending):
  1. rare-field: any item whose object has a key in rare_fields
  2. rare-status: detect_rare_status_values(items, common_fields)  (§6.2)
return outliers ascending
```

## 6.2 `detect_rare_status_values` (`outliers.rs:122-235`)
For each `common_field` (sorted for determinism):
```
values = field values across object items that have the field
unique_values = { stringify(v) | v not null }            // scalars natural, nested via serde
if !(2..=50).contains(unique_values.len()) { continue }  // cardinality 2..=50 (BUG#3-fixed)
value_counts: map key->count, null → "__none__"
total = values.len()
sorted_counts = value_counts sorted by count DESC, tiebreak key ASC
threshold = ceil(total * 0.8)
top_k = smallest prefix of sorted_counts whose cumulative count >= threshold
if top_k.len() > 5 { continue }                          // distribution too uniform
for each item: if its field value (or "__none__") NOT in top_k → outlier index
```
`stringify`: bool→`"true"/"false"`, number→`n.to_string()`, string→`s`,
nested→serde JSON.

## 6.3 `detect_error_items_for_preservation` (`outliers.rs:250-283`) 🟥 SEAM — DO NOT PORT
Upstream: for each **object** item, lowercase its JSON serialization (use cached
`item_strings[i]` if provided, else `to_string`), flag if it `contains` ANY
`ERROR_KEYWORDS`. **We replace this entirely with the Python-supplied
`is_error: &[bool]`.** Error keyword list, verbatim, **12 entries**
(`error_keywords.rs:17-30`): `error, exception, failed, failure, critical, fatal,
crash, panic, abort, timeout, denied, rejected`. (Documented for Python-side
parity only; not implemented in Rust.)

---

# 7. `constraints.rs` — default must-keep stack 🟩 PORT (errors via seam)

`Constraint` trait: `name() -> &str`, `must_keep(items, item_strings: Option<&[String]>) -> Vec<usize>`.

- `KeepErrorsConstraint` (name `"keep_errors"`): wraps
  `detect_error_items_for_preservation` → 🟥 SEAM (replace with `is_error[]`).
- `KeepStructuralOutliersConstraint` (name `"keep_structural_outliers"`): wraps
  `detect_structural_outliers` → 🟩 PORT (§6.1).
- `default_oss_constraints()` returns the stack **in fixed order**
  `[KeepErrors, KeepStructuralOutliers]` (`constraints.rs:89-94`). Order doesn't
  affect output (union) but is fixed for determinism.

---

# 8. Suggested Rust skeleton for `headroom_ja_core`

The crate's seam-aware signature. `is_error` and `relevance` are **precomputed by
Python**, one entry per item; `is_error.len() == relevance.len() == items.len()`.

```rust
/// Index-selection core. Python supplies `is_error` (error detection) and
/// `relevance` (query relevance) per item; Rust does dedup, fill, anomaly,
/// outlier, positional anchors, and prioritization.
pub fn crush_indices(
    items: &[Value],
    is_error: &[bool],   // SEAM: Python error detection, per item
    relevance: &[f32],   // SEAM: Python query relevance, per item
    cfg: &Config,        // {min_items_to_analyze, max_items_after_crush, variance_threshold,
                         //  dedup_identical_items, first_fraction, last_fraction,
                         //  relevance_threshold, bias, ...}
) -> (Vec<usize> /* keep, ascending */, Vec<usize> /* dropped, ascending */) {
    let n = items.len();

    // GATE A: too small to analyze.
    if n < cfg.min_items_to_analyze {
        return ((0..n).collect(), Vec::new());
    }

    // BUDGET: adaptive_k = compute_optimal_k(item_strings, bias, 3, Some(max_items_after_crush))
    let item_strings: Vec<String> = items.iter().map(|v| to_string(v)).collect();
    let max_k = (cfg.max_items_after_crush > 0).then_some(cfg.max_items_after_crush);
    let adaptive_k = compute_optimal_k(&refs(&item_strings), cfg.bias, 3, max_k);
    if n <= adaptive_k {
        return ((0..n).collect(), Vec::new());   // GATE: already small enough
    }
    let effective_max = adaptive_k;

    // BUILD KEEP SET (one strategy; SmartSample shown — pick per analysis).
    let analysis = analyze_array(items);
    // (GATE B: if analysis says Skip → keep all)
    let mut keep: BTreeSet<usize> = BTreeSet::new();
    keep.extend(select_anchors(items, effective_max, pattern, query));      // positional/anchor
    keep.extend((0..n).filter(|&i| is_error[i]));                           // SEAM: errors
    keep.extend(detect_structural_outliers(items));                        // PORT: outliers
    keep.extend(numeric_anomaly_indices(cfg, items, Some(&analysis)));     // PORT: anomalies
    // change points (±1 smart_sample / ±2 time_series) if applicable
    keep.extend((0..n).filter(|&i| relevance[i] >= cfg.relevance_threshold)); // SEAM: relevance

    // PRIORITIZE: dedup → fill → (over-budget) critical-first.
    let final_keep = prioritize_indices(
        cfg, &keep, items, n, Some(&analysis), effective_max, is_error,    // pass is_error through!
    );

    // EXECUTE: ascending, OOB-filtered.
    let mut keep_v: Vec<usize> = final_keep.into_iter().filter(|&i| i < n).collect();
    keep_v.sort_unstable();
    let keep_set: BTreeSet<usize> = keep_v.iter().copied().collect();
    let dropped: Vec<usize> = (0..n).filter(|i| !keep_set.contains(i)).collect();
    (keep_v, dropped)
}

/// prioritize_indices — pass the SEAM array through to the critical set.
fn prioritize_indices(
    cfg: &Config, keep: &BTreeSet<usize>, items: &[Value], n: usize,
    analysis: Option<&ArrayAnalysis>, effective_max: usize,
    is_error: &[bool],                                  // SEAM in
) -> BTreeSet<usize> {
    // 1. dedup (if cfg.dedup_identical_items) via compute_item_hash (md5[:16], sorted-keys json)
    let mut current = if cfg.dedup_identical_items { dedup_by_content(keep, items) } else { keep.clone() };
    // 2. fill to effective_max with stride = max(1, candidates.len()/(remaining+1))
    if current.len() < effective_max && current.len() < n {
        current = fill_remaining_slots(&current, items, n, effective_max);
    }
    // 3. under budget?  (<=, inclusive)
    if current.len() <= effective_max { return current; }
    // 4. over budget — critical first (errors via SEAM ∪ outliers ∪ anomalies),
    //    then first-3 + last-2, then ascending fill from `current`.
    let mut p = BTreeSet::new();
    p.extend((0..n).filter(|&i| is_error[i]));          // SEAM: errors
    p.extend(detect_structural_outliers(items));
    p.extend(numeric_anomaly_indices(cfg, items, analysis));
    let mut remaining = effective_max.saturating_sub(p.len());
    if remaining > 0 {
        for i in 0..3.min(n)            { if !p.contains(&i) && remaining>0 { p.insert(i); remaining-=1; } }
        for i in n.saturating_sub(2)..n { if !p.contains(&i) && remaining>0 { p.insert(i); remaining-=1; } }
    }
    if remaining > 0 {
        let mut others: Vec<usize> = current.difference(&p).copied().collect();
        others.sort();
        for i in others { if remaining==0 { break } p.insert(i); remaining-=1; }
    }
    p   // MAY exceed effective_max — quality guarantee, do NOT clamp
}
```

---

# 9. Parity checklist (pin these exactly)

- [ ] Content hash = **MD5**, **first 16 hex chars**, over **sorted-keys**,
      **spaced-separator** (`", "` / `": "`), **ascii-escaped** (`\uXXXX`,
      surrogate pairs) JSON. (`{"k":"café"} → 6761da28ed7eb489`)
- [ ] Dedup: **lowest index wins** per hash; OOB indices skipped.
- [ ] Fill stride: `step = max(1, candidates.len() / (remaining + 1))`; outer
      `start_offset ∈ [0, step)`, inner `+= step`; stop at `added == remaining`;
      skip content duplicates.
- [ ] Under-budget early return uses **`<=`** (inclusive).
- [ ] Critical set = errors(seam) ∪ structural outliers ∪ numeric anomalies
      (∪ learned=∅). Positional anchors = **first 3, last 2** (in that order).
      Then ascending fill from `current \ prioritized`.
- [ ] Over-budget result **may exceed** `effective_max` — never clamp.
- [ ] Budget `effective_max` = `compute_optimal_k(...)` output (`min_k=3`,
      `max_k=max_items_after_crush=15`), NOT raw 15. Gate: passthrough if
      `n <= adaptive_k`.
- [ ] `first_fraction=0.3`, `last_fraction=0.15` apply in `compute_k_split`
      (non-dict crushers), via banker's rounding, clamped so
      `k_first + k_last <= k_total`.
- [ ] Numeric anomaly: strict `(num - mean).abs() > variance_threshold * std`,
      `variance_threshold = 2.0`, requires `field_type=="numeric"`, `var > 0`.
- [ ] Structural outliers: rare-field `< 20%`, common-field `>= 80%`, rare-status
      cardinality `2..=50`, Pareto threshold `ceil(total*0.8)`, top-K must be `<= 5`.
- [ ] Change-point window: **±1** (smart_sample), **±2** (time_series).
- [ ] top_n: `top_count = max_items - 3`; relevance adds capped at **3** with
      threshold `max(relevance_threshold*2, 0.5)`; **no** `prioritize_indices`.
- [ ] cluster_sample: cluster key = `md5(first 50 chars)[:8]`, **2 reps/cluster**.
- [ ] `execute_plan`: ascending sort, OOB filtered; output = `items[idx].clone()`.
- [ ] Relevance threshold (smart/cluster/time_series query signals) = **0.3**.
- [ ] Error keywords (Python side only, 12): `error, exception, failed, failure,
      critical, fatal, crash, panic, abort, timeout, denied, rejected`.
