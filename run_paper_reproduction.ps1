$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing .venv. Create it and install requirements-reproduction.txt first."
}

if ($env:OTP_OFFLINE_MODE -eq "1") {
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
}
$env:OTP_ORIGINAL_EXTRACTOR_PATH = Join-Path $root "legacy\otp_code_extractor.py"
$env:OTP_DATASET_PATH = Join-Path $root "otp_dataset_dedup.xlsx"
$env:OTP_ORIGINAL_RESULT_PATH = Join-Path $root "results\reproduction\reproduction_paper_results.xlsx"
$env:OTP_NOTIFICATION_MARGIN = "0.03"

& $python -X utf8 (Join-Path $root "legacy\otp_result_original_summary_fixed.py")
