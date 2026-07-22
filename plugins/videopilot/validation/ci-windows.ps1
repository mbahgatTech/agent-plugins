[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if ($env:CI -ne "true") {
    [Console]::Error.WriteLine(
        "This hook installs prerequisites only on a disposable CI runner."
    )
    exit 2
}

$pluginRoot = if ($env:MARKETPLACE_PLUGIN_ROOT) {
    $env:MARKETPLACE_PLUGIN_ROOT
} else {
    Split-Path -Parent $PSScriptRoot
}
$installer = Join-Path $pluginRoot "skills/init/scripts/install-prereqs.ps1"

function Assert-ExitCode {
    [CmdletBinding()]
    param(
        [string]$Label,
        [int]$Expected,
        [string[]]$Arguments
    )

    $process = Start-Process pwsh -NoNewWindow -Wait -PassThru -ArgumentList $Arguments
    if ($process.ExitCode -ne $Expected) {
        [Console]::Error.WriteLine(
            "$Label`: expected exit code $Expected, got $($process.ExitCode)"
        )
        exit 1
    }
    Write-Output "$Label`: observed expected exit code $Expected"
}

Write-Output "Installing VideoPilot CI prerequisites."
if ($null -eq (Get-Command uvx -ErrorAction SilentlyContinue)) {
    & python -m pip install --disable-pip-version-check uv
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
if (
    $null -eq (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or
    $null -eq (Get-Command ffprobe -ErrorAction SilentlyContinue)
) {
    & choco install ffmpeg -y --no-progress
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Output "Checking prerequisite script safe default."
$defaultOutput = & pwsh -NoProfile -File $installer
$defaultCode = $LASTEXITCODE
$defaultOutput
if ($defaultCode -ne 0 -or $defaultOutput -match "^Running:") {
    [Console]::Error.WriteLine(
        "The default prerequisite check failed or attempted a mutation."
    )
    exit 1
}

Write-Output "Checking Windows dry-run setup."
$env:VIDEOPILOT_TEST_MISSING = "uv,uvx,ffmpeg,ffprobe"
Assert-ExitCode -Label "Windows dry-run" -Expected 0 -Arguments @(
    "-NoProfile",
    "-File", $installer,
    "-Install",
    "-DryRun"
)

Write-Output "Checking noninteractive setup without approval."
$env:VIDEOPILOT_NONINTERACTIVE = "1"
Assert-ExitCode -Label "noninteractive without approval" -Expected 2 -Arguments @(
    "-NoProfile",
    "-File", $installer,
    "-Install"
)
Remove-Item Env:VIDEOPILOT_NONINTERACTIVE

Write-Output "Checking unsupported Windows setup."
$env:VIDEOPILOT_TEST_PLATFORM = "linux"
Assert-ExitCode -Label "unsupported platform" -Expected 3 -Arguments @(
    "-NoProfile",
    "-File", $installer
)
Remove-Item Env:VIDEOPILOT_TEST_PLATFORM
Remove-Item Env:VIDEOPILOT_TEST_MISSING

Write-Output "Prewarming: uvx --from videopilot==0.1.7 videopilot-mcp --version"
& uvx --from videopilot==0.1.7 videopilot-mcp --version
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output "Running: uvx --from videopilot==0.1.7 videopilot doctor"
& uvx --from videopilot==0.1.7 videopilot doctor
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
