param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$BuildId,
    [Parameter(Mandatory = $true)][ValidateSet('nuitka', 'staging')][string]$Area
)
$ErrorActionPreference = 'Stop'

# Only the current build's direct child is eligible; never accept path syntax.
if ($BuildId -notmatch '^[0-9]{8}-[0-9]{6}-[a-z0-9-]+-[a-f0-9]{8}$') {
    throw 'Invalid build ID for cleanup'
}
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
$buildRoot = Join-Path $root 'build'
$areaRoot = Join-Path $buildRoot $Area
$target = Join-Path $areaRoot $BuildId
foreach ($path in @($root, $buildRoot, $areaRoot, $target)) {
    $item = Get-Item -LiteralPath $path -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Refusing linked or non-directory cleanup path: $path"
    }
}
$resolvedBase = (Resolve-Path -LiteralPath $areaRoot).Path
$resolvedTarget = (Resolve-Path -LiteralPath $target).Path
if (-not [string]::Equals((Split-Path -Parent $resolvedTarget), $resolvedBase,
        [StringComparison]::OrdinalIgnoreCase) -or
    -not [string]::Equals((Split-Path -Leaf $resolvedTarget), $BuildId,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Cleanup target is outside the current build directory'
}
# Walk one level at a time so a junction is rejected before traversing it.
$pending = [Collections.Generic.Stack[string]]::new()
$pending.Push($resolvedTarget)
while ($pending.Count -gt 0) {
    foreach ($item in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing linked build output: $($item.FullName)"
        }
        if ($item.PSIsContainer) { $pending.Push($item.FullName) }
    }
}
Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
Write-Host "Removed current build output: $resolvedTarget"
