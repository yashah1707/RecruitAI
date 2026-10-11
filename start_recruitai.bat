@echo off
rem Starts RecruitAI on this computer. Double-click this file; close the window to stop the site.
title RecruitAI - keep this window open
cd /d "%~dp0"
echo RecruitAI is starting at http://127.0.0.1:8000
echo Keep this window open while you use the site. Close it to stop the site.
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
echo.
echo The site has stopped.
pause
