//! Rust core for headroom-japanese. Phase 0: prove the PyO3/maturin build works
//! end-to-end before porting the smart_crusher logic from headroom's Rust source.

use pyo3::prelude::*;

/// Build-pipeline smoke test.
#[pyfunction]
fn ping() -> String {
    "headroom_ja_core ok".to_string()
}

#[pymodule]
fn headroom_ja_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ping, m)?)?;
    Ok(())
}
