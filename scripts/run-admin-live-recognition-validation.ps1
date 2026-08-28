$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$validator = Join-Path $projectRoot "scripts\validate_live_recognition.py"
$resultPath = Join-Path $projectRoot "logs\live-recognition-timing-admin.json"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing project interpreter: $python"
}
if (-not (Test-Path -LiteralPath $validator)) {
    throw "Missing live recognition validator: $validator"
}

$arguments = @(
    $validator,
    "--acknowledge-top-state",
    "--sample-count", "8",
    "--interval-ms", "100",
    "--scroll-interval-ms", "100",
    "--settle-ms", "800",
    "--result-path", $resultPath
)

$process = Start-Process `
    -FilePath $python `
    -Verb RunAs `
    -ArgumentList $arguments `
    -WindowStyle Hidden `
    -Wait `
    -PassThru

if (Test-Path -LiteralPath $resultPath) {
    Get-Content -Raw -LiteralPath $resultPath
}
else {
    throw "Elevated recognition validator produced no result file (exit code $($process.ExitCode))."
}

exit $process.ExitCode
