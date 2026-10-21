<#
.SYNOPSIS
    Antigravity Task Guard Installer

.DESCRIPTION
    Installs lifecycle hooks and scripts to global configuration (~/.gemini/config/)
    or workspace configuration (.agents/).

.PARAMETER Scope
    Installation target: 'Global' (default, applies machine-wide) or 'Workspace' (current project only).
#>

param (
    [ValidateSet("Global", "Workspace")]
    [string]$Scope = "Global"
)

$ErrorActionPreference = "Stop"

$currentDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Scope -eq "Global") {
    $targetDir = [System.IO.Path]::Combine($env:USERPROFILE, ".gemini", "config")
} else {
    $targetDir = [System.IO.Path]::Combine((Get-Location).Path, ".agents")
}

Write-Host "Installing Antigravity Task Guard to [$Scope]: $targetDir" -ForegroundColor Cyan

$scriptsDir = Join-Path $targetDir "scripts"
if (-not (Test-Path $scriptsDir)) {
    New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
}

Copy-Item (Join-Path $currentDir "hooks.json") -Destination (Join-Path $targetDir "hooks.json") -Force
Copy-Item (Join-Path $currentDir "scripts\pre_run_command.py") -Destination (Join-Path $scriptsDir "pre_run_command.py") -Force
Copy-Item (Join-Path $currentDir "scripts\stop_watchdog.py") -Destination (Join-Path $scriptsDir "stop_watchdog.py") -Force

Write-Host "Antigravity Task Guard installed successfully." -ForegroundColor Green
