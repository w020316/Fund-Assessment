@echo off
echo Starting OpenClaw Quantitative Trading System...
if "%APP_ENV%"=="" set "APP_ENV=dev"
if "%ADMIN_TOKEN%"=="" (
  echo ========================================
  echo  [WARN] ADMIN_TOKEN is empty, write endpoints have NO auth!
  echo  Local loopback dev only; production MUST set ADMIN_TOKEN.
  echo ========================================
)
D:\dev-tools\Python312\python.exe -m uvicorn web.api:app --host 0.0.0.0 --port 8000 --reload
