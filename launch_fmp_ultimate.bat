@echo off
title FMP Ultimate Backend Server
cd /d "C:\FMP_Ultimate"
echo ====================================================
echo          STARTING FMP ULTIMATE BACKEND SERVER
echo ====================================================
echo.
echo [INFO] Resolving Python and running app.py on port 5000...
py app.py
echo.
echo [WARNING] FMP Ultimate Backend stopped.
pause
