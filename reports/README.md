# Reports

`Regex_OpenAI_外部成本比較_中文.xlsx` is the curated comparison workbook for
the current 128-message experiment. Raw paid API JSON is intentionally excluded
from Git because it can contain candidate context and OpenAI response IDs.

The workbook compares baseline, Regex v2 + BGE, Regex v2 + BGE + OpenAI
fallback, and Regex v2 + OpenAI. It includes aggregate quality, API calls,
tokens, estimated USD cost, latency percentiles, and selected error details.
