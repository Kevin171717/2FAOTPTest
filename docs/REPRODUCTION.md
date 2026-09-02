# OTP extraction paper reproduction

## Reproduced setting

- Dataset: 128 messages (103 OTP, 10 verification links, 15 negatives)
- Candidate extraction: the regular expressions and exclusions in `legacy/otp_code_extractor.py`
- Embedding model: `BAAI/bge-large-zh-v1.5`
- Semantic threshold: `0.50`
- Notification rejection margin: `0.03`
- Runtime: local CPU, Python 3.12, package versions in `requirements-reproduction.txt`

## Run

Run `setup.cmd` first. On the first BGE run, allow network access so
`BAAI/bge-large-zh-v1.5` can be downloaded into the Hugging Face cache. Then run:

```powershell
.\run_paper_reproduction.cmd
```

The script writes `results/reproduction/reproduction_paper_results.xlsx` with `Detail` and `Summary`
sheets. CPU execution is expected to take several minutes, especially for long
HTML email messages.

After the model is cached, use `set OTP_OFFLINE_MODE=1` in Command Prompt or
`$env:OTP_OFFLINE_MODE = "1"` in PowerShell to prevent Hugging Face metadata
requests.

## Expected result

| Metric | Reproduced value |
| --- | ---: |
| OTP Top-1 | 85.4% (88/103) |
| OTP Top-3 | 86.4% (89/103) |
| Verification-link exact match | 70.0% (7/10) |
| False extraction on negatives | 20.0% (3/15) |

These values match the thesis. The supplied extractor did not contain the thesis'
notification-mail rejection layer, so it is implemented as an optional
`notification_margin` parameter. Leaving the parameter unset preserves the
original extractor behavior.
