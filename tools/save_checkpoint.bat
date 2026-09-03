@echo off
echo ==============================================
echo  Hard Reset Point - Save State
echo ==============================================

cd /d "C:\FMP_Ultimate"

set /p msg="Enter a brief description for this checkpoint (e.g., 'Before editing index.html'): "

REM git add . is intentional here (unlike the auto-commit workers elsewhere
REM in this codebase) - this is a manual, interactively-invoked "snapshot
REM everything I've changed" tool, not an unattended background commit that
REM could sweep up someone else's unrelated staged work.
git add .
git commit -m "CHECKPOINT: %msg%"

REM Dynamic branch detection instead of a hardcoded "main" - the repo's
REM actual working branch is "dev".
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b

echo.
echo Trying to push to remote (if configured)...
git push origin %BRANCH%

echo.
echo Checkpoint Saved! You can always return to this point using git log / git reset.
timeout /t 5
