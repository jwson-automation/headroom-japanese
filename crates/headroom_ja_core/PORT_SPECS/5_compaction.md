# Port Spec 5 — Lossless Compaction (Smart Crusher / compaction subsystem)

Faithful port spec for headroom's **lossless-first** array compaction. This is the path
tried *before* lossy row-dropping: an array of JSON objects is turned into a columnar
("tabular") representation that is byte-for-byte reconstructable, then rendered to a
compact textual form (CSV+schema by default). If it doesn't compact (or doesn't save
enough bytes), the caller falls back to the lossy path.

**Source module** (headroom `crates/headroom-core/src/transforms/smart_crusher/compaction/`):
`mod.rs`, `compactor.rs`, `classifier.rs`, `walker.rs`, `ir.rs`, `formatter.rs`.

Citations below are `file:line` against the fetched `main` revision.

> **Scope boundary.** The accept/reject **savings gate**
> (`lossless_min_savings_ratio = 0.15`, "byte savings vs lossy") is **NOT** in these six
> files. The only gate inside this subsystem is `Compaction::was_compacted()`
> (`walker.rs:121`). The savings-ratio dispatch lives on `SmartCrusherConfig` and in the
> PR4 `crush_array` caller — see **§9** and Port Spec `1_config_types_hashing.md:317,345`.

---

## 0. Pipeline overview (`mod.rs:1-24`)

```
input array
   ↓
[TabularCompactor / compact()]  → Compaction IR (recursive tree)   (compactor.rs)
   ↓
[Formatter trait]               → bytes (String)                   (formatter.rs)
```

The **`DocumentCompactor` walker** (`walker.rs`) wraps both: it recurses through an
arbitrary JSON document, finds compactable spots (arrays-of-objects, stringified-JSON,
opaque blobs), and replaces each spot **in place** with a rendered string. Document shape
is preserved; only bulky leaves become strings.

`CompactionStage` (`mod.rs:48-118`) pairs a `CompactConfig` with a boxed `Formatter` and
exposes `run(items) -> (Compaction, String)` (`mod.rs:113-117`).

Formatter presets by name (`mod.rs:95-108`): `"csv-schema"` (default/recommended),
`"json"` (debug/structured), `"markdown-kv"` (token-heavier, higher read accuracy).

---

## 1. The decision to compact (`compactor.rs:98-128`)

`compact(items: &[Value], cfg: &CompactConfig) -> Compaction`:

1. **Too few items** — `items.len() < cfg.min_items` (default **2**) → `Untouched(Array(items))`. (`compactor.rs:99-101`)
2. **Not all objects** — any item not `Value::Object` → `Untouched(Array(items))`. (`compactor.rs:102-104`)
3. Compute **key frequencies** over the union of all object keys (`compute_key_freqs`, `compactor.rs:130-140`): a `BTreeMap<String,usize>` counting how many rows contain each key.
4. **Core fields** (`compactor.rs:107-116`):
   - `total = items.len()`
   - `core_threshold = ceil(total * core_field_fraction)` with `core_field_fraction = 0.8` → a key is **core** if it appears in `>= core_threshold` rows (i.e. **≥ 80 % of rows**, rounded up).
   - `core_count` = number of keys that are core.
   - `total_keys` = number of distinct keys.
   - `core_ratio = core_count / total_keys` (or `1.0` if `total_keys == 0`).
5. **Heterogeneous branch** (`compactor.rs:118-125`): if `core_ratio < heterogeneous_core_ratio`
   (default **0.6** — see §1.1 discrepancy) **AND** a discriminator is found → return
   `bucket_by(...)` (`Compaction::Buckets`). If no clean discriminator is found, **fall
   through** to a sparse homogeneous table anyway (a sparse table still beats letting the
   lossy path drop fields wholesale).
6. Otherwise → `build_homogeneous_table(...)` → `Compaction::Table`. (`compactor.rs:127`)

### 1.1 ⚠ Doc-vs-code constant discrepancy — `heterogeneous_core_ratio`

- The **module doc** (`compactor.rs:25`) says: "When **< 50 %** of keys appear in ≥ 80 % of rows..."
- `CompactConfig` field doc (`compactor.rs:64-67`) says **Default: 0.5**.
- The actual **`Default` impl uses `0.6`** (`compactor.rs:89`).

**Port the code value `0.6`**, not the doc value `0.5`. (Treat the `0.5` mentions as stale comments.)

### 1.2 "Cleanly tabular" — summary of constants

| Concept | Constant | Default | Source |
|---|---|---|---|
| Min items to attempt compaction | `min_items` | `2` | `compactor.rs:57,91` |
| Core-field fraction (key shared by ≥ X of rows) | `core_field_fraction` | `0.8` | `compactor.rs:62,89` |
| Heterogeneous trigger (`core_ratio <` this → try buckets) | `heterogeneous_core_ratio` | **`0.6`** (code) | `compactor.rs:67,89` |
| Nested-flatten inner-key cap | `max_flatten_inner_keys` | `6` | `compactor.rs:72,90` |
| Min bucket count | `min_buckets` | `2` | `compactor.rs:76,92` |
| Max bucket count | `max_buckets` | `8` | `compactor.rs:80,92` |
| Discriminator "essentially unique" reject ratio | (inline literal) | `> 0.7` distinct/total → reject | `compactor.rs:441` |
| Inner-array recursion min length | (inline literal) | `>= 2` objects | `compactor.rs:204,214` |

---

## 2. Building the homogeneous table (`compactor.rs:142-180`)

### 2.1 Column order — **descending frequency, then alphabetical**

`build_homogeneous_table` (`compactor.rs:147-150`):

```
keys = key_freqs.iter().collect()
keys.sort_by(|a, b| b.freq.cmp(a.freq).then_with(|| a.key.cmp(b.key)))
```

So columns are ordered by **descending frequency**; ties broken **alphabetically ascending**.
(Test `stable_field_ordering`, `compactor.rs:687-704`: `common` first; tied rares `a_rare` before `z_rare`.)

### 2.2 FieldSpec per column (`compactor.rs:153-164`)

For each ordered key, build a `FieldSpec { name, type_tag, nullable }`:

- `name` = the key (later may be rewritten to a dotted name by flattening — §4).
- `type_tag` = `infer_type_tag(items, key)` (§2.4).
- `nullable = true` iff **either** `key_freqs[key] < total` (key absent in ≥ 1 row)
  **or** any row has the key present but `Value::Null`. (`compactor.rs:158-162`)
  (Test `nullable_field_marked`, `compactor.rs:706-722`.)

### 2.3 Row emission (`compactor.rs:166-169, 182-195`)

`build_row(item, ordered_keys, cfg)`: for each ordered key, look it up in the object:

- key **absent** → `CellValue::Missing` (`compactor.rs:189-190`). This is **distinct** from a
  present-but-null value, which becomes `Scalar(Value::Null)`. (IR doc `ir.rs:82-86`.)
- key present → `cell_from_value(v, cfg)` (§3).

Row length always equals `ordered_keys.len()` (then equals schema field count after flatten).

### 2.4 `infer_type_tag` (`compactor.rs:357-387`)

Scan all rows' values for the key, **skipping nulls** (`compactor.rs:361-363`):

- First non-null value sets the tag via `type_tag_for`.
- If a later value's tag differs → tag becomes `"json"` (mixed) and scan stops.
- If no non-null value seen → `"string"` (the `unwrap_or` default, `compactor.rs:375`).

`type_tag_for(v)` (`compactor.rs:378-387`):

| JSON value | `type_tag` |
|---|---|
| `Null` | `"null"` |
| `Bool` | `"bool"` |
| `Number` where `is_i64() \|\| is_u64()` | `"int"` |
| other `Number` | `"float"` |
| `String` | `"string"` |
| `Object` / `Array` | `"json"` |

`FieldSpec.type_tag` may also be `"ccr"` for opaque-pointer columns (IR doc `ir.rs:46-47`).

---

## 3. Cell classification & cell building

### 3.1 `cell_from_value` (`compactor.rs:197-232`)

Calls `classify_cell` (§3.2), then:

- **`Scalar`** → `CellValue::Scalar(v.clone())`.
- **`JsonObject`** → `CellValue::Scalar(v.clone())` — left as a scalar-object; the
  flatten pass (§4) may later promote it into dotted columns. (`compactor.rs:200`)
- **`JsonArray`** → if the inner array is **all objects AND len ≥ 2**, recurse:
  `CellValue::Nested(Box::new(compact(items, cfg)))`. Otherwise `Scalar(v.clone())`. (`compactor.rs:201-209`)
- **`StringifiedJson(parsed)`** → if `parsed` is an **array of objects, len ≥ 2**, recurse
  into `Nested(compact(...))`; otherwise store the **parsed** value as `Scalar(parsed)`
  (un-escapes the embedded JSON for free). (`compactor.rs:210-219`)
- **`Opaque(kind)`** → if the value is a `String`, emit
  `CellValue::OpaqueRef { ccr_hash, byte_size, kind }`; else fall back to `Scalar(v)`.
  `ccr_hash = hash_opaque(bytes)` = first **6 bytes** of SHA-256, lowercase hex → **12-char**
  string (`compactor.rs:389-397`; test `hash_opaque_stable_and_short`, `:725-732`).
  `byte_size = bytes.len()`. (`compactor.rs:220-230`)

### 3.2 `classify_cell` (`classifier.rs:73-111`)

```
Object  → JsonObject
Array   → JsonArray
String  → classify_string(s, cfg)
else    → Scalar
```

`classify_string` (`classifier.rs:82-111`), in order:

1. **Stringified-JSON fast path**: trim leading whitespace; if first char is `{` or `[`,
   `serde_json::from_str::<Value>(s)`. If it parses to **Object or Array**, return
   `StringifiedJson(parsed)`. (Note: a string like `"123"` parses as a JSON *number* but is
   intentionally treated as a scalar, not recursed — `classifier.rs:84-95`, test `:218-224`.)
2. **Length gate**: if `s.len() <= opaque_min_bytes` (default **256**) → `Scalar`. (`classifier.rs:98-100`)
3. **base64**: `looks_like_base64` → `Opaque(Base64Blob)`. (`classifier.rs:102-104`)
4. **HTML**: `looks_like_html` → `Opaque(HtmlChunk)`. (`classifier.rs:106-108`)
5. else → `Opaque(LongString)`. (`classifier.rs:110`)

**`ClassifyConfig`** (`classifier.rs:48-70`):

| Field | Default | Meaning |
|---|---|---|
| `opaque_min_bytes` | `256` | Strings strictly longer become opaque candidates. |
| `base64_alphabet_ratio` | `0.95` | Min fraction of chars in `[A-Za-z0-9+/=_-]`. |
| `html_min_open_brackets` | `3` | Min `<` (tag-start) count to call it HTML. |

**`looks_like_base64`** (`classifier.rs:113-146`):
- `false` if `s.len() < 64`.
- Disqualify if contains `<` or `>`, or any whitespace.
- `alphabet = count(chars in [ascii_alphanumeric] ∪ {+ / = _ -})`; require
  `alphabet/total >= ratio_threshold` (0.95).
- **Diversity filter**: require **≥ 16 distinct characters** (returns `true` as soon as the
  unique-char set hits 16; otherwise `false`). Guards against e.g. `{xxxx...}`.

**`looks_like_html`** (`classifier.rs:148-167`):
- Count `<`; require `>= min_open_brackets` (3).
- Count "tag starts" = `<` immediately followed by an ASCII letter, `/`, or `!`.
- Require `tag_starts >= min_open_brackets`. (Avoids false positives on `"a < b"`.)

### 3.3 `CellClass` enum (`classifier.rs:25-40`)

`Scalar | JsonObject | JsonArray | StringifiedJson(Value) | Opaque(OpaqueKind)`.

> **v1 deferral note (opaque refs).** The `<<ccr:...>>` opaque-ref substitution and the CCR
> store are **deferrable**. In v1 we MAY skip opaque classification entirely (treat long
> strings as plain `Scalar`) and never emit `OpaqueRef`. This loses lossless reconstruction
> of large blobs unless the CCR store is implemented, but keeps the columnar path intact.
> If skipping: in `classify_string` return `Scalar` for everything that isn't
> stringified-JSON, and never produce `CellValue::OpaqueRef`. Marker format is in §6.4 for
> when it's implemented.

---

## 4. Nested-uniform flattening → dotted columns (`compactor.rs:237-355`)

Runs once after rows are built (`build_homogeneous_table` calls
`flatten_uniform_nested(&mut specs, &mut rows, cfg)`, `compactor.rs:171`).

### 4.1 Eligibility — `uniform_object_keys(specs, rows, col)` (`compactor.rs:325-355`)

A column is flattenable iff:
- Its name does **not** already contain `.` (not already flattened). (`compactor.rs:326-329`)
- Every row's cell at `col` is **either** `Scalar(Object(map))` **or** `Missing`. (`compactor.rs:332-350`)
- All present objects share the **exact same key set** (`Vec<String>` equality, order
  included — `serde_json::Map` preserves insertion/BTree order). Any mismatch → `None`. (`compactor.rs:338-346`)
- At least one object was seen (`saw_object`). (`compactor.rs:351-353`)

Returns the canonical inner-key list.

### 4.2 The cap (`compactor.rs:240-246`)

Flatten only if `!keys.is_empty() && keys.len() <= max_flatten_inner_keys` (default **6**).
Larger inner schemas stay nested (i.e. remain a single `meta` column holding the object).

### 4.3 Splice (`compactor.rs:248-297`)

For an eligible parent column at index `i` with `parent_name`:
- Build `n_new` new specs named `"{parent}.{inner_key}"`, provisional `type_tag = "string"`,
  `nullable = false`. (`compactor.rs:249-257`)
- `specs.splice(i..i+1, new_specs)` replaces the parent column. (`compactor.rs:260`)
- For each row: `remove` the cell at `i`; if it was `Scalar(Object(map))`, expand into the
  inner-key order: `map.get(k)` → `Scalar(v)` or `Missing`; if the original cell was
  `Missing`, **all** expanded cells are `Missing`. Insert the expanded cells back at `i..`. (`compactor.rs:263-285`)
  (Anything other than `Scalar(Object)` / `Missing` is `unreachable!()` — guaranteed by 4.1, `compactor.rs:268-270`.)
- **Refine** each new column's `type_tag` + `nullable` from the actual cells via
  `infer_type_tag_from_cells` (§4.4). (`compactor.rs:288-294`)
- Advance `i += n_new`. (`compactor.rs:296`)

Tests: `nested_uniform_is_flattened` (`compactor.rs:554-571`) → produces `meta.region`,
`meta.tier`, drops `meta`. `nested_mixed_keys_stay_nested` (`:573-589`) → keeps `meta`.

### 4.4 `infer_type_tag_from_cells` (`compactor.rs:300-321`)

Per column, scan cells:
- `Missing` or `Scalar(Null)` → set `nullable = true`.
- First `Scalar(v)` sets `tag = type_tag_for(v)`; a later differing scalar tag → `"json"`.
- Any non-scalar cell → `"json"`.
- Default tag before any value seen: `"string"`.

---

## 5. Heterogeneous bucketing (`compactor.rs:399-501`)

### 5.1 `detect_discriminator` (`compactor.rs:404-452`)

Find the best discriminator key. For each key in `key_freqs`:
- Must be **present in every row**: `freq < total` → skip. (`compactor.rs:413`)
- All its values must be **strings**; any non-string → skip the key. (`compactor.rs:417-430`)
- Count `distinct` string values = `n`. Require `min_buckets <= n <= max_buckets`
  (i.e. **2..=8**). (`compactor.rs:436-438`)
- **Reject near-unique** keys (ID-like): `n / total > 0.7` → skip. (`compactor.rs:441-443`)
- `score = n` (prefer more buckets, up to max); keep the highest-scoring key. (`compactor.rs:444-449`)

Returns `Option<String>` (the chosen key). Test `id_like_field_not_chosen_as_discriminator`
(`compactor.rs:669-685`): unique `id` rejected, categorical `kind` chosen.

### 5.2 `bucket_by` (`compactor.rs:454-501`)

- Group items into `BTreeMap<String, Vec<Value>>` keyed by the discriminator's string value;
  missing/non-string value → `"__missing__"`. (`compactor.rs:455-464`)
- For each group, recursively `compact(group_items, cfg)`:
  - If the sub-result is `Table { schema, rows, .. }` → `Bucket { key, schema, rows }`. (`compactor.rs:469-474`)
  - Otherwise (sub-compaction declined) → **degenerate single-column fallback**: a one-column
    `"value":"json"` schema, one row per item holding `Scalar(item)`. (`compactor.rs:475-492`)
- Returns `Compaction::Buckets { discriminator, buckets, original_count = items.len() }`.

`BTreeMap` iteration means bucket order is **alphabetical by key**.

---

## 6. The IR (`ir.rs`)

### 6.1 `Compaction` enum (`ir.rs:122-149`)

```
Table     { schema: Schema, rows: Vec<Row>, original_count: usize }
Buckets   { discriminator: String, buckets: Vec<Bucket>, original_count: usize }
OpaqueRef { ccr_hash: String, byte_size: usize, kind: OpaqueKind }   // top-level opaque (rare)
Untouched(Value)                                                      // declined → caller falls back
```

`original_count` = pre-row-drop count. Helpers: `kept_row_count()` (`ir.rs:154-160`),
`original_row_count()` (`ir.rs:163-169`), `was_compacted()` = true for Table/Buckets/OpaqueRef,
false for Untouched (`ir.rs:171-177`).

### 6.2 `Schema` / `FieldSpec` (`ir.rs:39-63`)

`FieldSpec { name: String, type_tag: String, nullable: bool }`. `name` may be dotted.
`type_tag ∈ {int, float, string, bool, null, json, ccr}`. `Schema { fields: Vec<FieldSpec> }`
with `field_names()`.

### 6.3 `Row` / `CellValue` (`ir.rs:65-103`)

`Row(pub Vec<CellValue>)`. Cell order/length matches the parent schema's fields.

```
CellValue::Scalar(Value)                                  // number/string/bool/null literal
CellValue::Nested(Box<Compaction>)                        // recursive sub-table
CellValue::OpaqueRef { ccr_hash, byte_size, kind }        // CCR pointer
CellValue::Missing                                        // key absent (≠ Scalar(Null))
```

### 6.4 `OpaqueKind` (`ir.rs:25-37`) + marker string

`Base64Blob | LongString | HtmlChunk | Other(String)`. Kind→string mapping used in markers:
`base64 / string / html / <custom>`.

`Bucket { key: Value, schema: Schema, rows: Vec<Row> }` (`ir.rs:107-113`).

---

## 7. The walker (`walker.rs`)

`DocumentCompactor { config: CompactConfig, formatter: Box<dyn Formatter>, ccr_store: Option<Arc<dyn CcrStore>> }`
(`walker.rs:53-60`). Default formatter = `CsvSchemaFormatter` (`walker.rs:62-70`).
`compact(doc) -> Value` calls `walk` (`walker.rs:94-97`).

`walk(v, ctx)` (`walker.rs:99-106`):
- **Object** → recurse each field value (`walk_object`, `:108-110`).
- **Array** → `walk_array` (`:112-126`): **recurse into items FIRST**, then
  `compact(&inner, cfg)`. If `was_compacted()` → replace whole array with
  `Value::String(formatter.format(&c))`; else keep `Value::Array(inner)`.
  Recursing items first is what makes deep nesting cascade — inner sub-tables/opaque markers
  are already rendered strings before the outer table sees them.
- **String** → `walk_string` (`:128-148`):
  1. `try_parse_json_container(&s)` (`:153-161`): only if it starts (after trim) with `{`/`[`
     and parses to Object/Array. If so, `walk` the parsed value; result is either the rendered
     sub-table string, or `serde_json::to_string(other)` (compact JSON) if it didn't compact.
  2. Else if `classify_cell` says `Opaque(kind)` → `emit_opaque_ccr_marker(s, kind, ccr_store)`.
  3. Else leave the string unchanged.
- **scalar** → unchanged.

`emit_opaque_ccr_marker` (`walker.rs:171-194`): SHA-256 of payload bytes, first 6 bytes hex
= 12-char hash; if a store is present, `store.put(&hash, payload)`; emit
`<<ccr:{hash},{kind_str},{humanize(len)}>>`. `humanize` (`walker.rs:196-205`): `<1024` → `"{n}B"`,
`<1024 KB` → `"{:.1}KB"`, else `"{:.1}MB"`. (Identical to formatter's `humanize_bytes`, §6.4 / §8.)

`compact_document(doc)` (`walker.rs:209-211`) = `DocumentCompactor::new().compact(doc)`.

> **Output contract:** same JSON shape as input; compacted leaves become **strings** holding
> rendered bytes. (`walker.rs:16-26`.)

---

## 8. Formatters (`formatter.rs`) — output formats

`Formatter` trait (`formatter.rs:41-54`): `name()`, `format(&Compaction) -> String`,
`estimate_bytes()` (default = `format().len()`).

### 8.1 `CsvSchemaFormatter` (default) — `[N]{cols}` + CSV rows (`formatter.rs:186-373`)

**Table** (`write_table`, `:256-291`):
1. Declaration line: `[` + `rows.len()` + `]{` + comma-joined column decls + `}` + `\n`.
   - Column decl = `"{name}:{type_tag}"`, or `"{name}:{type_tag}?"` if `nullable`. (`:267-279`)
   - Optional ` __dropped:{n}` suffix iff `include_drop_summary` (default off) and rows dropped. (`:280-282`)
2. One CSV line per row: cells joined by `,` + `\n`. (`:286-290`)

**Cell rendering** (`format_cell`, `:293-309`):
- `Missing` → empty string (positional empty cell).
- `Scalar(v)` → `json_scalar_to_csv(v)` (`:338-354`):
  `Null`→empty; `Bool`→`true/false`; `Number`→`n.to_string()`; `String`→raw unless it needs
  quoting; Object/Array→CSV-quoted JSON.
- `Nested(sub)` → render sub with **`JsonFormatter`**, then **CSV-quote** the whole thing. (`:297-302`)
- `OpaqueRef` → `format_ccr_marker` (§8.4).

**CSV quoting**: `needs_csv_quote` = contains `, " \n \r` (`:356-358`). `csv_quote` wraps in
`"…"` and doubles internal `"` (RFC-4180 style) (`:360-373`).

**Buckets** (`write_compaction`, `:224-242`): line `__buckets:{discriminator}` (+ optional
` __dropped:{n}`), then per bucket: `__key:{json_scalar_to_csv(key)}\n` followed by that
bucket's table.

**OpaqueRef (top-level)** → just the marker. **Untouched** → `serde_json::to_string(v)`. (`:243-253`)

### 8.2 `JsonFormatter` (`formatter.rs:60-179`)

Single-line by default; `.pretty()` for pretty. Renders `Compaction` to structured JSON via
`compaction_to_json` (`:90-132`):
- Table → `{"_compaction":"table","_schema":[…],"_kept":N,"_total":M,"_rows":[[…],…]}`.
  Schema entries: `{"name","type"[, "nullable":true]}` (nullable omitted when false, `:142-145`).
- Buckets → `{"_compaction":"buckets","_discriminator","_total","_buckets":[{"_key","_schema","_rows"}]}`.
- OpaqueRef / cell OpaqueRef → `{"_compaction":"ccr"/"_ccr",...,"_size","_kind"}`.
- `Missing` cell → `Value::Null`; `Nested` → recurse; `Untouched` → verbatim value.

### 8.3 `MarkdownKvFormatter` (`formatter.rs:395-567`)

Same `[N]{cols}` declaration line, then **one Markdown list item per row**: first cell line
prefixed `- `, subsequent lines `  ` (two spaces), each line `"{field}: {value}"`. (`:500-528`)
- `Missing` cells **omitted entirely** (no line). All-missing row → bare `-` line so the
  rendered row count still matches `[N]`. (`:506-528`)
- `kv_scalar` (`:531-547`): `Null`→`"null"`; bool/number as-is; strings raw unless
  `needs_kv_quote` (empty, newline/CR, leading/trailing whitespace → JSON-quoted, `:549-555`).
- `kv_field_name` (`:561-567`): JSON-quote names that are pathological (`needs_kv_quote` or
  contain `": "`), both in the declaration and row lines.
- `Nested` → inline `JsonFormatter` JSON (no quoting). `OpaqueRef` → marker.

### 8.4 CCR marker format (shared, fixed across all formatters)

`format_ccr_marker(hash, byte_size, kind)` (`formatter.rs:311-324`):

```
<<ccr:{hash},{kind_str},{humanized_size}>>
```

`kind_str ∈ {base64, string, html, <other>}`. `humanize_bytes` (`formatter.rs:326-336`):
`<1024`→`"{n}B"`; `<1024 KB`→`"{kb:.1}KB"`; else `"{mb:.1}MB"`.
Example: `<<ccr:1f3a9c0e2b77,base64,2.0KB>>`.

### 8.5 Concrete example (CSV+schema, default)

**Input array:**
```json
[
  {"id": 1, "name": "alice", "status": "ok"},
  {"id": 2, "name": "bob",   "status": "ok"},
  {"id": 3, "name": "carol", "status": "fail"}
]
```
All three keys appear in 3/3 rows (≥ 80 %), so `core_ratio = 1.0` ≥ 0.6 → homogeneous Table.
Column order by descending freq (all tied at 3) then alphabetical → `id, name, status`.

**Compacted output (`CsvSchemaFormatter`):**
```
[3]{id:int,name:string,status:string}
1,alice,ok
2,bob,ok
3,carol,fail
```
(Trailing `\n` after the last row.) Test `csv_formatter_pure_tabular` (`formatter.rs:627-643`)
and walker test `top_level_array_of_objects_is_compacted` (`walker.rs:222-237`) confirm the
`[3]{...}` shape.

**Nested-uniform example** — input
`[{"id":1,"meta":{"region":"us","tier":"gold"}}, {"id":2,"meta":{"region":"eu","tier":"silver"}}]`
flattens `meta` → columns `id, meta.region, meta.tier`, output:
```
[2]{id:int,meta.region:string,meta.tier:string}
1,us,gold
2,eu,silver
```

**Bucket example** — `[{"type":"user","id":1,"name":"alice"},{"type":"user","id":2,"name":"bob"},{"type":"order","id":99,"total":50},{"type":"order","id":100,"total":75}]`
→ discriminator `type`, 2 buckets (alphabetical: `order`, `user`):
```
__buckets:type
__key:order
[2]{id:int,total:int}
99,50
100,75
__key:user
[2]{id:int,name:string}
1,alice
2,bob
```

---

## 9. Accept/reject savings gate (CALLER-SIDE — not in these files)

The brief's `lossless_min_savings_ratio = 0.15` ("byte savings vs the lossy path") is **not
implemented in the compaction subsystem**. Inside it, the only accept/reject is
`Compaction::was_compacted()` (`walker.rs:121`): if the IR is Table/Buckets/OpaqueRef, the
spot is replaced; if `Untouched`, it isn't.

The savings gate lives on `SmartCrusherConfig` (Port Spec `1_config_types_hashing.md:317,345`)
and is applied by the PR4 `crush_array` dispatch (a different module). Per spec 1's row 17:

- `lossless_min_savings_ratio: f64` default **`0.15`** (Rust-only / PR4).
- Savings ratio computed as **`1 - len(rendered) / len(input)`**.
- `0.0` = always prefer lossless; `1.0` = disable lossless (always use lossy + CCR).

**Port implication:** the lossless path produces `rendered: String`; the caller compares it
against the original serialized bytes and only keeps the lossless result if
`(1 - rendered.len() as f64 / input.len() as f64) >= lossless_min_savings_ratio`. Otherwise it
falls back to the lossy row-drop path. Implement this gate in the dispatch layer, not in
`compact()` / `DocumentCompactor`. (See §10 `savings_ratio` helper.)

---

## 10. Edge cases

| Case | Behavior | Source |
|---|---|---|
| `items.len() < 2` | `Untouched` | `compactor.rs:99-101` |
| Array contains a non-object | `Untouched` (whole array) | `compactor.rs:102-104` |
| Array of scalars (`[1,2,"x"]`) | walker leaves it unchanged (`Untouched` → `Array`) | `walker.rs:122-125`, test `array_of_scalars_left_alone` `:371-377` |
| Empty object / empty array doc | unchanged | walker tests `:380-389` |
| `total_keys == 0` | `core_ratio = 1.0` (no bucketing) | `compactor.rs:112-114` |
| No clean discriminator in hetero case | fall through to **sparse Table** (not refuse) | `compactor.rs:122-124` |
| Sub-compaction of a bucket declines | degenerate 1-col `value:json` table | `compactor.rs:475-492` |
| Stringified scalar (`"123"`, `"true"`) | `Scalar`, NOT recursed | `classifier.rs:84-95`, test `:218-224` |
| Malformed stringified JSON (`"{not valid"`) | left as plain string | walker test `:392-396` |
| Present-but-null vs absent key | `Scalar(Null)` vs `Missing` (distinct) | `ir.rs:82-86` |
| Inner array of objects len < 2 | NOT recursed (stays `Scalar` array) | `compactor.rs:204,214` |
| Inner object schema > 6 keys | NOT flattened (stays nested `meta` column) | `compactor.rs:241` |
| Mixed scalar types in a column | `type_tag = "json"` | `compactor.rs:366-370` |
| Discriminator near-unique (`distinct/total > 0.7`) | rejected | `compactor.rs:441` |

---

## 11. Suggested Rust skeleton (our crate)

Signatures the brief asked for. `compact(items, core_fraction)` returns a **lossless columnar
JSON value** (the `JsonFormatter`-shaped structured form) — the byte-for-byte reconstructable
representation — or `None` when the array isn't cleanly tabular.

```rust
use serde_json::Value;

/// Lossless columnar compaction of an array of objects.
/// Returns the structured columnar JSON (schema + rows) when the array is
/// cleanly tabular, else None (caller keeps the original / tries lossy path).
///
/// `core_fraction` = headroom's `core_field_fraction` (default 0.8): a key is
/// "core" when it appears in >= ceil(items.len() * core_fraction) rows.
pub fn compact(items: &[Value], core_fraction: f64) -> Option<Value> {
    // 1. Gate: >= 2 items AND all objects (compactor.rs:99-104).
    if items.len() < 2 || !items.iter().all(Value::is_object) {
        return None;
    }

    // 2. Key frequencies (BTreeMap<String,usize>), core_ratio.
    //    core_threshold = ceil(total * core_fraction); core key: freq >= threshold.
    //    core_ratio = core_count / total_keys (1.0 if total_keys == 0).
    //    if core_ratio < 0.6 { try discriminator bucketing (2..=8 buckets,
    //       distinct/total <= 0.7); else fall through to sparse table }.

    // 3. Build schema: columns sorted by descending freq, then ascending name.
    //    FieldSpec { name, type_tag (int/float/string/bool/null/json/ccr), nullable }.
    //    nullable = (freq < total) || any present-null.

    // 4. Build rows: per ordered key -> Missing (absent) | classify+build cell.
    //    classify: stringified-JSON (parse, recurse if array-of-objects len>=2),
    //              opaque (>256 bytes -> ccr ref; DEFERRABLE in v1), else scalar.

    // 5. flatten_uniform_nested: object columns with identical inner key sets
    //    and <= 6 inner keys -> dotted columns "{parent}.{inner}".

    // 6. Emit structured columnar JSON (JsonFormatter shape):
    //    {"_compaction":"table","_schema":[{name,type[,nullable]}],
    //     "_kept":N,"_total":N,"_rows":[[cell,...],...]}
    //    (or {"_compaction":"buckets",...}). Missing cell -> null.
    Some(/* columnar Value */ Value::Null)
}

/// Caller-side accept/reject gate (lives in the dispatch layer, NOT in compact()).
/// rendered = the textual form (CSV+schema String) produced from the columnar IR.
/// input    = original serialized bytes of the array.
/// Keep lossless only if savings >= min_ratio (default 0.15). Spec 1 row 17.
pub fn savings_ratio(input_len: usize, rendered_len: usize) -> f64 {
    if input_len == 0 { return 0.0; }
    1.0 - (rendered_len as f64) / (input_len as f64)
}

pub fn accept_lossless(input_len: usize, rendered_len: usize, min_ratio: f64) -> bool {
    savings_ratio(input_len, rendered_len) >= min_ratio
}
```

**Reconstructability note.** The columnar JSON (schema + ordered rows, `Missing`↔null
distinction, parsed-stringified-JSON un-escaping, dotted-column flattening) is fully
invertible back to the original array — *except* opaque `OpaqueRef` cells, which require the
CCR store to retrieve the original bytes. If v1 skips opaque refs (§3.3), every value stays
inline and reconstruction is byte-exact without any external store.

---

## 12. Verbatim constants index

| Constant | Value | File:line |
|---|---|---|
| `min_items` | `2` | `compactor.rs:91` |
| `core_field_fraction` | `0.8` | `compactor.rs:89` |
| `heterogeneous_core_ratio` | `0.6` (code; doc says 0.5 — stale) | `compactor.rs:89` |
| `max_flatten_inner_keys` | `6` | `compactor.rs:90` |
| `min_buckets` | `2` | `compactor.rs:92` |
| `max_buckets` | `8` | `compactor.rs:92` |
| discriminator near-unique reject | `distinct/total > 0.7` | `compactor.rs:441` |
| inner-array recurse min len | `>= 2` | `compactor.rs:204,214` |
| ccr hash length | 6 SHA-256 bytes → 12 hex chars | `compactor.rs:393-396`, `walker.rs:178-183` |
| `opaque_min_bytes` | `256` | `classifier.rs:65` |
| `base64_alphabet_ratio` | `0.95` | `classifier.rs:66` |
| `html_min_open_brackets` | `3` | `classifier.rs:67` |
| base64 min length | `64` | `classifier.rs:114` |
| base64 distinct-char min | `16` | `classifier.rs:141` |
| humanize thresholds | `1024` B/KB boundaries, `{:.1}` | `walker.rs:196-205`, `formatter.rs:326-336` |
| `lossless_min_savings_ratio` (caller) | `0.15`, `1 - rendered/input` | spec 1 `:317,345` |
