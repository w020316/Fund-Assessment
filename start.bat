@echo off
echo Starting QuantFlow Pro ...
if "%APP_ENV%"=="" set "APP_ENV=dev"
if "%ADMIN_TOKEN%"=="" (
  echo ========================================
  echo  [WARN] ADMIN_TOKEN is empty, write endpoints have NO auth!
  echo  Local loopback dev only; production MUST set ADMIN_TOKEN.
  echo ========================================
)
rem 统一走 launch.py: 由其负责 sys.path / PYTHONPATH 注入, 避免依赖调用方环境
D:\dev-tools\Python312\python.exe "%~dp0launch.py" --host 0.0.0.0 --port 8000 --reload
