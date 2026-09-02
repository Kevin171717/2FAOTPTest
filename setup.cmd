@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher "py" was not found. Install Python 3.12 first.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements-reproduction.txt
if errorlevel 1 exit /b 1

if not exist ".env" copy /Y ".env.example" ".env" >nul

echo Setup complete.
echo Add OPENAI_API_KEY to .env only when running a paid OpenAI experiment.
exit /b 0
