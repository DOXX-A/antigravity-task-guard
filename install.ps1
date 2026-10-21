<#
.SYNOPSIS
    Antigravity Task Guard 安装脚本

.DESCRIPTION
    将 Hook 配置与执行脚本安装至全局配置 (~/.gemini/config/) 或当前工作区 (.agents/)。

.PARAMETER Scope
    安装范围：'Global'（默认，整机所有项目生效）或 'Workspace'（仅当前工作区生效）。
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

Write-Host "正在安装 Antigravity Task Guard 到 [$Scope]: $targetDir" -ForegroundColor Cyan

# 确保目录存在
$scriptsDir = Join-Path $targetDir "scripts"
if (-not (Test-Path $scriptsDir)) {
    New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
}

# 复制脚本与配置
Copy-Item (Join-Path $currentDir "hooks.json") -Destination (Join-Path $targetDir "hooks.json") -Force
Copy-Item (Join-Path $currentDir "scripts\pre_run_command.py") -Destination (Join-Path $scriptsDir "pre_run_command.py") -Force
Copy-Item (Join-Path $currentDir "scripts\stop_watchdog.py") -Destination (Join-Path $scriptsDir "stop_watchdog.py") -Force

Write-Host "文件复制完成。" -ForegroundColor Green
Write-Host "请将 rules/GEMINI_RULE.md 中的规则内容添加至您的 GEMINI.md 中完成闭环配置。" -ForegroundColor Yellow
