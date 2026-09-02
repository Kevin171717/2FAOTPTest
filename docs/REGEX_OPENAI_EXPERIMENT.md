# Regex v2 + OpenAI

## Architecture

```text
message
  -> Regex v2 candidate generation
  -> no candidate: NO_OTP
  -> one or more candidates: OpenAI structured decision
  -> candidate ID(s) or NO_OTP
```

This experiment deliberately does not load BGE. Every message containing at
least one Regex v2 OTP candidate is sent to OpenAI. URL extraction remains the
same as the Regex v2 + BGE experiment, so this experiment measures OTP
adjudication rather than a new URL extractor.

The API receives only candidate IDs, values, Regex pattern names, local context,
the sanitized subject, and a sanitized message excerpt. Ground truth and BGE
scores are never included. The structured-output schema prevents the model from
inventing or transforming a candidate.

## Dry run

```powershell
.\run_regex_openai_experiment.cmd --dry-run
```

Dry-run does not call OpenAI. It uses Regex candidate order as a placeholder and
is intended only to validate candidate generation, routing, and output writing.
It is not an OpenAI accuracy result.

## Paid run

Add `OPENAI_API_KEY` to `.env`, run a bounded smoke test, and then run the full
dataset:

```powershell
.\run_regex_openai_experiment.cmd --ids 4,86,92,103,128,137 --max-api-calls 6 --output results\regex_openai\smoke_test.json
.\run_regex_openai_experiment.cmd
```

The full run calls OpenAI for 123 of the 128 current samples. It records API
attempts, failures, latency, token categories, estimated USD cost, and row-level
predictions. Results are checkpointed after every message and can be resumed:

```powershell
.\run_regex_openai_experiment.cmd --resume
```

Paid result JSON is ignored by Git because it may contain candidate context and
OpenAI response IDs.
