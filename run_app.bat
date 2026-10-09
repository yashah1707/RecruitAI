@echo off
rem Starts RecruitAI on this computer. Double-click it, or run it from a terminal.
rem It brings the database up to date first, so it is safe after pulling a newer version.
rem Close this window (or press Ctrl+C) to stop the application.

cd /d "%~dp0"

echo Bringing the database up to date...
python -m alembic upgrade head || goto :failed
python -m backend.seed >nul || goto :failed
python -m backend.rules_seed >nul || goto :failed

echo.
echo RecruitAI is starting. Open http://127.0.0.1:8000 in your browser.
echo Leave this window open while you use it.
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
goto :eof

:failed
echo.
echo RecruitAI could not start. Check that PostgreSQL is running and that .env is in this folder.
pause
