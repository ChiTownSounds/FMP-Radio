@echo off
echo ==============================================
echo  Database Time Machine - Snapshot Generator
echo ==============================================

cd /d "C:\FMP_Ultimate"

REM Get timestamp in a safe format
set TIMESTAMP=%date:~-4%%date:~4,2%%date:~7,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%

echo Checking for changes to fmp_data_7718.csv...
git add configs/fmp_data_7718.csv

git commit -m "Auto-backup master database: %TIMESTAMP%"

echo.
echo Trying to push to remote (if configured)...
git push origin main

echo.
echo Done! The timeline is secure.
timeout /t 5
