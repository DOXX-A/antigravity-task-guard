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
Get-ChildItem -Path (Join-Path $currentDir "scripts\*.py") | ForEach-Object {
    Copy-Item $_.FullName -Destination (Join-Path $scriptsDir $_.Name) -Force
}

Write-Host "Antigravity Task Guard installed successfully." -ForegroundColor Green
