from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from openai_otp_fallback import (
    REGEX_ONLY_SYSTEM_INSTRUCTIONS,
    OpenAIConfig,
    OpenAIFallbackClient,
    load_env_file,
)
from regex_v2_candidates import extract_candidates


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "otp_dataset_dedup.xlsx"
DEFAULT_BASELINE = PROJECT_ROOT / "results" / "reproduction" / "reproduction_paper_results_verified.xlsx"
DEFAULT_REGEX_V2 = PROJECT_ROOT / "results" / "regex_v2" / "regex_v2_experiment_results.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "regex_openai" / "regex_openai_experiment_results.json"
DEFAULT_DRY_OUTPUT = PROJECT_ROOT / "results" / "regex_openai" / "regex_openai_dry_run.json"

NEGATIVE_LABELS = {"", "none", "null", "nan", "n/a", "na"}
ANNOTATION_MARKERS = re.compile(
    r"真碼|干擾|ground\s*truth|annotation|人工註解", re.IGNORECASE
)


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate Regex v2 + OpenAI for every candidate-bearing message.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--regex-v2", type=Path, default=DEFAULT_REGEX_V2)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-api-calls", type=int)
    parser.add_argument("--ids", help="Comma-separated dataset IDs for a focused run.")
    return parser.parse_args()


def clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def subject_proxy_from_note(value) -> str:
    note = clean(value)
    return "" if not note or ANNOTATION_MARKERS.search(note) else note


def category_from_ground_truth(value) -> str:
    ground_truth = clean(value)
    if ground_truth.lower() in NEGATIVE_LABELS:
        return "negative"
    if ground_truth.startswith(("http://", "https://")):
        return "link"
    return "otp"


def parse_count(value: str) -> tuple[int, int]:
    match = re.search(r"\((\d+)/(\d+)\)", clean(value))
    if not match:
        raise ValueError(f"Cannot parse metric count: {value!r}")
    return int(match.group(1)), int(match.group(2))


def load_comparison_metrics(baseline_path: Path, regex_payload: dict) -> list[dict]:
    baseline = pd.read_excel(baseline_path, sheet_name="Summary")
    baseline_by_metric = {
        clean(row["Metric"]): parse_count(row["Overall"])
        for row in baseline.to_dict(orient="records")
    }
    regex_by_metric = {
        clean(row["Metric"]): parse_count(row["Overall"])
        for row in regex_payload["summary"]
    }
    labels = [
        ("OTP Top-1", "OTP Top-1"),
        ("OTP Top-3", "OTP Top-3"),
        ("Verification Link Extraction", "Verification Link"),
        ("False Extractions on Negative Examples", "Negative False Extraction"),
    ]
    return [
        {
            "metric": label,
            "baseline_hits": baseline_by_metric[key][0],
            "baseline_total": baseline_by_metric[key][1],
            "regex_v2_hits": regex_by_metric[key][0],
            "regex_v2_total": regex_by_metric[key][1],
        }
        for key, label in labels
    ]


def metric_summary(details: list[dict]) -> dict:
    otp = [row for row in details if row["category"] == "otp"]
    links = [row for row in details if row["category"] == "link"]
    negatives = [row for row in details if row["category"] == "negative"]
    return {
        "otp_top1": {"hits": sum(row["top1_correct"] for row in otp), "total": len(otp)},
        "otp_top3": {"hits": sum(row["top3_correct"] for row in otp), "total": len(otp)},
        "verification_link": {"hits": sum(row["link_correct"] for row in links), "total": len(links)},
        "negative_false_extraction": {
            "hits": sum(row["false_extraction"] for row in negatives),
            "total": len(negatives),
        },
    }


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def usage_summary(details: list[dict]) -> dict:
    usage_keys = [
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "estimated_cost_usd",
    ]
    totals = {key: 0 for key in usage_keys}
    successful_rows = [row for row in details if row.get("api")]
    attempted_rows = [row for row in details if row.get("api_attempted")]
    latencies = [row["api"]["latency_ms"] for row in successful_rows]
    for row in successful_rows:
        for key in usage_keys:
            totals[key] += row["api"]["usage"].get(key, 0)
    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 8)
    totals["api_calls"] = len(attempted_rows)
    totals["successful_calls"] = len(successful_rows)
    totals["failed_calls"] = len(attempted_rows) - len(successful_rows)
    totals["average_latency_ms"] = round(sum(latencies) / len(latencies)) if latencies else 0
    totals["median_latency_ms"] = _percentile(latencies, 0.50)
    totals["p95_latency_ms"] = _percentile(latencies, 0.95)
    totals["max_latency_ms"] = max(latencies, default=0)
    totals["api_latency_total_ms"] = sum(latencies)
    totals["estimated_cost_per_1000_messages_usd"] = (
        round(totals["estimated_cost_usd"] / len(details) * 1000, 6) if details else 0
    )
    return totals


def process_memory_mb() -> tuple[float | None, float | None]:
    if os.name != "nt":
        return None, None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        return None, None
    unit = 1024 * 1024
    return round(counters.WorkingSetSize / unit, 3), round(counters.PeakWorkingSetSize / unit, 3)


def runtime_summary(
    details: list[dict],
    started_wall: float,
    started_cpu: float,
) -> dict:
    wall_seconds = time.perf_counter() - started_wall
    cpu_seconds = time.process_time() - started_cpu
    working_set_mb, peak_working_set_mb = process_memory_mb()
    return {
        "wall_time_seconds": round(wall_seconds, 3),
        "cpu_time_seconds": round(cpu_seconds, 3),
        "throughput_messages_per_second": round(len(details) / wall_seconds, 4) if wall_seconds else 0,
        "working_set_mb": working_set_mb,
        "peak_working_set_mb": peak_working_set_mb,
    }


def write_checkpoint(
    output_path: Path,
    details: list[dict],
    comparison: list[dict],
    config: OpenAIConfig,
    dry_run: bool,
    full_dataset: bool,
    started_wall: float,
    started_cpu: float,
    started_at: str,
) -> None:
    payload = {
        "experiment": {
            "name": "Regex v2 + OpenAI all candidates",
            "dry_run": dry_run,
            "openai_evaluation_valid": not dry_run and full_dataset,
            "ground_truth_used_for_prediction": False,
            "routing_policy": "Call OpenAI for every message with one or more Regex v2 candidates; otherwise NO_OTP.",
            "url_policy": "Keep the existing Regex v2+BGE URL prediction unchanged.",
            "openai": config.public_dict(),
            "prompt": {
                "name": "otp-regex-openai-v1",
                "sha256": hashlib.sha256(REGEX_ONLY_SYSTEM_INSTRUCTIONS.encode("utf-8")).hexdigest(),
            },
            "routing": dict(Counter(row["route"] for row in details)),
            "started_at_utc": started_at,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "metrics": metric_summary(details),
        "api_usage": usage_summary(details),
        "runtime": runtime_summary(
            details, started_wall, started_cpu
        ),
        "comparison": comparison,
        "detail": sorted(details, key=lambda row: row["sequence"]),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    args = parse_args()
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    started_at = datetime.now(timezone.utc).isoformat()
    load_env_file(args.env_file)
    config = OpenAIConfig.from_environment()
    output_path = args.output or (DEFAULT_DRY_OUTPUT if args.dry_run else DEFAULT_OUTPUT)

    if not args.dry_run and not config.api_key:
        raise SystemExit(f"OPENAI_API_KEY is empty. Fill it in {args.env_file} and run again.")

    dataset = pd.read_excel(args.dataset, sheet_name="dataset_dedup", dtype=str, keep_default_na=False)
    regex_payload = json.loads(args.regex_v2.read_text(encoding="utf-8"))
    regex_by_sequence = {int(row["row"]): row for row in regex_payload["detail"]}
    comparison = load_comparison_metrics(args.baseline, regex_payload)
    selected_ids = (
        {int(value.strip()) for value in args.ids.split(",") if value.strip()}
        if args.ids
        else None
    )

    existing_details = []
    if args.resume and output_path.exists():
        existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
        if existing_payload.get("experiment", {}).get("name") != "Regex v2 + OpenAI all candidates":
            raise SystemExit("Resume file belongs to a different experiment architecture.")
        existing_details = existing_payload.get("detail", [])
    details_by_sequence = {int(row["sequence"]): row for row in existing_details}

    api_client = None if args.dry_run else OpenAIFallbackClient(config)
    api_calls_this_run = 0
    selected_total = (
        sum(int(row["id"]) in selected_ids for _, row in dataset.iterrows())
        if selected_ids is not None
        else len(dataset)
    )

    for zero_index, source_row in dataset.iterrows():
        sequence = zero_index + 1
        dataset_id = int(source_row["id"])
        if selected_ids is not None and dataset_id not in selected_ids:
            continue
        if sequence in details_by_sequence:
            continue

        row_started_wall = time.perf_counter()
        row_started_cpu = time.process_time()
        regex_result = regex_by_sequence[sequence]
        text = clean(source_row["text"])
        subject = subject_proxy_from_note(source_row.get("note", ""))
        combined_text = f"{subject}\n{text}" if subject else text
        regex_started = time.perf_counter()
        candidates = extract_candidates(combined_text)
        regex_latency_ms = round((time.perf_counter() - regex_started) * 1000, 3)
        actual_candidates = [candidate["value"] for candidate in candidates]

        final_url = clean(regex_result["URL Prediction"])
        api_decision = None
        api_attempted = False
        api_error = ""
        candidate_by_id = {candidate["candidate_id"]: candidate["value"] for candidate in candidates}

        if not candidates:
            final_codes = []
            route = "NO_REGEX_CANDIDATE"
        elif args.dry_run:
            final_codes = actual_candidates[:3]
            route = "DRY_RUN_OPENAI_ALL_FALLBACK_TO_REGEX"
        elif args.max_api_calls is not None and api_calls_this_run >= args.max_api_calls:
            final_codes = actual_candidates[:3]
            route = "API_LIMIT_FALLBACK_TO_REGEX"
        else:
            api_attempted = True
            api_calls_this_run += 1
            route = "OPENAI_ALL_CANDIDATES"
            try:
                api_decision = api_client.decide_regex_only(subject, combined_text, candidates)
                final_codes = [
                    candidate_by_id[candidate_id]
                    for candidate_id in api_decision.ranked_candidate_ids
                ]
            except Exception as exc:
                api_error = f"{type(exc).__name__}: {exc}"
                final_codes = actual_candidates[:3]
                route = "API_ERROR_FALLBACK_TO_REGEX"

        # Ground truth is deliberately read only after prediction is complete.
        ground_truth = clean(source_row["ground_truth"])
        category = category_from_ground_truth(ground_truth)
        top1_correct = int(category == "otp" and bool(final_codes) and final_codes[0] == ground_truth)
        top3_correct = int(category == "otp" and ground_truth in final_codes[:3])
        link_correct = int(category == "link" and final_url == ground_truth)
        false_extraction = int(category == "negative" and bool(final_codes or final_url))
        working_set_mb, peak_working_set_mb = process_memory_mb()

        result = {
            "dataset_id": dataset_id,
            "sequence": sequence,
            "source": clean(source_row["source"]).lower(),
            "category": category,
            "ground_truth": ground_truth,
            "subject_proxy": subject,
            "route": route,
            "candidates": candidates,
            "regex_top1": actual_candidates[0] if actual_candidates else "",
            "regex_top3": actual_candidates[:3],
            "final_top1": final_codes[0] if final_codes else "",
            "final_top3": final_codes[:3],
            "final_url": final_url,
            "top1_correct": top1_correct,
            "top3_correct": top3_correct,
            "link_correct": link_correct,
            "false_extraction": false_extraction,
            "api_attempted": api_attempted,
            "api": api_decision.to_dict() if api_decision else None,
            "api_error": api_error,
            "timing": {
                "regex_latency_ms": regex_latency_ms,
                "row_wall_ms": round((time.perf_counter() - row_started_wall) * 1000, 3),
                "row_cpu_ms": round((time.process_time() - row_started_cpu) * 1000, 3),
                "working_set_mb": working_set_mb,
                "peak_working_set_mb": peak_working_set_mb,
            },
        }
        details_by_sequence[sequence] = result
        details = list(details_by_sequence.values())
        write_checkpoint(
            output_path,
            details,
            comparison,
            config,
            args.dry_run,
            selected_ids is None,
            started_wall,
            started_cpu,
            started_at,
        )
        print(
            f"[{len(details)}/{selected_total}] ID={dataset_id} route={route} "
            f"top1={result['final_top1'] or 'NO_OTP'}"
        )

    details = list(details_by_sequence.values())
    write_checkpoint(
        output_path,
        details,
        comparison,
        config,
        args.dry_run,
        selected_ids is None,
        started_wall,
        started_cpu,
        started_at,
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    print(json.dumps(payload["api_usage"], ensure_ascii=False, indent=2))
    print(json.dumps(payload["runtime"], ensure_ascii=False, indent=2))
    print(f"Result JSON: {output_path}")


if __name__ == "__main__":
    main()
