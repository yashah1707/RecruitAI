@echo off
rem Backs up RecruitAI's database and stored resumes into the backups folder.
rem Double-click it, or schedule it in Windows Task Scheduler (for example, every night).
rem With DATA_ENCRYPTION_KEY set in .env the backup is encrypted. Only the last 14 are kept.

cd /d "%~dp0"
python -m backend.backup
if errorlevel 1 (
  echo.
  echo The backup did not finish. See logs\recruitai.log.
  if "%1"=="" pause
  exit /b 1
)
if "%1"=="" pause
