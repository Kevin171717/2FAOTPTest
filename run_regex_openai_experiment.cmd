@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 "src\run_regex_openai_experiment.py" %*
endlocal
