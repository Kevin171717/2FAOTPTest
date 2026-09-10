@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 "src\run_raw_openai_experiment.py" %*
endlocal
