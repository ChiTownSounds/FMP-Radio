@echo off
echo ==============================================
echo  Hard Reset Point - Save State
echo ==============================================

cd /d "C:\FMP_Ultimate"

set /p msg="Enter a brief description for this checkpoint (e.g., 'Before editing index.html'): "

git add .
git commit -m "CHECKPOINT: %msg%"

echo.
echo Trying to push to remote (if configured)...
git push origin main

echo.
echo Checkpoint Saved! You can always return to this point using git log / git reset.
pause
