$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing project interpreter: $python"
}

Push-Location $projectRoot
try {
    # The WGC entry point selects the Qt-compatible MSVC runtime before PyWinRT.
    # Keep Qt and WGC together so import-order regressions cannot hide behind isolation.
    & $python -m pytest
    if ($LASTEXITCODE -ne 0) {
        throw "Source test process failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
