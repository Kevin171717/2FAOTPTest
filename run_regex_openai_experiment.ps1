$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& ".\.venv\Scripts\python.exe" -X utf8 ".\src\run_regex_openai_experiment.py" @args
