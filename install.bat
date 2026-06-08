@echo off
echo Installing Fund-Assessment dependencies...
D:\dev-tools\Python312\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple apscheduler tushare stockstats pandas-ta akshare mootdx
echo.
echo Verifying installation...
D:\dev-tools\Python312\python.exe -c "import akshare, apscheduler, tushare, pandas_ta, mootdx, stockstats; print('All dependencies installed successfully!')"
echo.
echo Press any key to start the server...
pause
D:\dev-tools\Python312\python.exe -m uvicorn web.api:app --host 0.0.0.0 --port 8000
