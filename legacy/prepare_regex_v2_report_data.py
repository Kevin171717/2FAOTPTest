import json
import re
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "otp_dataset_dedup.xlsx"
BASELINE_PATH = PROJECT_ROOT / "results" / "reproduction" / "reproduction_paper_results_verified.xlsx"
V2_PATH = PROJECT_ROOT / "results" / "regex_v2" / "regex_v2_experiment_results.json"
OUTPUT_PATH = PROJECT_ROOT / "results" / "regex_v2" / "regex_v2_report_data.json"


RECOVERY_REASONS = {
    "311421": "六位數以連字號分組；v1 只支援空白分組",
    "819668": "數字緊貼後方英文字串（819668This）",
    "GQQCTJ": "純大寫英文字母 OTP；v1 要求至少一個數字",
    "126722": "正文沒有 OTP，只有郵件主旨含正確碼",
    "ZYE-W6Z": "含連字號的英數 OTP",
    "64110198": "八位數緊貼後方英文字串（64110198If）",
}

PATTERN_LABELS = {
    "grouped_numeric": "分組數字",
    "attached_numeric": "緊貼文字的數字",
    "uppercase_alpha": "純大寫字母",
    "hyphenated_alnum": "連字號英數字",
    "bounded_alnum": "邊界完整英數字",
}

RECOVERY_RULES = {
    "311421": "分組數字",
    "819668": "緊貼文字的數字",
    "GQQCTJ": "純大寫字母",
    "126722": "主旨欄位",
    "ZYE-W6Z": "連字號英數字",
    "64110198": "緊貼文字的數字",
}


def normalize(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def top3_values(value):
    return [part.strip() for part in normalize(value).split(",") if part.strip()]


def parse_summary_count(value):
    match = re.search(r"\((\d+)/(\d+)\)", normalize(value))
    if not match:
        raise ValueError(f"Unable to parse summary count: {value!r}")
    return int(match.group(1)), int(match.group(2))


def unique(values):
    return list(dict.fromkeys(normalize(value) for value in values if normalize(value)))


def joined(values, limit=12):
    items = unique(values)
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" ... (+{len(items) - limit})"
    return ", ".join(items)


def main():
    dataset = pd.read_excel(DATASET_PATH, sheet_name="dataset_dedup")
    baseline_detail = pd.read_excel(BASELINE_PATH, sheet_name="Detail")
    baseline_summary = pd.read_excel(BASELINE_PATH, sheet_name="Summary")
    v2_payload = json.loads(V2_PATH.read_text(encoding="utf-8"))
    v2_detail = v2_payload["detail"]

    if len(dataset) != len(v2_detail) or len(dataset) != len(baseline_detail):
        raise ValueError("Dataset, baseline, and Regex v2 result row counts differ.")

    baseline_by_row = {
        int(record["row"]): record for record in baseline_detail.to_dict(orient="records")
    }

    otp_rows = []
    recovered = []
    final_misses = []
    negative_rows = []

    for result in v2_detail:
        seq = int(result["row"])
        source_row = dataset.iloc[seq - 1]
        dataset_id = int(source_row["id"])
        category = normalize(result["category"]).lower()
        ground_truth = normalize(result["ground_truth"])
        baseline = baseline_by_row[seq]

        v1_body = unique(result["regex_v1_body_candidates"])
        v1_combined = unique(result["regex_v1_body_subject_candidates"])
        v2_body = unique(result["regex_v2_body_candidates"])
        v2_combined = unique(result["regex_v2_body_subject_candidates"])
        v2_patterns = unique(result["regex_v2_candidate_patterns"])

        if category == "otp":
            baseline_top1 = normalize(baseline["BGE Top-1"])
            baseline_top3 = top3_values(baseline["BGE Top-3"])
            v2_top1 = normalize(result["BGE Top-1"])
            v2_top3 = top3_values(result["BGE Top-3"])

            detail = {
                "dataset_id": dataset_id,
                "sequence": seq,
                "source": normalize(result["source"]),
                "ground_truth": ground_truth,
                "subject_proxy": normalize(result["subject_proxy"]),
                "subject_proxy_used": 1 if result["subject_proxy_used"] else 0,
                "v1_body_hit": 1 if result["regex_v1_body_hit"] else 0,
                "v1_combined_hit": 1 if result["regex_v1_body_subject_hit"] else 0,
                "v2_body_hit": 1 if result["regex_v2_body_hit"] else 0,
                "v2_combined_hit": 1 if result["regex_v2_body_subject_hit"] else 0,
                "v1_candidate_count": len(v1_body),
                "v2_candidate_count": len(v2_combined),
                "v2_extra_candidate_count": max(0, len(v2_combined) - len(v1_body)),
                "v2_patterns": joined(PATTERN_LABELS.get(p, p) for p in v2_patterns),
                "v2_candidates": joined(v2_combined),
                "baseline_top1": baseline_top1,
                "baseline_top1_hit": 1 if baseline_top1 == ground_truth else 0,
                "baseline_top3": ", ".join(baseline_top3),
                "baseline_top3_hit": 1 if ground_truth in baseline_top3 else 0,
                "v2_top1": v2_top1,
                "v2_top1_hit": 1 if v2_top1 == ground_truth else 0,
                "v2_top3": ", ".join(v2_top3),
                "v2_top3_hit": 1 if ground_truth in v2_top3 else 0,
            }
            otp_rows.append(detail)

            if ground_truth in RECOVERY_REASONS:
                recovered.append(
                    {
                        "dataset_id": dataset_id,
                        "source": normalize(result["source"]),
                        "ground_truth": ground_truth,
                        "v1_miss_reason": RECOVERY_REASONS[ground_truth],
                        "v2_rule": RECOVERY_RULES[ground_truth],
                        "v2_body_hit": "是" if result["regex_v2_body_hit"] else "否",
                        "subject_needed": "是" if not result["regex_v2_body_hit"] else "否",
                        "v2_top1": v2_top1,
                        "top1_hit": "是" if v2_top1 == ground_truth else "否",
                        "v2_top3": ", ".join(v2_top3),
                    }
                )

            if ground_truth not in v2_top3:
                final_misses.append(
                    {
                        "dataset_id": dataset_id,
                        "source": normalize(result["source"]),
                        "ground_truth": ground_truth,
                        "v2_top1": v2_top1,
                        "v2_top3": ", ".join(v2_top3),
                        "candidate_found": "是" if result["regex_v2_body_subject_hit"] else "否",
                        "candidate_count": len(v2_combined),
                        "diagnosis": (
                            "候選已找到，但通知分類或 anchor 門檻拒絕輸出"
                            if result["regex_v2_body_subject_hit"] and not v2_top3
                            else "候選已找到，但 BGE/距離排序未進 Top-3"
                            if result["regex_v2_body_subject_hit"]
                            else "Regex 候選漏失"
                        ),
                    }
                )

        elif category == "negative":
            negative_rows.append(
                {
                    "dataset_id": dataset_id,
                    "baseline_false_extraction": 1
                    if normalize(baseline["False Extraction"]).upper() == "YES"
                    else 0,
                    "v2_false_extraction": 1
                    if normalize(result["False Extraction"]).upper() == "YES"
                    else 0,
                    "v2_top3": normalize(result["BGE Top-3"]),
                }
            )

    recall_scenarios = [
        {
            "scenario": "Regex v1：只看正文",
            "hits": sum(row["v1_body_hit"] for row in otp_rows),
            "total": len(otp_rows),
            "scope": "原始候選規則",
        },
        {
            "scenario": "Regex v1：正文＋主旨",
            "hits": sum(row["v1_combined_hit"] for row in otp_rows),
            "total": len(otp_rows),
            "scope": "只增加主旨欄位",
        },
        {
            "scenario": "Regex v2：只看正文",
            "hits": sum(row["v2_body_hit"] for row in otp_rows),
            "total": len(otp_rows),
            "scope": "新增四類 OTP 形態",
        },
        {
            "scenario": "Regex v2：正文＋主旨",
            "hits": sum(row["v2_combined_hit"] for row in otp_rows),
            "total": len(otp_rows),
            "scope": "正式建議版本",
        },
    ]

    baseline_summary_by_metric = {
        normalize(row["Metric"]): parse_summary_count(row["Overall"])
        for row in baseline_summary.to_dict(orient="records")
    }
    v2_summary_by_metric = {
        normalize(row["Metric"]): parse_summary_count(row["Overall"])
        for row in v2_payload["summary"]
    }
    metric_labels = [
        ("OTP Top-1", "OTP Top-1 正確率", "越高越好"),
        ("OTP Top-3", "OTP Top-3 正確率", "越高越好"),
        ("Verification Link Extraction", "驗證連結正確率", "越高越好"),
        (
            "False Extractions on Negative Examples",
            "負樣本誤抽率",
            "越低越好",
        ),
    ]
    final_metrics = []
    for key, label, direction in metric_labels:
        b_hits, b_total = baseline_summary_by_metric[key]
        v_hits, v_total = v2_summary_by_metric[key]
        final_metrics.append(
            {
                "metric": label,
                "direction": direction,
                "baseline_hits": b_hits,
                "baseline_total": b_total,
                "v2_hits": v_hits,
                "v2_total": v_total,
                "delta_hits": v_hits - b_hits,
            }
        )

    recommendations = [
        {
            "priority": "P0",
            "action": "對純大寫與連字號候選加上局部 OTP 語境閘門",
            "why": "v2 會把 STOP、ACCOUNT、日期範圍等文字帶入候選，造成負樣本誤抽上升。",
            "expected": "保留 GQQCTJ、ZYE-W6Z 的 recall，同時降低候選噪音與誤抽率。",
            "validation": "針對六個新增形態與 15 筆負樣本做回歸測試。",
        },
        {
            "priority": "P0",
            "action": "改為每個候選各自計算附近句子／視窗的 OTP 分數",
            "why": "現行排序偏向全域最高 anchor 與距離，頁尾年份有時會比真碼更靠近。",
            "expected": "提升 Top-1，特別是正文同時有年份、郵遞區號與地址的 Email。",
            "validation": "比較 local-window reranker 前後 Top-1 與負樣本誤抽率。",
        },
        {
            "priority": "P1",
            "action": "擴充日期、年份區間、電話、IP、郵遞區號與頁尾地址排除規則",
            "why": "新增連字號／純字母規則後，21-May、2022-2026、地址數字等假陽性更常見。",
            "expected": "降低每封信候選數，改善 Top-3 precision 與推論成本。",
            "validation": "以 hard-negative 清單檢查排除後不傷害 103 筆 OTP recall。",
        },
        {
            "priority": "P1",
            "action": "正式資料使用真實 Email subject，subject 與 body 分欄評分",
            "why": "本資料缺 subject 欄，本次以 note 代理並排除人工註解；仍可能有資料洩漏風險。",
            "expected": "可靠救回正文缺碼案例，又能控制主旨加權。",
            "validation": "補 subject 欄後重新計算 body-only 與 body+subject recall。",
        },
        {
            "priority": "P2",
            "action": "只在候選模糊時呼叫 LLM 或小型 reranker",
            "why": "Regex v2 已負責高 recall，LLM 適合處理多候選排序而非掃描整封訊息。",
            "expected": "以較低 API 成本提高 Top-1，並避免 LLM 憑空生成 OTP。",
            "validation": "只對 Top-1/Top-2 分差低於門檻的樣本做 A/B 測試。",
        },
        {
            "priority": "P2",
            "action": "以 provider 分組切分開發集／測試集並鎖定 regression suite",
            "why": "目前 128 筆同時用於找規則與評估，100% recall 可能過度擬合。",
            "expected": "得到可泛化、可向論文報告的成效估計。",
            "validation": "採 leave-one-provider-out 或固定 holdout 後只在測試集報最終數字。",
        },
    ]

    payload = {
        "metadata": {
            "dataset_rows": len(dataset),
            "otp_rows": len(otp_rows),
            "link_rows": sum(1 for row in v2_detail if normalize(row["category"]).lower() == "link"),
            "negative_rows": len(negative_rows),
            "model": v2_payload["parameters"]["embedding_model"],
            "notification_margin": v2_payload["parameters"]["notification_margin"],
            "subject_proxy_note": (
                "資料集無 subject 欄；本次以 note 作主旨代理，含人工註解標記者不採用。"
            ),
        },
        "recall_scenarios": recall_scenarios,
        "final_metrics": final_metrics,
        "recovered_cases": sorted(recovered, key=lambda row: row["dataset_id"]),
        "final_misses": sorted(final_misses, key=lambda row: row["dataset_id"]),
        "otp_detail": sorted(otp_rows, key=lambda row: row["dataset_id"]),
        "negative_detail": sorted(negative_rows, key=lambda row: row["dataset_id"]),
        "recommendations": recommendations,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Report data: {OUTPUT_PATH}")
    for item in recall_scenarios:
        print(f"{item['scenario']}: {item['hits']}/{item['total']}")
    print(f"Final Top-3 misses: {len(final_misses)}")


if __name__ == "__main__":
    main()
