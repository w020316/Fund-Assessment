$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Fund-Assessment 一键启动脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$PylibsDir = Join-Path $Workspace "pylibs"
$IsProd = $args -contains "--prod"

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

if (-not $IsProd) {
    if (-not (Test-Path $PylibsDir)) {
        Write-Host "[WARN] 依赖目录未找到，正在自动安装..." -ForegroundColor Yellow
        & (Join-Path $Workspace "setup.ps1")
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] 环境初始化失败" -ForegroundColor Red
            exit 1
        }
    }

    Write-Host "`n[1/2] 检查依赖..." -ForegroundColor Yellow
    & $pythonCmd (Join-Path $Workspace "install_deps.py")
    Write-Host "[OK] 依赖就绪" -ForegroundColor Green
}

$env:PYTHONPATH = "$PylibsDir;$Workspace;$env:PYTHONPATH"
Write-Host "[OK] PYTHONPATH: $PylibsDir" -ForegroundColor Green

Write-Host "`n[2/2] 启动 FastAPI 服务..." -ForegroundColor Yellow
$envFile = Join-Path $Workspace ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
        }
    }
    Write-Host "[OK] 已加载 .env 配置" -ForegroundColor Green
}

# R-01: 本地开发显式注入 APP_ENV=dev, 让 require_admin 走 dev 放行分支。
# 仅本地回环使用; --prod 分支不注入, 生产保持 fail-closed(fail-closed 逻辑本身未改动)。
if (-not $IsProd -and -not $env:APP_ENV) {
    $env:APP_ENV = "dev"
    Write-Host "[DEV] 已注入 APP_ENV=dev (仅限本地回环开发)" -ForegroundColor Yellow
}

if (-not $env:ADMIN_TOKEN) {
    Write-Host "========================================" -ForegroundColor Red
    Write-Host " [警告] ADMIN_TOKEN 为空, 写端点(交易/持仓/配置)无鉴权保护!" -ForegroundColor Red
    Write-Host " 仅限本地回环开发; 生产环境必须设置 ADMIN_TOKEN=<随机长字符串>" -ForegroundColor Red
    Write-Host " 生成: python -c `"import secrets; print(secrets.token_urlsafe(32))`"" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
}

if ($IsProd) {
    # R-15: 模拟持仓/风控/限流计数/缓存均驻留进程内, 多 worker 会让状态各持一份
    # (持仓消失、限流翻倍、风控错乱), 因此生产固定单进程。
    Write-Host "[PROD] 生产模式: 单进程(进程内状态一致性要求), 无热重载" -ForegroundColor Green
    & $pythonCmd launch.py --host 0.0.0.0 --port 8000
} else {
    Write-Host "[DEV] 开发模式: 热重载已启用" -ForegroundColor Green
    $job = Start-Job -ScriptBlock {
        param($py, $root)
        Set-Location $root
        # 统一走 launch.py: 由其负责 sys.path/PYTHONPATH 注入, 避免子进程继承失效
        & $py launch.py --host 0.0.0.0 --port 8000 --reload
    } -ArgumentList $pythonCmd, $Workspace

    Start-Sleep -Seconds 3

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
}
