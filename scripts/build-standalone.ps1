$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing project interpreter: $python"
}
$usageFileName = (([char]0x4F7F, [char]0x7528, [char]0x8BF4, [char]0x660E) -join '') + ".txt"
$usageGuide = Join-Path (Join-Path $projectRoot "docs") $usageFileName
if (-not (Test-Path -LiteralPath $usageGuide -PathType Leaf)) {
    throw "Missing packaged usage guide: $usageGuide"
}
$usageGuideInclude = "$usageGuide=$usageFileName"
$sourceConfig = Join-Path $projectRoot "config\internal.yaml"
if (-not (Test-Path -LiteralPath $sourceConfig -PathType Leaf)) {
    throw "Missing source configuration: $sourceConfig"
}
$releaseConfig = Join-Path $projectRoot "dist\internal.release.yaml"

New-Item -ItemType Directory -Path (Join-Path $projectRoot "dist") -Force | Out-Null

# Never carry runtime output from a previous build into the release.
$releaseDir = Join-Path $projectRoot "dist\launcher.dist"
if (Test-Path -LiteralPath $releaseDir) {
    Remove-Item -LiteralPath $releaseDir -Recurse -Force
}
$configText = [IO.File]::ReadAllText($sourceConfig)
$configText = $configText -replace '(?m)^  profile:\s*\S+\s*$', '  profile: compact'
$configText = $configText -replace '(?m)^  keep_days:\s*\d+\s*$', '  keep_days: 7'
$configText = $configText -replace '(?m)^  keep_files:\s*\d+\s*$', '  keep_files: 5'
$configText = $configText -replace '(?m)^  max_file_mb:\s*\d+\s*$', '  max_file_mb: 2'
[IO.File]::WriteAllText($releaseConfig, $configText, [Text.UTF8Encoding]::new($false))

$env:NUITKA_CACHE_DIR = Join-Path $projectRoot ".nuitka-cache"
Push-Location $projectRoot
try {
    & $python -m nuitka `
        --mode=standalone `
        --enable-plugin=pyside6 `
        --windows-uac-admin `
        --windows-console-mode=attach `
        --output-dir=dist `
        --output-filename=E7auto.exe `
        --include-package=e7auto `
        --include-data-file=$releaseConfig=config/internal.yaml `
        --include-data-dir=assets/templates=assets/templates `
        --include-data-file=$usageGuideInclude `
        --assume-yes-for-downloads `
        launcher.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka standalone build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $releaseConfig -Force -ErrorAction SilentlyContinue
    Pop-Location
}
