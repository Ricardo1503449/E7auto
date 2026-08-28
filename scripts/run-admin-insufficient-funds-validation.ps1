$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$validator = Join-Path $projectRoot "scripts\validate_insufficient_funds.py"
$resultPath = Join-Path $projectRoot "logs\insufficient-funds-live-validation.json"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing project interpreter: $python"
}
if (-not (Test-Path -LiteralPath $validator)) {
    throw "Missing insufficient-funds validator: $validator"
}

$arguments = @(
    $validator,
    "--acknowledge-prompt-visible",
    "--sample-count", "5",
    "--interval-ms", "100",
    "--foreground-wait-seconds", "30",
    "--result-path", $resultPath
)

$startedAt = Get-Date
$process = Start-Process `
    -FilePath $python `
    -Verb RunAs `
    -ArgumentList $arguments `
    -WindowStyle Hidden `
    -Wait `
    -PassThru

if (
    (Test-Path -LiteralPath $resultPath) -and
    (Get-Item -LiteralPath $resultPath).LastWriteTime -ge $startedAt
) {
    Get-Content -Raw -LiteralPath $resultPath
}
else {
    throw "Elevated insufficient-funds validator produced no fresh result file (exit code $($process.ExitCode))."
}

exit $process.ExitCode
