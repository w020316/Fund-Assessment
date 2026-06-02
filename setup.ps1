$ErrorActionPreference = "Stop"

$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fund-Assessment 环境初始化" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$pythonCmd = $null
foreach ($cmd in @("D:\dev-tools\Python312\python.exe", "python", "python3", "py")) {
    try {
        $version = & $cmd --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonCmd = $cmd
            Write-Host "[OK] 找到 Python: $version" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host "[ERROR] 未找到 Python，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

Write-Host "`n[1/2] 安装项目依赖 (清华源 → 项目本地 pylibs)..." -ForegroundColor Yellow
& $pythonCmd (Join-Path $Workspace "install_deps.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 依赖安装失败" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 依赖安装完成" -ForegroundColor Green

$PylibsDir = Join-Path $Workspace "pylibs"
Write-Host "`n[2/2] 配置环境..." -ForegroundColor Yellow
$env:PYTHONPATH = "$PylibsDir;$Workspace;$env:PYTHONPATH"
Write-Host "[OK] PYTHONPATH 已包含: $PylibsDir" -ForegroundColor Green

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  环境初始化完成！" -ForegroundColor Cyan
Write-Host "  依赖目录: $PylibsDir" -ForegroundColor Cyan
Write-Host "  启动项目: .\start.ps1" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
