# OpenClaw 量化 AI 炒股机器人 - Windows 控制脚本

$Workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Workspace "data\logs"
$PidDir = Join-Path $Workspace "data"
$PylibsDir = Join-Path $Workspace "pylibs"
$Python = "D:\dev-tools\Python312\python.exe"

if (-not (Test-Path $Python)) {
    $Python = "python"
}

$env:PYTHONPATH = "$PylibsDir;$Workspace;$env:PYTHONPATH"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Start-Modules {
    Write-Host "启动 OpenClaw 量化交易系统..."

    $quantProc = Start-Process -FilePath $Python -ArgumentList "`"$Workspace\scripts\quant.py`" market_anomaly" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LogDir\quant.log" -RedirectStandardError "$LogDir\quant_error.log"
    $quantProc.Id | Out-File -FilePath "$PidDir\quant.pid" -Encoding utf8
    Write-Host "量化分析模块已启动 [PID: $($quantProc.Id)]"

    $cbProc = Start-Process -FilePath $Python -ArgumentList "`"$Workspace\scripts\cb_monitor.py`" --continuous 30" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LogDir\cb_monitor.log" -RedirectStandardError "$LogDir\cb_monitor_error.log"
    $cbProc.Id | Out-File -FilePath "$PidDir\cb_monitor.pid" -Encoding utf8
    Write-Host "可转债监控已启动 [PID: $($cbProc.Id)]"

    $limitProc = Start-Process -FilePath $Python -ArgumentList "`"$Workspace\scripts\limit_up_monitor.py`"" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LogDir\limit_up.log" -RedirectStandardError "$LogDir\limit_up_error.log"
    $limitProc.Id | Out-File -FilePath "$PidDir\limit_up.pid" -Encoding utf8
    Write-Host "涨停板监控已启动 [PID: $($limitProc.Id)]"

    Write-Host "所有模块已启动"
}

function Stop-Modules {
    Write-Host "停止 OpenClaw 量化交易系统..."
    $pidFiles = Get-ChildItem -Path $PidDir -Filter "*.pid" -ErrorAction SilentlyContinue
    foreach ($pf in $pidFiles) {
        $name = $pf.BaseName
        $pid = Get-Content $pf.FullName -ErrorAction SilentlyContinue
        if ($pid) {
            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Write-Host "$name 已停止 [PID: $pid]"
            } else {
                Write-Host "$name 未运行"
            }
        }
        Remove-Item $pf.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Host "所有模块已停止"
}

function Get-ModuleStatus {
    Write-Host "OpenClaw 量化交易系统状态:"
    $pidFiles = Get-ChildItem -Path $PidDir -Filter "*.pid" -ErrorAction SilentlyContinue
    foreach ($pf in $pidFiles) {
        $name = $pf.BaseName
        $pid = Get-Content $pf.FullName -ErrorAction SilentlyContinue
        if ($pid) {
            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "  $name: 运行中 [PID: $pid]"
            } else {
                Write-Host "  $name: 已停止"
            }
        }
    }
}

function Watch-ModuleLog {
    param([string]$Module = "quant")
    $logFile = Join-Path $LogDir "$Module.log"
    if (Test-Path $logFile) {
        Get-Content $logFile -Wait -Tail 50
    } else {
        Write-Host "日志文件不存在: $logFile"
    }
}

$action = if ($args.Count -gt 0) { $args[0] } else { "" }

switch ($action) {
    "start"   { Start-Modules }
    "stop"    { Stop-Modules }
    "restart" { Stop-Modules; Start-Sleep -Seconds 2; Start-Modules }
    "status"  { Get-ModuleStatus }
    "log"     { Watch-ModuleLog -Module $(if ($args.Count -gt 1) { $args[1] } else { "quant" }) }
    default   { Write-Host "用法: .\control.ps1 {start|stop|restart|status|log [module]}" }
}
