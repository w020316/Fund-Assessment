$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fund-Assessment 一键启动脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
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

Write-Host "`n[1/3] 安装依赖..." -ForegroundColor Yellow
& $pythonCmd -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 依赖安装失败" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 依赖安装完成" -ForegroundColor Green

Write-Host "`n[2/3] 启动 FastAPI 服务..." -ForegroundColor Yellow
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
    Write-Host "[OK] 已加载 .env 配置" -ForegroundColor Green
}

$job = Start-Job -ScriptBlock {
    param($root)
    Set-Location $root
    & python -m uvicorn web.api:app --host 0.0.0.0 --port 8000 --reload
} -ArgumentList $PSScriptRoot

Start-Sleep -Seconds 3

Write-Host "`n[3/3] 打开浏览器..." -ForegroundColor Yellow
Start-Process "http://localhost:8000"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  服务已启动: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  按 Ctrl+C 停止服务" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

try {
    Receive-Job $job -Wait
} finally {
    Stop-Job $job -ErrorAction SilentlyContinue
    Remove-Job $job -ErrorAction SilentlyContinue
}
