param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("capture", "scroll", "navigation")]
    [string]$Mode,

    [ValidateSet("wgc")]
    [string]$CaptureBackend = "wgc",

    [ValidateRange(800, 30000)]
    [int]$EffectObservationMs = 800
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$validator = Join-Path $PSScriptRoot "validate_background_mode.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing project interpreter: $python"
}
if (-not (Test-Path -LiteralPath $validator -PathType Leaf)) {
    throw "Missing background validator: $validator"
}

Push-Location $projectRoot
try {
    $runDirectory = & $python -B -m scripts.project.artifacts --kind tasks --feature platform --subject "background-$Mode-validation"
    if ($LASTEXITCODE -ne 0) { throw "Unable to allocate background validation output" }
} finally { Pop-Location }
$resultPath = Join-Path $runDirectory "results\background-$Mode-$CaptureBackend-validation.json"
Write-Output "Validation result: $resultPath"

$acknowledgement = if ($Mode -eq "navigation") {
    "--acknowledge-main-screen-covered"
} else {
    "--acknowledge-shop-top-covered"
}
$arguments = @(
    "-m", "scripts.validation.validate_background_mode",
    $Mode,
    $acknowledgement,
    "--capture-backend",
    $CaptureBackend,
    "--result-path", ('"{0}"' -f $resultPath)
)
if ($Mode -eq "scroll") {
    $arguments += @("--effect-observation-ms", $EffectObservationMs)
}
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -Verb RunAs `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if (Test-Path -LiteralPath $resultPath -PathType Leaf) {
    Get-Content -LiteralPath $resultPath -Raw
} else {
    throw "Background validator produced no result file (exit code $($process.ExitCode))."
}
exit $process.ExitCode
