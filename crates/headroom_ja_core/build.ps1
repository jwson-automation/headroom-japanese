# Build the Rust core and install it into the active Python (no venv needed).
# Usage:  pwsh crates/headroom_ja_core/build.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mat = "$env:APPDATA\Python\Python313\Scripts\maturin.exe"
if (-not (Test-Path $mat)) { $mat = "maturin" }
Push-Location $here
try {
    & $mat build --release
    $whl = (Get-ChildItem "target\wheels\*.whl" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
    pip install --force-reinstall --no-deps $whl
    python -c "import headroom_ja_core as c; print('built:', c.ping())"
} finally { Pop-Location }
