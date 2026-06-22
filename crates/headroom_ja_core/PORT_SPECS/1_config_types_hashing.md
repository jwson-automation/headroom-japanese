# PORT SPEC 1 — config + types + hashing (+ error_keywords seam)

Faithful port spec extracted from headroom's Rust source so we can reimplement in
`headroom_ja_core` with **no dependency on headroom**. Defaults and the hash
algorithm are load-bearing — they must match byte-for-byte for parity/dedup.

Sources (headroom `main`, `crates/headroom-core/src/transforms/smart_crusher/`):
- `config.rs`
- `types.rs`
- `hashing.rs`
- `error_keywords.rs`

Each headroom file is itself a "direct port of" the Python `smart_crusher.py` (line
numbers cited in the headroom source are reproduced where relevant). When in doubt,
the Python defaults are the source of truth and the Rust values below already mirror
them.

---

## File: `config.rs`

**Purpose.** Configuration struct `SmartCrusherConfig` for the SmartCrusher
compression transform. Every default is consulted by some compression path; drift
breaks parity fixtures. Defaults mirror Python `smart_crusher.py:934-957` byte-for-byte
(except the PR4 Rust-only dispatch knobs `lossless_min_savings_ratio`,
`enable_ccr_marker`, which have no Python counterpart).

### struct `SmartCrusherConfig`  (`#[derive(Debug, Clone)]`)

| # | Field | Type | Default | Meaning |
|---|-------|------|---------|---------|
| 1 | `enabled` | `bool` | `true` | Master enable. |
| 2 | `min_items_to_analyze` | `usize` | `5` | Don't analyze arrays smaller than this. |
| 3 | `min_tokens_to_crush` | `usize` | `200` | Only crush content with more than this many tokens. |
| 4 | `variance_threshold` | `f64` | `2.0` | Std-devs from mean to count as a change point. |
| 5 | `uniqueness_threshold` | `f64` | `0.1` | Below this unique-ratio a field is "nearly constant". |
| 6 | `similarity_threshold` | `f64` | `0.8` | Similarity score above which strings cluster together. |
| 7 | `max_items_after_crush` | `usize` | `15` | Target max items in output. |
| 8 | `preserve_change_points` | `bool` | `true` | Preserve detected change points. |
| 9 | `factor_out_constants` | `bool` | `false` | Factor out constant-valued fields (disabled — preserves schema). |
| 10 | `include_summaries` | `bool` | `false` | Include generated text summaries (disabled — no generated text). |
| 11 | `use_feedback_hints` | `bool` | `true` | Use feedback hints to tune aggressiveness. |
| 12 | `toin_confidence_threshold` | `f64` | `0.5` | Min confidence to apply TOIN recommendations (Python LOW FIX #21). |
| 13 | `dedup_identical_items` | `bool` | `true` | Drop content-identical items before sampling. |
| 14 | `first_fraction` | `f64` | `0.3` | Fraction of K allocated to start of array. |
| 15 | `last_fraction` | `f64` | `0.15` | Fraction of K allocated to end of array. |
| 16 | `relevance_threshold` | `f64` | `0.3` | Items with `RelevanceScore.score >= this` are pinned. Mirrors Python `RelevanceConfig.relevance_threshold`. |
| 17 | `lossless_min_savings_ratio` | `f64` | `0.15` | **Rust-only (PR4).** Min byte-savings ratio for lossless path over lossy. Computed `1 - len(rendered)/len(input)`. `0.0` = always prefer lossless; `1.0` = disable lossless (lossy+CCR always). |
| 18 | `enable_ccr_marker` | `bool` | `true` | **Rust-only.** Master gate for CCR-Dropped row-drop sentinels. Python shim sets this = `ccr_config.enabled and ccr_config.inject_retrieval_marker`. Gates only the `crush_array` row-drop path. |
| 19 | `compaction_core_field_fraction` | `f64` | `0.8` | A field is "core" if it appears in ≥ this fraction of rows. Mirrors `CompactConfig::core_field_fraction`. |
| 20 | `compaction_heterogeneous_core_ratio` | `f64` | `0.6` | If fewer than this fraction of observed keys are core → array is heterogeneous; look for a discriminator. Mirrors `CompactConfig::heterogeneous_core_ratio`. |
| 21 | `compaction_max_flatten_inner_keys` | `usize` | `6` | Cap on inner-key count for nested-uniform flattening. Mirrors `CompactConfig::max_flatten_inner_keys`. |
| 22 | `compaction_min_buckets` | `usize` | `2` | Min bucket count before a candidate discriminator is "useful". Mirrors `CompactConfig::min_buckets`. |
| 23 | `compaction_max_buckets` | `usize` | `8` | Max bucket count (too many → too granular, e.g. ID column). Mirrors `CompactConfig::max_buckets`. |

`impl Default for SmartCrusherConfig` returns exactly the defaults above. (Pinned by a
`defaults_match_python` test — every field asserted.)

**Edge note:** `lossless_min_savings_ratio` and `enable_ccr_marker` are the only two
fields with no Python equivalent; if you ever build a Python-parity bridge, they are
set on the Rust side only. Everything else must equal the Python dataclass default.

---

## File: `types.rs`

**Purpose.** Core data types (enums/structs) for SmartCrusher, mirroring the Python
dataclasses at `smart_crusher.py:318-924` 1:1 so a PyO3 bridge can `from_dict`-rebuild
them. Uses `serde_json::Value` and `std::collections::BTreeMap`.

Imports: `use serde_json::Value;` and `use std::collections::BTreeMap;`

### enum `CompressionStrategy`  (`#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]`)

Mirrors `CompressionStrategy` at `smart_crusher.py:318-326`. Variants and their
`as_str()` values are **pinned by parity fixtures — must not drift**.

| Variant | `as_str()` value | Meaning |
|---------|------------------|---------|
| `None` | `"none"` | No compression needed. |
| `Skip` | `"skip"` | Explicitly skip — not safe to crush. |
| `TimeSeries` | `"time_series"` | Time-series: keep change points, summarize stable runs. |
| `ClusterSample` | `"cluster"` | Cluster-sample: dedupe similar items. |
| `TopN` | `"top_n"` | Top-N: keep highest-scored items. |
| `SmartSample` | `"smart_sample"` | Smart-sample: statistical sampling with anchor preservation. |

Public fn:
```
pub fn as_str(self) -> &'static str
```
Maps each variant to the lowercase string above (matches Python `Enum.value`). Note the
non-obvious mappings: `ClusterSample → "cluster"` (not `"cluster_sample"`) and
`TopN → "top_n"`.

### struct `FieldStats`  (`#[derive(Debug, Clone)]`)

Mirrors `FieldStats` at `smart_crusher.py:864-885`. Statistics for one field across
array items.

| Field | Type | Notes |
|-------|------|-------|
| `name` | `String` | Field name. |
| `field_type` | `String` | One of `"numeric"`, `"string"`, `"boolean"`, `"object"`, `"array"`, `"null"`. String literals match Python `field_type`. |
| `count` | `usize` | |
| `unique_count` | `usize` | |
| `unique_ratio` | `f64` | |
| `is_constant` | `bool` | |
| `constant_value` | `Option<Value>` | |
| `min_val` | `Option<f64>` | Numeric-specific. |
| `max_val` | `Option<f64>` | Numeric-specific. |
| `mean_val` | `Option<f64>` | Numeric-specific. |
| `variance` | `Option<f64>` | Numeric-specific. |
| `change_points` | `Vec<usize>` | Numeric-specific (indices). |
| `avg_length` | `Option<f64>` | String-specific. |
| `top_values` | `Vec<(String, usize)>` | String-specific. Top values by frequency desc; bounded. Mirrors Python `list[tuple[str, int]]`. |

No `Default` impl, no associated fns. (Construct field-by-field.)

### struct `CrushabilityAnalysis`  (`#[derive(Debug, Clone)]`)

Mirrors `CrushabilityAnalysis` at `smart_crusher.py:833-860`. Key invariant: **if there
is no reliable signal to decide which items are important, don't crush at all.**

| Field | Type |
|-------|------|
| `crushable` | `bool` |
| `confidence` | `f64` |
| `reason` | `String` |
| `signals_present` | `Vec<String>` |
| `signals_absent` | `Vec<String>` |
| `has_id_field` | `bool` |
| `id_uniqueness` | `f64` |
| `avg_string_uniqueness` | `f64` |
| `has_score_field` | `bool` |
| `error_item_count` | `usize` |
| `anomaly_count` | `usize` |

Public fn:
```
pub fn skip(reason: impl Into<String>, confidence: f64) -> Self
```
Builds a "not crushable" verdict for early exits. Logic: set `crushable = false`,
`confidence = confidence`, `reason = reason.into()`; `signals_present` and
`signals_absent` = empty `Vec`; all detail metrics zeroed (`has_id_field = false`,
`id_uniqueness = 0.0`, `avg_string_uniqueness = 0.0`, `has_score_field = false`,
`error_item_count = 0`, `anomaly_count = 0`).

### struct `ArrayAnalysis`  (`#[derive(Debug, Clone)]`)

Mirrors `ArrayAnalysis` at `smart_crusher.py:887-897`. Complete analysis of an array.

| Field | Type | Notes |
|-------|------|-------|
| `item_count` | `usize` | |
| `field_stats` | `BTreeMap<String, FieldStats>` | Sorted-by-key iteration. **Parity nuance below.** |
| `detected_pattern` | `String` | One of `"time_series"`, `"logs"`, `"search_results"`, `"generic"`. |
| `recommended_strategy` | `CompressionStrategy` | |
| `constant_fields` | `BTreeMap<String, Value>` | |
| `estimated_reduction` | `f64` | |
| `crushability` | `Option<CrushabilityAnalysis>` | |

**Sort vs insertion-order parity nuance (verbatim from source).** Python `dict`
preserves insertion order; `_analyze_field` runs once per key in `items[0].keys()`
(JSON parse order). With `serde_json/preserve_order`, `serde_json::Map` is an `IndexMap`
matching Python. Here `BTreeMap` gives **sorted-key** iteration, which differs. Matters
only if a downstream path observes iteration order of `field_stats` (debug output,
"first field" selection, strategy strings that embed field names). headroom flags two
future options: switch to `IndexMap`, or rewrite Python's order-sensitive paths to
iterate sorted and mirror that in Rust. **For our port: be aware; if any later stage
observes field_stats order, prefer `IndexMap` with `preserve_order` to match Python.**

### struct `CompressionPlan`  (`#[derive(Debug, Clone)]`)

Mirrors `CompressionPlan` at `smart_crusher.py:900-910`. Plan for compressing an array.

| Field | Type | Default (from `Default` impl) | Notes |
|-------|------|-------------------------------|-------|
| `strategy` | `CompressionStrategy` | `CompressionStrategy::None` | |
| `keep_indices` | `Vec<usize>` | `Vec::new()` | Original-array indices that survive. |
| `constant_fields` | `BTreeMap<String, Value>` | `BTreeMap::new()` | |
| `summary_ranges` | `Vec<(usize, usize, Value)>` | `Vec::new()` | `(start, end, summary)` for summarized runs. Python `list[tuple[int,int,dict]]`; `Value` used so any JSON shape fits. Currently unused in Python impl but plumbed for parity. |
| `cluster_field` | `Option<String>` | `None` | |
| `sort_field` | `Option<String>` | `None` | |
| `keep_count` | `usize` | `10` | **Load-bearing default** — Python `@dataclass` default `keep_count: int = 10`. Pinned by test. |

`impl Default for CompressionPlan` returns exactly the defaults in the table above.

### struct `CrushResult`  (`#[derive(Debug, Clone)]`)

Mirrors `CrushResult` at `smart_crusher.py:913-923`. Result from `SmartCrusher.crush()`,
used by ContentRouter when routing JSON arrays.

| Field | Type |
|-------|------|
| `compressed` | `String` |
| `original` | `String` |
| `was_modified` | `bool` |
| `strategy` | `String` |

Public fn:
```
pub fn passthrough(content: impl Into<String>) -> Self
```
Pass-through result for uncompressible content (not JSON, too small, no crushable
arrays). Logic: `let s = content.into();` then `compressed = s.clone()`, `original = s`,
`was_modified = false`, `strategy = "passthrough".to_string()`. (Note the exact strategy
string `"passthrough"`, pinned by test.)

---

## File: `hashing.rs`

**Purpose.** Field-name hashing for cache keys. Direct port of Python `_hash_field_name`
(`smart_crusher.py:171-177`). Used to look up TOIN-anonymized `preserve_fields`; TOIN
stores field names as **SHA-256[:8]**, so a wrong truncation length silently misses
every cache lookup and defeats the `use_feedback_hints` path.

### EXACT HASH ALGORITHM (byte-for-byte parity — critical)

- Algorithm: **SHA-256** (crate `sha2`, `Sha256`).
- Input bytes: the **raw UTF-8 bytes of `field_name`** — `field_name.as_bytes()`. No
  normalization, no canonical JSON, no key sorting, no quoting. It is a plain string
  hash of the field name only.
- Encoding: hex via `format!("{:x}", digest)` → **lowercase** hex. (Both Python
  `hexdigest()` and Rust `sha2` produce lowercase; no case-coercion needed.)
- Truncation: **first 8 hex characters** (= 4 bytes of digest), `hex[..8]`.
- Python equivalent: `hashlib.sha256(field_name.encode()).hexdigest()[:8]`.

> **WARNING — pinned to `[:8]`, not `[:16]`.** An earlier headroom version used `[:16]`
> by misreading the Python; review caught it. Use **8**. Output is always exactly 8 hex
> chars regardless of input length.

### Public function

```
pub fn hash_field_name(field_name: &str) -> String
```
Step-by-step:
1. `let mut hasher = Sha256::new();`
2. `hasher.update(field_name.as_bytes());`  (UTF-8 bytes, unmodified)
3. `let digest = hasher.finalize();`
4. `let hex = format!("{:x}", digest);`  (lowercase hex of full 32-byte digest)
5. `return hex[..8].to_string();`  (first 8 hex chars)

### Reference vectors (verified against Python — use as our test fixtures)

| Input | Output |
|-------|--------|
| `"customer_id"` | `1e38d67d` |
| `""` (empty) | `e3b0c442` |
| `"café"` (UTF-8 bytes `63 61 66 c3 a9`) | `850f7dc4` |
| `"test"` | deterministic (equals itself across calls) |
| `"a"` / `"x"×1000` | length always exactly `8` |

Edge cases: empty string is valid (`e3b0c442`); Unicode must be encoded as UTF-8 (the
`"café"` vector pins this); output length is invariant at 8.

---

## File: `error_keywords.rs` — **SEAM: do NOT port to Rust**

**Purpose (in headroom).** Canonical fallback error-keyword set for item preservation.
Direct port of `ERROR_KEYWORDS` from `headroom/transforms/error_detection.py:18-33`.
Fallback preservation signal when TOIN field semantics aren't available; intentionally
broad ("better to over-preserve than drop a real error item"). Used by
`detect_error_items_for_preservation`. Lowercase by construction; **callers lowercase
the haystack before substring-matching.**

headroom's 12 keywords (English; recorded here for reference only):
`error`, `exception`, `failed`, `failure`, `critical`, `fatal`, `crash`, `panic`,
`abort`, `timeout`, `denied`, `rejected`.

### ⚠️ JAPANESE SEAM — porting decision

**Do NOT port the keyword list into our Rust crate.** In `headroom_ja_core`, error
detection is the **Japanese seam**: detection happens in **Python** (Japanese keywords,
language-aware logic) and is passed into Rust as a **per-item boolean**.

Concretely for our port:
- Rust does **not** own `ERROR_KEYWORDS`, does **not** lowercase/substring-match, and
  does **not** replicate `detect_error_items_for_preservation`.
- The crushability / preservation logic in Rust consumes a caller-supplied
  `is_error: &[bool]` (one bool per array item, aligned by index), produced by the
  Python layer using Japanese keyword/semantic detection.
- `CrushabilityAnalysis.error_item_count` should be derived from that supplied
  `is_error[]` (count of `true`), not from a Rust keyword scan.

This keeps Japanese-language error semantics in Python where they belong and makes the
Rust core language-agnostic.

---

## Suggested idiomatic Rust skeleton (the parts we WILL port)

> Spec only — implement verbatim later. `error_keywords` is intentionally absent (seam).

```rust
// config.rs
#[derive(Debug, Clone)]
pub struct SmartCrusherConfig {
    pub enabled: bool,
    pub min_items_to_analyze: usize,
    pub min_tokens_to_crush: usize,
    pub variance_threshold: f64,
    pub uniqueness_threshold: f64,
    pub similarity_threshold: f64,
    pub max_items_after_crush: usize,
    pub preserve_change_points: bool,
    pub factor_out_constants: bool,
    pub include_summaries: bool,
    pub use_feedback_hints: bool,
    pub toin_confidence_threshold: f64,
    pub dedup_identical_items: bool,
    pub first_fraction: f64,
    pub last_fraction: f64,
    pub relevance_threshold: f64,
    pub lossless_min_savings_ratio: f64, // Rust-only
    pub enable_ccr_marker: bool,         // Rust-only
    pub compaction_core_field_fraction: f64,
    pub compaction_heterogeneous_core_ratio: f64,
    pub compaction_max_flatten_inner_keys: usize,
    pub compaction_min_buckets: usize,
    pub compaction_max_buckets: usize,
}

impl Default for SmartCrusherConfig {
    fn default() -> Self {
        SmartCrusherConfig {
            enabled: true,
            min_items_to_analyze: 5,
            min_tokens_to_crush: 200,
            variance_threshold: 2.0,
            uniqueness_threshold: 0.1,
            similarity_threshold: 0.8,
            max_items_after_crush: 15,
            preserve_change_points: true,
            factor_out_constants: false,
            include_summaries: false,
            use_feedback_hints: true,
            toin_confidence_threshold: 0.5,
            dedup_identical_items: true,
            first_fraction: 0.3,
            last_fraction: 0.15,
            relevance_threshold: 0.3,
            lossless_min_savings_ratio: 0.15,
            enable_ccr_marker: true,
            compaction_core_field_fraction: 0.8,
            compaction_heterogeneous_core_ratio: 0.6,
            compaction_max_flatten_inner_keys: 6,
            compaction_min_buckets: 2,
            compaction_max_buckets: 8,
        }
    }
}

// types.rs
use serde_json::Value;
use std::collections::BTreeMap; // consider IndexMap if field order must match Python

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CompressionStrategy {
    None,
    Skip,
    TimeSeries,
    ClusterSample,
    TopN,
    SmartSample,
}

impl CompressionStrategy {
    pub fn as_str(self) -> &'static str {
        match self {
            CompressionStrategy::None => "none",
            CompressionStrategy::Skip => "skip",
            CompressionStrategy::TimeSeries => "time_series",
            CompressionStrategy::ClusterSample => "cluster",
            CompressionStrategy::TopN => "top_n",
            CompressionStrategy::SmartSample => "smart_sample",
        }
    }
}

#[derive(Debug, Clone)]
pub struct FieldStats {
    pub name: String,
    pub field_type: String, // "numeric"|"string"|"boolean"|"object"|"array"|"null"
    pub count: usize,
    pub unique_count: usize,
    pub unique_ratio: f64,
    pub is_constant: bool,
    pub constant_value: Option<Value>,
    pub min_val: Option<f64>,
    pub max_val: Option<f64>,
    pub mean_val: Option<f64>,
    pub variance: Option<f64>,
    pub change_points: Vec<usize>,
    pub avg_length: Option<f64>,
    pub top_values: Vec<(String, usize)>,
}

#[derive(Debug, Clone)]
pub struct CrushabilityAnalysis {
    pub crushable: bool,
    pub confidence: f64,
    pub reason: String,
    pub signals_present: Vec<String>,
    pub signals_absent: Vec<String>,
    pub has_id_field: bool,
    pub id_uniqueness: f64,
    pub avg_string_uniqueness: f64,
    pub has_score_field: bool,
    pub error_item_count: usize, // derive from Python-supplied is_error[] (seam)
    pub anomaly_count: usize,
}

impl CrushabilityAnalysis {
    pub fn skip(reason: impl Into<String>, confidence: f64) -> Self {
        CrushabilityAnalysis {
            crushable: false,
            confidence,
            reason: reason.into(),
            signals_present: Vec::new(),
            signals_absent: Vec::new(),
            has_id_field: false,
            id_uniqueness: 0.0,
            avg_string_uniqueness: 0.0,
            has_score_field: false,
            error_item_count: 0,
            anomaly_count: 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ArrayAnalysis {
    pub item_count: usize,
    pub field_stats: BTreeMap<String, FieldStats>,
    pub detected_pattern: String, // "time_series"|"logs"|"search_results"|"generic"
    pub recommended_strategy: CompressionStrategy,
    pub constant_fields: BTreeMap<String, Value>,
    pub estimated_reduction: f64,
    pub crushability: Option<CrushabilityAnalysis>,
}

#[derive(Debug, Clone)]
pub struct CompressionPlan {
    pub strategy: CompressionStrategy,
    pub keep_indices: Vec<usize>,
    pub constant_fields: BTreeMap<String, Value>,
    pub summary_ranges: Vec<(usize, usize, Value)>,
    pub cluster_field: Option<String>,
    pub sort_field: Option<String>,
    pub keep_count: usize,
}

impl Default for CompressionPlan {
    fn default() -> Self {
        CompressionPlan {
            strategy: CompressionStrategy::None,
            keep_indices: Vec::new(),
            constant_fields: BTreeMap::new(),
            summary_ranges: Vec::new(),
            cluster_field: None,
            sort_field: None,
            keep_count: 10,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CrushResult {
    pub compressed: String,
    pub original: String,
    pub was_modified: bool,
    pub strategy: String,
}

impl CrushResult {
    pub fn passthrough(content: impl Into<String>) -> Self {
        let s = content.into();
        CrushResult {
            compressed: s.clone(),
            original: s,
            was_modified: false,
            strategy: "passthrough".to_string(),
        }
    }
}

// hashing.rs
use sha2::{Digest, Sha256};

/// SHA-256 of the UTF-8 bytes of `field_name`, lowercase hex, truncated to 8 chars.
/// Python: hashlib.sha256(field_name.encode()).hexdigest()[:8]
pub fn hash_field_name(field_name: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(field_name.as_bytes());
    let digest = hasher.finalize();
    let hex = format!("{:x}", digest);
    hex[..8].to_string()
}
```

### Dependencies needed
- `serde_json` (with `preserve_order` feature at workspace level if field order must
  match Python).
- `sha2` (for `Sha256`).
- Optionally `indexmap` if `field_stats` order needs Python parity (see nuance above).

### Tests to carry over (parity guards)
- `defaults_match_python`: assert all 23 config defaults exactly.
- `compression_strategy_strings_match_python`: assert all 6 `as_str()` values.
- `compression_plan_default_keep_count_matches_python`: `keep_count == 10`,
  `strategy == None`, `keep_indices` empty.
- `crush_result_passthrough`: strategy `"passthrough"`, not modified, compressed ==
  original == input.
- `hash_field_name`: vectors `customer_id→1e38d67d`, `""→e3b0c442`,
  `café→850f7dc4`, determinism, length always 8.
