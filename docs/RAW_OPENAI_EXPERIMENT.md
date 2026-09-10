# Raw Message + OpenAI Ablation

This ablation sends every dataset `text` value to OpenAI unchanged. It does not
run Regex, BGE, sanitization, truncation, or subject extraction. The fixed task
instructions and Structured Outputs schema are the only experiment scaffolding.
Ground truth is read only after each prediction.

Run a no-cost pipeline check:

```powershell
.\run_raw_openai_experiment.cmd --dry-run
```

Run one paid smoke test, then the full 128-message experiment:

```powershell
.\run_raw_openai_experiment.cmd --ids 137 --max-api-calls 1 --output results\raw_openai\smoke_test.json
.\run_raw_openai_experiment.cmd
```

The primary metrics use strict exact matching: OTP Top-1/Top-3, verification
URL extraction, and false extraction on negative examples. The result records
message length and SHA-256 instead of copying raw message content into the JSON.

The full GPT-4o run produced strict OTP Top-1/Top-3 of 96/103, verification
links of 9/10, and 2/15 false extractions on negative messages. A secondary
input-normalized OTP metric is 102/103; it removes display separators and
non-input prefixes only when the ground truth is numeric.
