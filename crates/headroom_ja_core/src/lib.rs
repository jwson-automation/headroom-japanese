//! Rust core for headroom-japanese — faithful port of headroom's smart_crusher
//! index-selection (see PORT_SPECS/4_orchestration_crusher.md and 2_statistics_outliers.md).
//!
//! Seam: error detection and query relevance are computed in Python (Japanese
//! tokenizer/keywords) and passed in as `is_error: [bool]` and `relevance: [f32]`.
//! Everything statistical (dedup, structural/rare-value outliers, prioritization)
//! is ported here.

use md5::{Digest, Md5};
use pyo3::prelude::*;
use serde_json::{Map, Value};
use std::collections::{BTreeSet, HashMap, HashSet};

/// Canonical JSON with sorted object keys (compact). Used for the dedup content
/// hash. NOTE: not yet byte-parity with headroom's python_json_dumps_sort_keys
/// (which uses ", "/": " separators + ensure_ascii); consistent within our system.
fn canon(v: &Value, out: &mut String) {
    match v {
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(k).unwrap());
                out.push(':');
                canon(&m[*k], out);
            }
            out.push('}');
        }
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                canon(x, out);
            }
            out.push(']');
        }
        other => out.push_str(&serde_json::to_string(other).unwrap()),
    }
}

/// MD5 of the canonical form, first 16 hex chars (matches headroom's [:16]).
fn content_hash(v: &Value) -> String {
    let mut s = String::new();
    canon(v, &mut s);
    let mut h = Md5::new();
    h.update(s.as_bytes());
    let hex = format!("{:x}", h.finalize());
    hex[..16].to_string()
}

/// orchestration.rs::deduplicate_indices_by_content — lowest index per hash.
fn dedup(indices: &BTreeSet<usize>, items: &[Value]) -> BTreeSet<usize> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut out = BTreeSet::new();
    for &i in indices {
        if i >= items.len() {
            continue;
        }
        let h = content_hash(&items[i]);
        if seen.insert(h) {
            out.insert(i);
        }
    }
    out
}

/// orchestration.rs::fill_remaining_slots — interleaved stride sampling of
/// content-unique items up to effective_max.
fn fill_remaining(
    keep: &BTreeSet<usize>,
    items: &[Value],
    n: usize,
    effective_max: usize,
) -> BTreeSet<usize> {
    let remaining = effective_max.saturating_sub(keep.len());
    if remaining == 0 {
        return keep.clone();
    }
    let mut seen: HashSet<String> = HashSet::new();
    for &i in keep {
        if i < n {
            seen.insert(content_hash(&items[i]));
        }
    }
    let candidates: Vec<usize> = (0..n).filter(|i| !keep.contains(i)).collect();
    if candidates.is_empty() {
        return keep.clone();
    }
    let mut result = keep.clone();
    let step = std::cmp::max(1, candidates.len() / (remaining + 1));
    let mut added = 0usize;
    'outer: for start_offset in 0..step {
        if added >= remaining {
            break;
        }
        let mut i = start_offset;
        while i < candidates.len() {
            if added >= remaining {
                break 'outer;
            }
            let idx = candidates[i];
            let h = content_hash(&items[idx]);
            if !seen.contains(&h) {
                result.insert(idx);
                seen.insert(h);
                added += 1;
            }
            i += step;
        }
    }
    result
}

fn objects(items: &[Value]) -> Vec<(usize, &Map<String, Value>)> {
    items
        .iter()
        .enumerate()
        .filter_map(|(i, v)| v.as_object().map(|m| (i, m)))
        .collect()
}

/// outliers.rs — items carrying a RARE field (present in < 20% of objects).
fn structural_outliers(items: &[Value], core_fraction: f64) -> BTreeSet<usize> {
    let objs = objects(items);
    let n = objs.len();
    let mut out = BTreeSet::new();
    if n < 3 {
        return out;
    }
    let mut freq: HashMap<&str, usize> = HashMap::new();
    for (_, m) in &objs {
        for k in m.keys() {
            *freq.entry(k.as_str()).or_insert(0) += 1;
        }
    }
    let rare_cut = (1.0 - core_fraction) * n as f64; // <20% with core_fraction=0.8
    let rare: HashSet<&str> = freq
        .iter()
        .filter(|(_, &c)| (c as f64) < rare_cut)
        .map(|(&k, _)| k)
        .collect();
    for (i, m) in &objs {
        if m.keys().any(|k| rare.contains(k.as_str())) {
            out.insert(*i);
        }
    }
    out
}

/// outliers.rs::detect_rare_status_values — Pareto rare categorical/bool VALUES.
fn rare_value_outliers(items: &[Value], max_distinct: usize) -> BTreeSet<usize> {
    let objs = objects(items);
    let n = objs.len();
    let mut out = BTreeSet::new();
    if n < 5 {
        return out;
    }
    // field -> value-string -> indices
    let mut by_key: HashMap<&str, HashMap<String, Vec<usize>>> = HashMap::new();
    for (i, m) in &objs {
        for (k, v) in m.iter() {
            let key = match v {
                Value::String(s) => Some(s.clone()),
                Value::Bool(b) => Some(b.to_string()),
                _ => None,
            };
            if let Some(val) = key {
                by_key
                    .entry(k.as_str())
                    .or_default()
                    .entry(val)
                    .or_default()
                    .push(*i);
            }
        }
    }
    for (_, vmap) in by_key {
        let distinct = vmap.len();
        if distinct < 2 || distinct > max_distinct {
            continue;
        }
        let total: usize = vmap.values().map(|v| v.len()).sum();
        let covering = ((total as f64) * 0.8).ceil() as usize;
        // counts descending
        let mut counts: Vec<(&String, usize)> =
            vmap.iter().map(|(s, idxs)| (s, idxs.len())).collect();
        counts.sort_by(|a, b| b.1.cmp(&a.1));
        let mut acc = 0usize;
        let mut k = 0usize;
        for (_, c) in &counts {
            acc += c;
            k += 1;
            if acc >= covering {
                break;
            }
        }
        if k <= 5 {
            // long-tail values (beyond the top-k) are rare -> keep their items
            for (val, c) in counts.iter().skip(k) {
                let _ = c;
                for &i in &vmap[*val] {
                    out.insert(i);
                }
            }
        }
    }
    out
}

/// orchestration.rs::prioritize_indices — the full pipeline.
fn prioritize(
    items: &[Value],
    n: usize,
    effective_max: usize,
    is_error: &[bool],
    relevance: &[f64],
    rel_threshold: f64,
    core_fraction: f64,
    rare_max_distinct: usize,
) -> BTreeSet<usize> {
    let all: BTreeSet<usize> = (0..n).collect();

    // Step 1: dedup
    let mut current = dedup(&all, items);
    // Step 2: fill
    if current.len() < effective_max && current.len() < n {
        current = fill_remaining(&current, items, n, effective_max);
    }
    // Step 3: under budget -> done
    if current.len() <= effective_max {
        return current;
    }

    // Step 4: over budget -> critical-first
    let mut prioritized: BTreeSet<usize> = BTreeSet::new();
    // 4a critical union (errors[seam] u structural u rare-value u relevant[seam])
    for i in 0..n {
        if i < is_error.len() && is_error[i] {
            prioritized.insert(i);
        }
        if i < relevance.len() && relevance[i] >= rel_threshold {
            prioritized.insert(i);
        }
    }
    prioritized.extend(structural_outliers(items, core_fraction));
    prioritized.extend(rare_value_outliers(items, rare_max_distinct));

    // 4b first-3 / last-2 anchors if room
    let mut remaining = effective_max.saturating_sub(prioritized.len());
    if remaining > 0 {
        for i in 0..std::cmp::min(3, n) {
            if remaining > 0 && !prioritized.contains(&i) {
                prioritized.insert(i);
                remaining -= 1;
            }
        }
        let last_start = n.saturating_sub(2);
        for i in last_start..n {
            if remaining > 0 && !prioritized.contains(&i) {
                prioritized.insert(i);
                remaining -= 1;
            }
        }
    }
    // 4c fill ascending from current \ prioritized
    if remaining > 0 {
        for i in current.iter() {
            if remaining == 0 {
                break;
            }
            if !prioritized.contains(i) {
                prioritized.insert(*i);
                remaining -= 1;
            }
        }
    }
    prioritized
}

/// Select which item indices to keep / drop. The Japanese seam (is_error,
/// relevance) is supplied by Python; the statistical core is ported from headroom.
#[pyfunction]
#[pyo3(signature = (items_json, is_error, relevance, min_items=5, max_items=15,
                    core_fraction=0.8, relevance_threshold=0.3, rare_max_distinct=50))]
#[allow(clippy::too_many_arguments)]
fn crush_indices(
    items_json: &str,
    is_error: Vec<bool>,
    relevance: Vec<f64>,
    min_items: usize,
    max_items: usize,
    core_fraction: f64,
    relevance_threshold: f64,
    rare_max_distinct: usize,
) -> PyResult<(Vec<usize>, Vec<usize>)> {
    let items: Vec<Value> = serde_json::from_str(items_json)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("invalid JSON: {e}")))?;
    let n = items.len();
    if n < min_items {
        return Ok(((0..n).collect(), Vec::new()));
    }
    let keep = prioritize(
        &items,
        n,
        max_items,
        &is_error,
        &relevance,
        relevance_threshold,
        core_fraction,
        rare_max_distinct,
    );
    let keep_vec: Vec<usize> = keep.iter().copied().collect();
    let dropped: Vec<usize> = (0..n).filter(|i| !keep.contains(i)).collect();
    Ok((keep_vec, dropped))
}

#[pyfunction]
fn ping() -> String {
    "headroom_ja_core ok".to_string()
}

#[pymodule]
fn headroom_ja_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ping, m)?)?;
    m.add_function(wrap_pyfunction!(crush_indices, m)?)?;
    Ok(())
}
