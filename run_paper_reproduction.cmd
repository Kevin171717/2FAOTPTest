@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Python environment not found. Run setup.cmd first.
    exit /b 1
)

if /I "%OTP_OFFLINE_MODE%"=="1" (
    set "HF_HUB_OFFLINE=1"
    set "TRANSFORMERS_OFFLINE=1"
)

set "OTP_ORIGINAL_EXTRACTOR_PATH=%CD%\legacy\otp_code_extractor.py"
set "OTP_DATASET_PATH=%CD%\otp_dataset_dedup.xlsx"
set "OTP_ORIGINAL_RESULT_PATH=%CD%\results\reproduction\reproduction_paper_results.xlsx"
set "OTP_NOTIFICATION_MARGIN=0.03"

"%PYTHON%" -X utf8 "%CD%\legacy\otp_result_original_summary_fixed.py"
exit /b %ERRORLEVEL%
