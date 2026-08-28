$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$validator = Join-Path $projectRoot "scripts\validate_live_scroll_once.py"
$resultPath = Join-Path $projectRoot "logs\live-scroll-validation-admin.json"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing project interpreter: $python"
}
if (-not (Test-Path -LiteralPath $validator)) {
    throw "Missing live validator: $validator"
}

$arguments = @(
    $validator,
    "--acknowledge-real-input",
    "--delta", "-120",
    "--repetitions", "6",
    "--interval-ms", "100",
    "--settle-ms", "800",
    "--wait-for-foreground-ms", "30000",
    "--focus-game",
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
    throw "Elevated validator produced no result file (exit code $($process.ExitCode))."
}

exit $process.ExitCode
