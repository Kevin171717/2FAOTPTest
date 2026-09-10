from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from openai_otp_fallback import (
    RAW_MESSAGE_SYSTEM_INSTRUCTIONS,
    OpenAIConfig,
    OpenAIFallbackClient,
    load_env_file,
)
from run_regex_openai_experiment import (
    category_from_ground_truth,
    clean,
    load_comparison_metrics,
    metric_summary,
    process_memory_mb,
    runtime_summary,
    usage_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "otp_dataset_dedup.xlsx"
DEFAULT_BASELINE = PROJECT_ROOT / "results" / "reproduction" / "reproduction_paper_results_verified.xlsx"
DEFAULT_REGEX_V2 = PROJECT_ROOT / "results" / "regex_v2" / "regex_v2_experiment_results.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "raw_openai" / "raw_openai_gpt4o_full.json"
DEFAULT_DRY_OUTPUT = PROJECT_ROOT / "results" / "raw_openai" / "raw_openai_dry_run.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate raw message to OpenAI with no Regex or BGE preprocessing.")
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


def otp_input_normalized_match(prediction: str, ground_truth: str) -> int:
    if not prediction:
        return 0
    if ground_truth.isdigit():
        return int("".join(re.findall(r"\d", prediction)) == ground_truth)
    return int(re.sub(r"[\s-]+", "", prediction) == re.sub(r"[\s-]+", "", ground_truth))


def raw_metric_summary(details: list[dict]) -> dict:
    otp = [row for row in details if row["category"] == "otp"]
    links = [row for row in details if row["category"] == "link"]
    negatives = [row for row in details if row["category"] == "negative"]
    normalized_otp_hits = sum(
        otp_input_normalized_match(row["final_top1"], row["ground_truth"]) for row in otp
    )
    strict_correct = (
        sum(row["top1_correct"] for row in otp)
        + sum(row["link_correct"] for row in links)
        + sum(not row["false_extraction"] for row in negatives)
    )
    normalized_correct = (
        normalized_otp_hits
        + sum(row["link_correct"] for row in links)
        + sum(not row["false_extraction"] for row in negatives)
    )
    return {
        "otp_top1_input_normalized": {"hits": normalized_otp_hits, "total": len(otp)},
        "overall_strict": {"hits": strict_correct, "total": len(details)},
        "overall_input_normalized": {"hits": normalized_correct, "total": len(details)},
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
            "name": "Raw message + OpenAI",
            "dry_run": dry_run,
            "openai_evaluation_valid": not dry_run and full_dataset,
            "ground_truth_used_for_prediction": False,
            "input_policy": "Send the dataset text field unchanged. No Regex, BGE, sanitization, truncation, or note-derived subject.",
            "openai": config.public_dict(),
            "prompt": {
                "name": "otp-raw-openai-v1",
                "sha256": hashlib.sha256(RAW_MESSAGE_SYSTEM_INSTRUCTIONS.encode("utf-8")).hexdigest(),
            },
            "routing": dict(Counter(row["route"] for row in details)),
            "started_at_utc": started_at,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "metrics": metric_summary(details),
        "raw_ablation_metrics": raw_metric_summary(details),
        "api_usage": usage_summary(details),
        "runtime": runtime_summary(details, started_wall, started_cpu),
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
    comparison = load_comparison_metrics(args.baseline, regex_payload)
    selected_ids = (
        {int(value.strip()) for value in args.ids.split(",") if value.strip()}
        if args.ids
        else None
    )

    existing_details = []
    if args.resume and output_path.exists():
        existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
        if existing_payload.get("experiment", {}).get("name") != "Raw message + OpenAI":
            raise SystemExit("Resume file belongs to a different experiment architecture.")
        existing_details = [
            row for row in existing_payload.get("detail", []) if not row.get("api_error")
        ]
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
        raw_message = str(source_row["text"])
        api_decision = None
        api_attempted = False
        api_error = ""

        if args.dry_run:
            final_codes = []
            final_url = ""
            route = "DRY_RUN_NO_PREDICTION"
        elif args.max_api_calls is not None and api_calls_this_run >= args.max_api_calls:
            final_codes = []
            final_url = ""
            route = "API_LIMIT_NO_PREDICTION"
        else:
            api_attempted = True
            api_calls_this_run += 1
            route = "RAW_OPENAI"
            try:
                api_decision = api_client.decide_raw_message(raw_message)
                final_codes = api_decision.otp_codes
                final_url = api_decision.verification_url or ""
            except Exception as exc:
                api_error = f"{type(exc).__name__}: {exc}"
                final_codes = []
                final_url = ""
                route = "API_ERROR_NO_PREDICTION"

        # Ground truth is deliberately read only after the API prediction is complete.
        ground_truth = clean(source_row["ground_truth"])
        category = category_from_ground_truth(ground_truth)
        top1_correct = int(category == "otp" and bool(final_codes) and final_codes[0] == ground_truth)
        top3_correct = int(category == "otp" and ground_truth in final_codes[:3])
        link_correct = int(category == "link" and final_url == ground_truth)
        false_extraction = int(category == "negative" and bool(final_codes or final_url))
        working_set_mb, peak_working_set_mb = process_memory_mb()

        details_by_sequence[sequence] = {
            "dataset_id": dataset_id,
            "sequence": sequence,
            "source": clean(source_row["source"]).lower(),
            "category": category,
            "ground_truth": ground_truth,
            "route": route,
            "raw_message_length": len(raw_message),
            "raw_message_sha256": hashlib.sha256(raw_message.encode("utf-8")).hexdigest(),
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
                "row_wall_ms": round((time.perf_counter() - row_started_wall) * 1000, 3),
                "row_cpu_ms": round((time.process_time() - row_started_cpu) * 1000, 3),
                "working_set_mb": working_set_mb,
                "peak_working_set_mb": peak_working_set_mb,
            },
        }
        details = list(details_by_sequence.values())
        full_dataset = selected_ids is None and len(details) == len(dataset)
        write_checkpoint(
            output_path,
            details,
            comparison,
            config,
            args.dry_run,
            full_dataset,
            started_wall,
            started_cpu,
            started_at,
        )
        current = len(details)
        top1 = final_codes[0] if final_codes else (final_url or "NO_OTP")
        print(f"[{current}/{selected_total}] ID={dataset_id} route={route} top1={top1}")

    details = list(details_by_sequence.values())
    full_dataset = selected_ids is None and len(details) == len(dataset)
    write_checkpoint(
        output_path,
        details,
        comparison,
        config,
        args.dry_run,
        full_dataset,
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
