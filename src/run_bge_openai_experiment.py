from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import torch
from sentence_transformers import util

from openai_otp_fallback import OpenAIConfig, OpenAIFallbackClient, load_env_file
from otp_bge_openai_gate import (
    ROUTE_BGE_DIRECT,
    build_candidate_evidence,
    local_context,
    route_with_bge,
)
from otp_code_extractor_regex_v2 import OTPCodeExtractor


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "otp_dataset_dedup.xlsx"
DEFAULT_BASELINE = PROJECT_ROOT / "results" / "reproduction" / "reproduction_paper_results_verified.xlsx"
DEFAULT_REGEX_V2 = PROJECT_ROOT / "results" / "regex_v2" / "regex_v2_experiment_results.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "bge_openai" / "bge_openai_experiment_results.json"
DEFAULT_DRY_OUTPUT = PROJECT_ROOT / "results" / "bge_openai" / "bge_openai_dry_run.json"

NEGATIVE_LABELS = {"", "none", "null", "nan", "n/a", "na"}
ANNOTATION_MARKERS = re.compile(
    r"真碼|干擾|ground\s*truth|annotation|人工註解", re.IGNORECASE
)
IP_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
YEAR_RANGE_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}[–—-](?:19|20)\d{2}(?!\d)")
MONTH_DATE_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:\d{1,2}[-/](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/]\d{1,2})(?![A-Za-z0-9])"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate Regex v2 + BGE + OpenAI fallback."
    )
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


def unique_codes(value) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = clean(value).split(",")
    return list(dict.fromkeys(clean(item) for item in items if clean(item)))


def ranges_for_pattern(pattern: re.Pattern, text: str, tag: str) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), tag) for match in pattern.finditer(text)]


def candidate_records(extractor: OTPCodeExtractor, text: str) -> list[dict]:
    excluded_ranges = []
    for url in extractor.url_extractor.find_urls(text):
        if not str(url).startswith(("http://", "https://")):
            continue
        start_from = 0
        while True:
            start = text.find(url, start_from)
            if start < 0:
                break
            excluded_ranges.append((start, start + len(url), "URL"))
            start_from = start + len(url)

    for noise_pattern in extractor.noise_patterns:
        excluded_ranges.extend(
            (match.start(), match.end(), "DATE_TIME_OR_LONG_NUMBER")
            for match in re.finditer(noise_pattern, text)
        )
    excluded_ranges.extend(ranges_for_pattern(IP_PATTERN, text, "IP_ADDRESS"))
    excluded_ranges.extend(ranges_for_pattern(YEAR_RANGE_PATTERN, text, "YEAR_RANGE"))
    excluded_ranges.extend(ranges_for_pattern(MONTH_DATE_PATTERN, text, "MONTH_DATE"))

    records = []
    for record in extractor.find_otp_candidate_records(text):
        containing = [
            tag
            for start, end, tag in excluded_ranges
            if start <= record["start"] and record["end"] <= end
        ]
        if not containing:
            records.append(dict(record))
    return records


def score_occurrences(
    extractor: OTPCodeExtractor,
    text: str,
    records: list[dict],
) -> list[tuple[float, float]]:
    if not records:
        return []

    query_contexts = []
    for record in records:
        context = local_context(text, record["start"], record["end"])
        masked = context.replace(record["raw"], "[OTP]", 1)
        query_contexts.append("為這個句子生成表示以用於檢索相關文章: " + masked)

    with torch.no_grad():
        embeddings = extractor.bge_model.encode(
            query_contexts,
            normalize_embeddings=True,
            convert_to_tensor=True,
        )
        otp_scores = util.cos_sim(
            embeddings, extractor.otp_template_embeddings
        ).max(dim=1).values
        notification_scores = util.cos_sim(
            embeddings, extractor.notification_template_embeddings
        ).max(dim=1).values

    return [
        (float(otp_score.item()), float(notification_score.item()))
        for otp_score, notification_score in zip(otp_scores, notification_scores)
    ]


def deduplicate_records(
    records: list[dict],
    scores: list[tuple[float, float]],
) -> tuple[list[dict], dict[str, tuple[float, float]]]:
    best_by_code = {}
    for record, (otp_score, notification_score) in zip(records, scores):
        code = clean(record["code"])
        current = best_by_code.get(code)
        if current is None or otp_score > current[1]:
            best_by_code[code] = (record, otp_score, notification_score)

    ordered = sorted(best_by_code.values(), key=lambda item: item[0]["start"])
    deduplicated = []
    semantic_scores = {}
    for index, (record, otp_score, notification_score) in enumerate(ordered):
        item = dict(record)
        item["candidate_id"] = f"C{index}"
        deduplicated.append(item)
        semantic_scores[item["candidate_id"]] = (otp_score, notification_score)
    return deduplicated, semantic_scores


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
        "verification_link": {
            "hits": sum(row["link_correct"] for row in links),
            "total": len(links),
        },
        "negative_false_extraction": {
            "hits": sum(row["false_extraction"] for row in negatives),
            "total": len(negatives),
        },
    }


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
    for row in successful_rows:
        usage = row["api"]["usage"]
        for key in usage_keys:
            totals[key] += usage.get(key, 0)
    totals["estimated_cost_usd"] = round(totals["estimated_cost_usd"], 8)
    totals["api_calls"] = len(attempted_rows)
    totals["successful_calls"] = len(successful_rows)
    totals["failed_calls"] = len(attempted_rows) - len(successful_rows)
    totals["average_latency_ms"] = (
        round(sum(row["api"]["latency_ms"] for row in successful_rows) / len(successful_rows))
        if successful_rows
        else 0
    )
    totals["estimated_cost_per_1000_messages_usd"] = (
        round(totals["estimated_cost_usd"] / len(details) * 1000, 6)
        if details
        else 0
    )
    return totals


def write_checkpoint(
    output_path: Path,
    details: list[dict],
    comparison: list[dict],
    config: OpenAIConfig,
    dry_run: bool,
    full_dataset: bool,
    thresholds: dict,
) -> None:
    payload = {
        "experiment": {
            "name": "Regex v2 + BGE + OpenAI fallback",
            "dry_run": dry_run,
            "openai_evaluation_valid": not dry_run and full_dataset,
            "ground_truth_used_for_routing": False,
            "openai": config.public_dict(),
            "thresholds": thresholds,
            "routing": dict(Counter(row["route"] for row in details)),
        },
        "metrics": metric_summary(details),
        "api_usage": usage_summary(details),
        "comparison": comparison,
        "detail": sorted(details, key=lambda row: row["sequence"]),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    args = parse_args()
    load_env_file(args.env_file)
    config = OpenAIConfig.from_environment()
    thresholds = {
        "anchor_threshold": float(os.getenv("BGE_ANCHOR_THRESHOLD", "0.50")),
        "score_gap_threshold": float(os.getenv("OPENAI_SCORE_GAP_THRESHOLD", "0.05")),
        "notification_margin": float(os.getenv("OPENAI_NOTIFICATION_MARGIN", "0.03")),
    }
    output_path = args.output or (DEFAULT_DRY_OUTPUT if args.dry_run else DEFAULT_OUTPUT)

    if not args.dry_run and not config.api_key:
        raise SystemExit(f"OPENAI_API_KEY is empty. Fill it in {args.env_file} and run again.")

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    dataset = pd.read_excel(
        args.dataset,
        sheet_name="dataset_dedup",
        dtype=str,
        keep_default_na=False,
    )
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
        if existing_payload.get("experiment", {}).get("name") != "Regex v2 + BGE + OpenAI fallback":
            raise SystemExit("Resume file belongs to a different experiment architecture.")
        existing_details = existing_payload.get("detail", [])
    details_by_sequence = {int(row["sequence"]): row for row in existing_details}

    print("Loading BGE model for candidate-local semantic scores...")
    extractor = OTPCodeExtractor(notification_margin=thresholds["notification_margin"])
    api_client = None if args.dry_run else OpenAIFallbackClient(config)
    api_calls_this_run = 0

    for zero_index, source_row in dataset.iterrows():
        sequence = zero_index + 1
        dataset_id = int(source_row["id"])
        if selected_ids is not None and dataset_id not in selected_ids:
            continue
        if sequence in details_by_sequence:
            continue

        regex_result = regex_by_sequence[sequence]
        text = clean(source_row["text"])
        subject = subject_proxy_from_note(source_row.get("note", ""))
        combined_text = f"{subject}\n{text}" if subject else text
        bge_top3 = unique_codes(regex_result["BGE Top-3"])
        final_url = clean(regex_result["URL Prediction"])
        api_decision = None
        api_attempted = False
        api_error = ""

        occurrences = candidate_records(extractor, combined_text)
        occurrence_scores = score_occurrences(extractor, combined_text, occurrences)
        records, semantic_scores = deduplicate_records(occurrences, occurrence_scores)
        candidates = build_candidate_evidence(combined_text, records, semantic_scores)
        gate = route_with_bge(
            combined_text,
            candidates,
            bge_top3,
            anchor_threshold=thresholds["anchor_threshold"],
            score_gap_threshold=thresholds["score_gap_threshold"],
            notification_margin=thresholds["notification_margin"],
        )
        route = gate.route
        route_reasons = gate.reasons
        candidate_by_id = {
            candidate.candidate_id: candidate.value for candidate in candidates
        }

        if route == ROUTE_BGE_DIRECT:
            final_codes = bge_top3
        elif args.dry_run:
            final_codes = bge_top3
            route = "DRY_RUN_OPENAI_FALLBACK_TO_BGE"
        elif args.max_api_calls is not None and api_calls_this_run >= args.max_api_calls:
            final_codes = bge_top3
            route = "API_LIMIT_FALLBACK_TO_BGE"
        else:
            api_attempted = True
            api_calls_this_run += 1
            try:
                api_decision = api_client.decide(
                    subject=subject,
                    message=combined_text,
                    candidates=[candidate.to_dict() for candidate in candidates],
                    route_reasons=route_reasons,
                    bge_top3=bge_top3,
                )
                final_codes = [
                    candidate_by_id[candidate_id]
                    for candidate_id in api_decision.ranked_candidate_ids
                ]
            except Exception as exc:
                api_error = f"{type(exc).__name__}: {exc}"
                final_codes = bge_top3
                route = "API_ERROR_FALLBACK_TO_BGE"

        # Labels are read only after the prediction path has completed.
        ground_truth = clean(source_row["ground_truth"])
        category = category_from_ground_truth(ground_truth)
        top1_correct = int(category == "otp" and bool(final_codes) and final_codes[0] == ground_truth)
        top3_correct = int(category == "otp" and ground_truth in final_codes[:3])
        link_correct = int(category == "link" and final_url == ground_truth)
        false_extraction = int(category == "negative" and bool(final_codes or final_url))

        result = {
            "dataset_id": dataset_id,
            "sequence": sequence,
            "source": clean(source_row["source"]).lower(),
            "category": category,
            "ground_truth": ground_truth,
            "subject_proxy": subject,
            "route": route,
            "route_reasons": route_reasons,
            "candidates": [candidate.to_dict() for candidate in candidates],
            "bge_top1": bge_top3[0] if bge_top3 else "",
            "bge_top3": bge_top3[:3],
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
            thresholds,
        )
        print(
            f"[{len(details)}/{len(dataset)}] ID={dataset_id} route={route} "
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
        thresholds,
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    print(json.dumps(payload["api_usage"], ensure_ascii=False, indent=2))
    print(f"Result JSON: {output_path}")


if __name__ == "__main__":
    main()
