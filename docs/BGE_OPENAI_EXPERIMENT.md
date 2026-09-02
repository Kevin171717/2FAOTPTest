# Regex v2 + BGE + OpenAI Fallback

This experiment has no rule layer that directly accepts or rejects OTPs. Regex
v2 generates candidates, BGE produces the existing ranking, and OpenAI is used
only when a general uncertainty gate is triggered. Ground truth is read only
after prediction and is used only to calculate metrics.

## Configure the API

Run `setup.cmd` first. The first BGE run downloads
`BAAI/bge-large-zh-v1.5`; later runs can set `OTP_OFFLINE_MODE=1` when the
model is already cached.

Open `.env` and fill only the value after `OPENAI_API_KEY=`:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
```

Do not paste the key into source code, chat, or version control. Prices in
`.env` are used only to estimate experiment cost and can be updated separately.

## API gate

OpenAI is called when at least one of these conditions is present:

- two or more Regex candidates;
- a candidate is a year, address component, or phone suffix;
- BGE OTP and security-notification scores compete;
- the best BGE score is below `0.50`, especially with a code cue;
- Top-1/Top-2 or Top-1/Top-3 candidate scores differ by at most `0.05`;
- a candidate is all-uppercase alphabetic text;
- BGE has candidates but is uncertain between a candidate and `NO_OTP`.

These checks only choose `BGE_DIRECT` or `OPENAI_FALLBACK`. They never select a
candidate and never output `NO_OTP` themselves.

## Dry run

```powershell
.\run_bge_openai_experiment.cmd --dry-run
```

Output: `results/bge_openai/bge_openai_dry_run.json`

The dry run never calls OpenAI. Rows selected by the gate fall back to the
saved BGE result, so its accuracy must match Regex v2 + BGE. It is useful for
checking routing volume before spending API credits.

## API experiment

```powershell
.\run_bge_openai_experiment.cmd
```

Output: `results/bge_openai/bge_openai_experiment_results.json`

The result is checkpointed after every row. Resume an interrupted run with:

```powershell
.\run_bge_openai_experiment.cmd --resume
```

Run a small paid smoke test before the full experiment:

```powershell
.\run_bge_openai_experiment.cmd --ids 86,92,103,128,131,137 --max-api-calls 6
```

The `.cmd` launcher works even when Windows PowerShell script execution is
disabled. The equivalent `.ps1` launcher is kept for environments that permit
PowerShell scripts.

Each successful API call records model, latency, retries, input tokens, cached
input tokens, cache-write tokens, output tokens, reasoning tokens, total tokens,
and estimated USD cost. Failed attempts are counted separately.

## Comparison workbook

After a dry-run or API run, generate the Excel comparison report from the
corresponding JSON file with `scripts/build_bge_openai_report.mjs`. The workbook contains
summary metrics and a chart, all API-gated rows, remaining misses/errors, and all
128 row-level results. A dry-run report is explicitly labelled and must not be
reported as an OpenAI accuracy result.
