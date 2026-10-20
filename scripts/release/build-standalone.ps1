param([string]$IsccPath = "")
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing project interpreter: $python"
}
if (-not $IsccPath) {
    Push-Location $projectRoot
    try {
        $IsccPath = & $python -B -c "from scripts.common.paths import development_tool_entry; print(development_tool_entry('innosetup'))"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve registered Inno Setup tool.' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Registered Inno Setup 6.7.3 is missing; prepare tools\innosetup\6.7.3 or specify -IsccPath.'
}
$IsccPath = (Resolve-Path -LiteralPath $IsccPath).Path
@'
#if VER != 0x06070300
#error Inno Setup 6.7.3 is required
#endif
[Setup]
AppName=Compiler version check
AppVersion=1
; /O- disables output; use the existing directory to avoid creating Output.
OutputDir=.
DefaultDirName={tmp}\E7autoCompilerCheck
Uninstallable=no
'@ | & $IsccPath /Q /O- -
if ($LASTEXITCODE -ne 0) { throw 'This build requires Inno Setup 6.7.3.' }
$usageFileName = (([char]0x4F7F, [char]0x7528, [char]0x8BF4, [char]0x660E) -join '') + ".md"
$usageGuide = Join-Path (Join-Path $projectRoot "docs\user") $usageFileName
if (-not (Test-Path -LiteralPath $usageGuide -PathType Leaf)) {
    throw "Missing packaged usage guide: $usageGuide"
}
$usageGuideInclude = "$usageGuide=$usageFileName"
$sourceConfig = Join-Path $projectRoot "config\internal.yaml"
if (-not (Test-Path -LiteralPath $sourceConfig -PathType Leaf)) {
    throw "Missing source configuration: $sourceConfig"
}
$distDir = Join-Path $projectRoot "dist"
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
$setupName = "E7auto_v${version}_Setup_x64.exe"
$releaseInstaller = Join-Path $distDir $setupName
$uiAssetDir = Join-Path $projectRoot "assets\ui"
$appIcon = Join-Path $uiAssetDir "e7auto.ico"
if (-not (Test-Path -LiteralPath $appIcon -PathType Leaf)) {
    throw "Missing application icon: $appIcon"
}
if (-not (Test-Path -LiteralPath (Join-Path $uiAssetDir "shop-card-background.png") -PathType Leaf)) {
    throw "Missing shop card background"
}

# Every build has one managed output owner; logs and evidence stay outside build/.
Push-Location $projectRoot
try {
    & $python -B -m scripts.release.workflow preflight
    if ($LASTEXITCODE -ne 0) { throw "Synchronize version and documents before building" }
    & $python -B -m scripts.verify_environment
    if ($LASTEXITCODE -ne 0) { throw "Source environment verification failed" }
    $runDirectory = & $python -B -m scripts.project.artifacts --kind releases --version $version --subject installer
    if ($LASTEXITCODE -ne 0) { throw "Unable to allocate release output" }
} finally { Pop-Location }
$buildId = Split-Path -Leaf $runDirectory
$buildDir = Join-Path $projectRoot "build\nuitka\$buildId"
$stagingDir = Join-Path $projectRoot "build\staging\$buildId"
New-Item -ItemType Directory -Path $buildDir, $stagingDir -Force | Out-Null
$releaseConfig = Join-Path $stagingDir "internal.release.yaml"
$releaseDir = Join-Path $buildDir 'launcher.dist'
$setupOutput = Join-Path $stagingDir 'setup'
$temporaryInstaller = Join-Path $setupOutput $setupName
$buildSucceeded = $false
$configText = [IO.File]::ReadAllText($sourceConfig)
[IO.File]::WriteAllText($releaseConfig, $configText, [Text.UTF8Encoding]::new($false))

Push-Location $projectRoot
try {
    $env:NUITKA_CACHE_DIR = & $python -B -c "from scripts.common.paths import development_tool_path; print(development_tool_path('nuitka', kind='caches'))"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve registered Nuitka cache.' }
    & $python -B -m scripts.release.workflow capture --run-dir $runDirectory
    if ($LASTEXITCODE -ne 0) { throw "Unable to capture build inputs" }
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
    # Windows PowerShell 5 represents native stderr as ErrorRecord objects,
    # including Nuitka's normal progress messages. Judge failure by exit code.
    $ErrorActionPreference = 'Continue'
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
        "--output-dir=$buildDir" `
        --output-filename=E7auto.exe `
        --include-package=e7auto `
        --include-package=winrt.windows.foundation `
        --include-module=winrt._winrt_windows_foundation `
        --include-data-file=$releaseConfig=config/internal.yaml `
        --include-data-file=config/chaos.yaml=config/chaos.yaml `
        --include-data-dir=assets/templates=assets/templates `
        --include-data-dir=assets/ui=assets/ui `
        --include-data-file=$usageGuideInclude `
        --noinclude-dlls=cv2/opencv_videoio_ffmpeg*.dll `
        --noinclude-dlls=PySide6/qt-plugins/imageformats/qpdf.dll `
        --noinclude-dlls=qt6pdf.dll `
        --assume-yes-for-downloads `
        launcher.py 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath (Join-Path $runDirectory "build.log")
      $compileExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = 'Stop' }
    if ($compileExit -ne 0) {
        throw "Nuitka standalone build failed with exit code $compileExit"
    }

    # Retain the payload through sealing; clean this build only after publication.
    $compiledRelease = (Resolve-Path -LiteralPath (Join-Path $buildDir "launcher.dist")).Path
    if (-not $compiledRelease.StartsWith([IO.Path]::GetFullPath($buildDir) + '\')) {
        throw 'Compiled payload is outside the configured build directory'
    }
    & $python -B -m scripts.release.native_runtime --directory $compiledRelease
    if ($LASTEXITCODE -ne 0) {
        throw "Compiled MSVC runtime consistency check failed"
    }
    & $python -B -m scripts.release.workflow prepare --run-dir $runDirectory --directory $compiledRelease
    if ($LASTEXITCODE -ne 0) { throw 'Unable to freeze installer payload' }
    $ErrorActionPreference = 'Continue'
    try {
      & $IsccPath /Qp "/DAppVersion=$version" "/DPayloadDir=$compiledRelease" "/DSetupOutputDir=$setupOutput" `
        (Join-Path $PSScriptRoot 'e7auto.iss') 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath (Join-Path $runDirectory 'installer-build.log')
      $installerExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = 'Stop' }
    if ($installerExit -ne 0) { throw "Inno Setup compilation failed: $installerExit" }
    & $python -B -m scripts.release.workflow seal --run-dir $runDirectory --installer $temporaryInstaller --directory $compiledRelease
    if ($LASTEXITCODE -ne 0) { throw 'Build inputs or payload changed during installer compilation' }
    Move-Item -LiteralPath $temporaryInstaller -Destination $releaseInstaller -Force
    $buildSucceeded = $true
}
finally {
    $cleanupResults = @{}
    foreach ($area in @('nuitka', 'staging')) {
        $cleanupResults[$area] = @{ status = 'retained_after_failure'; error = $null }
    }
    if ($buildSucceeded) {
        foreach ($area in @('nuitka', 'staging')) {
            try {
                & (Join-Path $PSScriptRoot 'cleanup-build.ps1') -ProjectRoot $projectRoot -BuildId $buildId -Area $area
                $cleanupResults[$area].status = 'removed'
            } catch {
                $cleanupResults[$area].status = 'failed'
                $cleanupResults[$area].error = $_.Exception.Message
                Write-Warning "Installer built successfully; $area cleanup failed: $($_.Exception.Message)"
            }
        }
    }
    $recordPath = Join-Path $runDirectory "task.json"
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $record.status = if ($buildSucceeded) { "complete" } else { "failed" }
    $record | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $recordPath -Encoding utf8
    [ordered]@{ succeeded = $buildSucceeded; build_id = $buildId; version = $version;
        release_directory = $releaseDir; installer = $releaseInstaller;
        nuitka_cleanup = $cleanupResults['nuitka'].status; nuitka_cleanup_error = $cleanupResults['nuitka'].error;
        staging_cleanup = $cleanupResults['staging'].status; staging_cleanup_error = $cleanupResults['staging'].error;
        inno_version = '6.7.3'; compiler_sha256 = (Get-FileHash -LiteralPath $IsccPath -Algorithm SHA256).Hash.ToLowerInvariant() } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runDirectory "build-result.json") -Encoding utf8
    Write-Host "Release record directory: $runDirectory"
    Pop-Location
}
