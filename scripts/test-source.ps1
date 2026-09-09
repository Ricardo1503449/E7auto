$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing project interpreter: $python"
}

Push-Location $projectRoot
try {
    & $python -m pytest --ignore=tests/test_wgc_capture.py
    if ($LASTEXITCODE -ne 0) {
        throw "Qt/non-WGC test process failed with exit code $LASTEXITCODE"
    }

    & $python -m pytest tests/test_wgc_capture.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyWinRT/WGC test process failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
