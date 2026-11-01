@echo off
setlocal

set "PGHOST=192.168.200.188"
set "PGPORT=5432"
set "PGUSER=customer_service"
set "PGPASSWORD=customer_service"

"%~dp0ai-service\.venv\Scripts\python.exe" "%~dp0clear_database_data.py"

if errorlevel 1 (
    echo.
    echo Failed to clear database data.
    pause
    exit /b 1
)

echo.
echo Data in both databases was cleared successfully.
pause
endlocal
