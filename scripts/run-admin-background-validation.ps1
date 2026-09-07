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
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$validator = Join-Path $PSScriptRoot "validate_background_mode.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing project interpreter: $python"
}
if (-not (Test-Path -LiteralPath $validator -PathType Leaf)) {
    throw "Missing background validator: $validator"
}

$acknowledgement = if ($Mode -eq "navigation") {
    "--acknowledge-main-screen-covered"
} else {
    "--acknowledge-shop-top-covered"
}
$arguments = @(
    $validator,
    $Mode,
    $acknowledgement,
    "--capture-backend",
    $CaptureBackend
)
if ($Mode -eq "scroll") {
    $arguments += @("--effect-observation-ms", $EffectObservationMs)
}
$process = Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -Verb RunAs `
    -Wait `
    -PassThru
exit $process.ExitCode
