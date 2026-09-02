@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "RUNNER=%ROOT%src\run_bge_openai_experiment.py"

if not exist "%PYTHON%" (
    echo Python environment not found: %PYTHON%
    exit /b 1
)

if /I "%OTP_OFFLINE_MODE%"=="1" (
    set "HF_HUB_OFFLINE=1"
    set "TRANSFORMERS_OFFLINE=1"
)

"%PYTHON%" -X utf8 "%RUNNER%" %*
exit /b %ERRORLEVEL%
