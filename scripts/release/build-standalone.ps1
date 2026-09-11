$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
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
$distDir = Join-Path $projectRoot "dist"
$releaseConfig = Join-Path $distDir "internal.release.yaml"
$pyproject = Join-Path $projectRoot "pyproject.toml"
$versionMatch = [regex]::Match(
    [IO.File]::ReadAllText($pyproject),
    '(?m)^version\s*=\s*"(?<version>[^"\r\n]+)"\s*$'
)
if (-not $versionMatch.Success) {
    throw "Unable to read the release version from: $pyproject"
}
$version = $versionMatch.Groups["version"].Value
if ($version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "Unsafe release version for archive filename: $version"
}
$coreVersionMatch = [regex]::Match(
    $version,
    '^(?<major>\d+)\.(?<minor>\d+)\.(?<patch>\d+)'
)
if (-not $coreVersionMatch.Success) {
    throw "Unable to derive numeric Windows version from: $version"
}
$windowsVersion = "{0}.{1}.{2}.0" -f (
    $coreVersionMatch.Groups["major"].Value,
    $coreVersionMatch.Groups["minor"].Value,
    $coreVersionMatch.Groups["patch"].Value
)
$releaseZip = Join-Path $distDir "E7auto_v${version}_x64.zip"
$temporaryReleaseZip = Join-Path $distDir ".E7auto_v${version}_x64.building.zip"
$uiAssetDir = Join-Path $projectRoot "assets\ui"
$appIcon = Join-Path $uiAssetDir "e7auto.ico"
if (-not (Test-Path -LiteralPath $appIcon -PathType Leaf)) {
    throw "Missing application icon: $appIcon"
}
if (-not (Test-Path -LiteralPath (Join-Path $uiAssetDir "shop-card-background.png") -PathType Leaf)) {
    throw "Missing shop card background"
}

New-Item -ItemType Directory -Path $distDir -Force | Out-Null

# Never carry runtime output from a previous build into the release.
$releaseDir = Join-Path $projectRoot "dist\launcher.dist"
if (Test-Path -LiteralPath $releaseDir) {
    $resolvedReleaseDir = [IO.Path]::GetFullPath($releaseDir)
    if ([IO.Path]::GetDirectoryName($resolvedReleaseDir) -ne $distDir) {
        throw "Refusing to remove build output outside dist: $resolvedReleaseDir"
    }
    Remove-Item -LiteralPath $resolvedReleaseDir -Recurse -Force
}
$configText = [IO.File]::ReadAllText($sourceConfig)
[IO.File]::WriteAllText($releaseConfig, $configText, [Text.UTF8Encoding]::new($false))

$env:NUITKA_CACHE_DIR = Join-Path $projectRoot ".nuitka-cache"
Push-Location $projectRoot
try {
    & $python -m nuitka `
        --mode=standalone `
        --enable-plugin=pyside6 `
        --windows-uac-admin `
        --windows-console-mode=attach `
        --windows-icon-from-ico=$appIcon `
        --product-name=E7auto `
        "--file-description=E7auto Windows x64 shop automation" `
        "--file-version=$windowsVersion" `
        "--product-version=$windowsVersion" `
        --output-dir=dist `
        --output-filename=E7auto.exe `
        --include-package=e7auto `
        --include-package=winrt.windows.foundation `
        --include-module=winrt._winrt_windows_foundation `
        --include-data-file=$releaseConfig=config/internal.yaml `
        --include-data-dir=assets/templates=assets/templates `
        --include-data-dir=assets/ui=assets/ui `
        --include-data-file=$usageGuideInclude `
        --noinclude-dlls=cv2/opencv_videoio_ffmpeg*.dll `
        --noinclude-dlls=PySide6/qt-plugins/imageformats/qpdf.dll `
        --noinclude-dlls=qt6pdf.dll `
        --assume-yes-for-downloads `
        launcher.py
    if ($LASTEXITCODE -ne 0) {
        throw "Nuitka standalone build failed with exit code $LASTEXITCODE"
    }

    Remove-Item -LiteralPath $temporaryReleaseZip -Force -ErrorAction SilentlyContinue
    Compress-Archive `
        -LiteralPath (Get-ChildItem -LiteralPath $releaseDir -Force).FullName `
        -DestinationPath $temporaryReleaseZip `
        -CompressionLevel Optimal
    Move-Item -LiteralPath $temporaryReleaseZip -Destination $releaseZip -Force

    # Retain the newly completed archive only. Failed builds/archives never reach this cleanup.
    $oldReleaseZips = Get-ChildItem -LiteralPath $distDir -File | Where-Object {
        $_.Name -match '^E7auto_v.+_x64\.zip$' -and
        $_.FullName -ne $releaseZip
    }
    foreach ($oldReleaseZip in $oldReleaseZips) {
        $resolvedOldReleaseZip = [IO.Path]::GetFullPath($oldReleaseZip.FullName)
        if ([IO.Path]::GetDirectoryName($resolvedOldReleaseZip) -ne $distDir) {
            throw "Refusing to remove archive outside dist: $resolvedOldReleaseZip"
        }
        Remove-Item -LiteralPath $resolvedOldReleaseZip -Force
    }
}
finally {
    Remove-Item -LiteralPath $releaseConfig -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $temporaryReleaseZip -Force -ErrorAction SilentlyContinue
    Pop-Location
}
