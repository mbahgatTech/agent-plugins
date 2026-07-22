[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RequiredCommands = @("uv", "uvx", "ffmpeg", "ffprobe")

function Get-MissingCommands {
    [CmdletBinding()]
    param()

    $missing = @()
    foreach ($name in $RequiredCommands) {
        $testMissing = ",$($env:VIDEOPILOT_TEST_MISSING),"
        if ($testMissing -like "*,$name,*") {
            Write-Host "[missing] $name"
            $missing += $name
            continue
        }
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            Write-Host "[missing] $name"
            $missing += $name
            continue
        }

        $version = (& $name --version 2>&1 | Select-Object -First 1)
        Write-Host "[found] $name - $version"
    }
    return $missing
}

function Get-InstallCommands {
    [CmdletBinding()]
    param([string[]]$Missing)

    $commands = @()
    if ($Missing -contains "uv" -or $Missing -contains "uvx") {
        $commands += "winget install --id astral-sh.uv -e"
    }
    if ($Missing -contains "ffmpeg" -or $Missing -contains "ffprobe") {
        $commands += "winget install --id Gyan.FFmpeg -e"
    }
    return $commands
}

function Show-PlannedChanges {
    [CmdletBinding()]
    param([string[]]$Commands)

    Write-Output "The following commands may download software and modify user or system tool locations:"
    foreach ($command in $Commands) {
        Write-Output "  $command"
    }
    Write-Output "winget may display license prompts or request elevation."
    Write-Output "After installation, uvx contacts PyPI to prewarm videopilot==0.1.7."
}

if ($DryRun -and -not $Install) {
    [Console]::Error.WriteLine("-DryRun requires -Install.")
    exit 2
}

if ($env:VIDEOPILOT_TEST_PLATFORM -and $env:VIDEOPILOT_TEST_PLATFORM -ne "windows") {
    [Console]::Error.WriteLine("Unsupported platform: $($env:VIDEOPILOT_TEST_PLATFORM)")
    exit 3
}

Write-Output "Checking VideoPilot prerequisites (no changes are made by this check)."
$missing = @(Get-MissingCommands)

if ($missing.Count -eq 0) {
    Write-Output "All prerequisites are available."
    if (-not $Install) {
        exit 0
    }
}

$commands = @(Get-InstallCommands -Missing $missing)
if (-not $Install) {
    Show-PlannedChanges -Commands $commands
    [Console]::Error.WriteLine("Prerequisites are missing. Re-run with -Install after reviewing the commands.")
    exit 1
}

if ($commands.Count -gt 0) {
    Show-PlannedChanges -Commands $commands
} else {
    Write-Output "No package-manager commands are needed."
}

if ($DryRun) {
    Write-Output "Dry run complete; no commands were executed."
    exit 0
}

if (-not $Yes) {
    if ($env:CI -or $env:VIDEOPILOT_NONINTERACTIVE -eq "1") {
        [Console]::Error.WriteLine("Noninteractive install requires -Yes.")
        exit 2
    }
    $answer = Read-Host "Proceed with these installation commands? [y/N]"
    if ($answer -notmatch "^(?i:y|yes)$") {
        [Console]::Error.WriteLine("Installation declined.")
        exit 2
    }
}

if ($commands.Count -gt 0 -and $null -eq (Get-Command winget -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("winget is required for automatic installation on Windows.")
    exit 3
}

foreach ($command in $commands) {
    Write-Output "Running: $command"
    & $env:ComSpec /d /s /c $command
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("Installation command failed with exit code $LASTEXITCODE.")
        exit $LASTEXITCODE
    }
}

Write-Output "Rechecking prerequisites."
$remaining = @(Get-MissingCommands)
if ($remaining.Count -gt 0) {
    [Console]::Error.WriteLine("Prerequisites remain missing: $($remaining -join ', '). Restart the shell and rerun the check.")
    exit 1
}

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

Write-Output "Setup succeeded. Reload the host and call the VideoPilot MCP doctor tool."
