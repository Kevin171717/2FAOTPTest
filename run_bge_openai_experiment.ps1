$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $root "src\run_bge_openai_experiment.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found: $python"
}

if ($env:OTP_OFFLINE_MODE -eq "1") {
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
}

& $python -X utf8 $runner @args
exit $LASTEXITCODE
