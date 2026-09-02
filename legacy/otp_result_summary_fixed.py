"""
Standalone evaluator for the OpenAI-enhanced OTP extractor.

This script DOES NOT modify the original extractor.

It correctly separates:
    - OTP cases
    - Verification-link cases
    - Negative cases

For OTP cases it records BOTH:
    - BASELINE: BGE ranking before OpenAI reranking
    - PROPOSED: BGE + OpenAI ranking after reranking

For URL ground-truth rows, credential_preference is forced to "link"
so that the extractor actually enters extract_url_value().

Output:
    otp_experiment_results_fixed.xlsx

Sheets:
    - Detail
    - Summary

Environment variables:
    OTP_EXTRACTOR_PATH
        Path to OpenAI-enhanced extractor .py

    OTP_DATASET_PATH
        Path to dataset .xlsx

    OTP_RESULT_PATH
        Optional output .xlsx path
"""

import os
import sys
import importlib.util
from pathlib import Path

import pandas as pd


# ============================================================
# 1. Paths
# ============================================================

EXTRACTOR_PATH = os.getenv(
    "OTP_EXTRACTOR_PATH",
    "/content/otp_code_extractor_openai_annotated.py",
)

DATASET_PATH = os.getenv(
    "OTP_DATASET_PATH",
    "/content/otp_dataset_dedup.xlsx",
)

RESULT_PATH = os.getenv(
    "OTP_RESULT_PATH",
    "/content/otp_experiment_results_fixed.xlsx",
)


# ============================================================
# 2. Dynamically import OpenAI-enhanced extractor
# ============================================================

def load_extractor_class(py_path: str):
    py_path = str(Path(py_path).resolve())

    if not os.path.exists(py_path):
        raise FileNotFoundError(
            f"Extractor file not found: {py_path}\n"
            f"Set OTP_EXTRACTOR_PATH to the correct Colab path."
        )

    spec = importlib.util.spec_from_file_location(
        "otp_extractor_openai",
        py_path,
    )

    if spec is None or spec.loader is None:
        raise ImportError(
            f"Unable to import extractor from: {py_path}"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules["otp_extractor_openai"] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "OTPCodeExtractor"):
        raise AttributeError(
            "OTPCodeExtractor class was not found in the extractor file."
        )

    return module.OTPCodeExtractor


OTPCodeExtractor = load_extractor_class(EXTRACTOR_PATH)
extractor = OTPCodeExtractor()


# ============================================================
# 3. Runtime wrapper to capture BGE ranking BEFORE OpenAI
#
# Original OpenAI flow:
#     BGE Top-K candidates
#       -> _llm_rerank_otp_candidates(...)
#       -> Final Top-3
#
# We intercept the arguments passed into the existing reranker.
# This does NOT modify the extractor source file.
# ============================================================

if not hasattr(extractor, "_llm_rerank_otp_candidates"):
    raise AttributeError(
        "The loaded extractor does not contain "
        "_llm_rerank_otp_candidates(). "
        "Please point OTP_EXTRACTOR_PATH to the OpenAI-enhanced extractor."
    )

_original_llm_reranker = extractor._llm_rerank_otp_candidates


def capture_llm_reranker(text, candidates):
    # candidates are BGE Top-K BEFORE OpenAI reranking
    extractor._experiment_bge_candidates = [
        dict(c) for c in candidates
    ]

    result = _original_llm_reranker(
        text,
        candidates,
    )

    # result is Final Top-3 AFTER OpenAI reranking
    extractor._experiment_llm_candidates = [
        dict(c) for c in result
    ]

    return result


extractor._llm_rerank_otp_candidates = capture_llm_reranker


# ============================================================
# 4. Helpers
# ============================================================

NEGATIVE_LABELS = {
    "",
    "none",
    "null",
    "nan",
    "n/a",
    "na",
}


def clean_text_value(value):
    if value is None:
        return ""
    return str(value).strip()


def clean_code(value):
    """
    Preserve leading zeroes while removing spaces:
        408 785 -> 408785
        061960  -> 061960
    """
    return clean_text_value(value).replace(" ", "")


def normalize_url(value):
    """
    Minimal normalization only.
    URL metric uses exact string comparison.
    """
    return clean_text_value(value)


def codes_from_candidates(candidates):
    return [
        clean_code(c.get("code"))
        for c in (candidates or [])
        if clean_code(c.get("code"))
    ]


def top3_text(codes):
    return ", ".join(codes[:3])


def safe_pct(num, den):
    if den == 0:
        return 0.0
    return num / den


def metric_text(hit, total):
    if total == 0:
        return "—"

    return (
        f"{safe_pct(hit, total) * 100:.1f}% "
        f"({hit}/{total})"
    )


# ============================================================
# 5. Load dataset
# ============================================================

if not os.path.exists(DATASET_PATH):
    raise FileNotFoundError(
        f"Dataset not found: {DATASET_PATH}\n"
        f"Set OTP_DATASET_PATH to the correct Colab path."
    )

# dtype=str preserves values such as 061960.
df = pd.read_excel(
    DATASET_PATH,
    dtype=str,
    keep_default_na=False,
)

df.columns = [
    str(c).strip().lower()
    for c in df.columns
]

if "text" not in df.columns:
    raise ValueError(
        "Dataset must contain a 'text' column."
    )

if "ground_truth" not in df.columns:
    raise ValueError(
        "Dataset must contain a 'ground_truth' column."
    )


# ============================================================
# 6. Evaluation accumulators
# ============================================================

results = []

# OTP metrics by source
otp_stats = {
    "sms": {
        "total": 0,
        "bge_top1": 0,
        "bge_top3": 0,
        "llm_top1": 0,
        "llm_top3": 0,
        "saved": 0,
        "regression": 0,
    },
    "email": {
        "total": 0,
        "bge_top1": 0,
        "bge_top3": 0,
        "llm_top1": 0,
        "llm_top3": 0,
        "saved": 0,
        "regression": 0,
    },
}

# Link metrics by source
link_stats = {
    "sms": {"total": 0, "hit": 0},
    "email": {"total": 0, "hit": 0},
}

# Negative metrics by source
negative_stats = {
    "sms": {"total": 0, "false_extraction": 0},
    "email": {"total": 0, "false_extraction": 0},
}


# ============================================================
# 7. Run evaluation
# ============================================================

for idx, row in df.iterrows():

    text = clean_text_value(
        row.get("text", "")
    )

    gt_raw = clean_text_value(
        row.get("ground_truth", "")
    )

    gt_lower = gt_raw.lower()

    source = clean_text_value(
        row.get("source", "")
    ).lower() or "email"

    if source not in {"sms", "email", "app"}:
        source = "email"

    original_pref = clean_text_value(
        row.get("credential_preference", "")
    ).lower()

    row_type = clean_text_value(
        row.get("type", "")
    ).lower()

    # --------------------------------------------------------
    # Ground-truth category
    # --------------------------------------------------------

    is_negative = gt_lower in NEGATIVE_LABELS

    is_url_case = (
        (not is_negative)
        and gt_lower.startswith(
            ("http://", "https://")
        )
    )

    is_otp_case = (
        (not is_negative)
        and (not is_url_case)
    )

    if is_url_case:
        category = "link"
    elif is_otp_case:
        category = "otp"
    else:
        category = "negative"

    # --------------------------------------------------------
    # SAME FIX AS ORIGINAL SUMMARY:
    # URL ground truth MUST enter the URL extractor.
    # --------------------------------------------------------

    if is_url_case:
        eval_pref = "link"

    elif original_pref:
        eval_pref = original_pref

    else:
        if row_type == "otp+link":
            eval_pref = "otp+link"
        elif row_type == "link":
            eval_pref = "link"
        else:
            eval_pref = "otp"

    if eval_pref not in {
        "otp",
        "link",
        "otp+link",
        "none",
    }:
        eval_pref = "otp"

    print("\n" + "=" * 80)
    print(f"ROW {idx}")
    print("Category:", category)
    print("Ground Truth:", gt_raw)
    print("Source:", source)
    print(
        "Original preference:",
        original_pref or "(blank)"
    )
    print(
        "Evaluation preference:",
        eval_pref
    )

    # Reset captured rankings for this row.
    extractor._experiment_bge_candidates = []
    extractor._experiment_llm_candidates = []

    error = ""

    try:
        final_candidates, url_value = extractor.extract_otp_code(
            {"text": text},
            source,
            eval_pref,
        )

        bge_candidates = getattr(
            extractor,
            "_experiment_bge_candidates",
            [],
        )

        llm_candidates = getattr(
            extractor,
            "_experiment_llm_candidates",
            final_candidates or [],
        )

        bge_codes = codes_from_candidates(
            bge_candidates
        )

        llm_codes = codes_from_candidates(
            llm_candidates
        )

    except Exception as exc:
        bge_codes = []
        llm_codes = []
        url_value = None
        error = str(exc)
        print("ERROR:", error)

    bge_top1 = (
        bge_codes[0]
        if bge_codes
        else ""
    )

    llm_top1 = (
        llm_codes[0]
        if llm_codes
        else ""
    )

    predicted_url = normalize_url(
        url_value
    )

    # Default flags
    bge_top1_correct = False
    bge_top3_correct = False
    llm_top1_correct = False
    llm_top3_correct = False

    url_correct = False
    false_extraction = False
    llm_status = "—"

    # --------------------------------------------------------
    # OTP case
    # --------------------------------------------------------

    if is_otp_case:

        expected_code = clean_code(
            gt_raw
        )

        bge_top1_correct = (
            bool(bge_codes)
            and bge_codes[0] == expected_code
        )

        bge_top3_correct = (
            expected_code in bge_codes[:3]
        )

        llm_top1_correct = (
            bool(llm_codes)
            and llm_codes[0] == expected_code
        )

        llm_top3_correct = (
            expected_code in llm_codes[:3]
        )

        if (
            (not bge_top1_correct)
            and llm_top1_correct
        ):
            llm_status = "YES"

        elif (
            bge_top1_correct
            and (not llm_top1_correct)
        ):
            llm_status = "REGRESSION"

        if source in otp_stats:
            otp_stats[source]["total"] += 1

            otp_stats[source]["bge_top1"] += int(
                bge_top1_correct
            )

            otp_stats[source]["bge_top3"] += int(
                bge_top3_correct
            )

            otp_stats[source]["llm_top1"] += int(
                llm_top1_correct
            )

            otp_stats[source]["llm_top3"] += int(
                llm_top3_correct
            )

            otp_stats[source]["saved"] += int(
                llm_status == "YES"
            )

            otp_stats[source]["regression"] += int(
                llm_status == "REGRESSION"
            )

    # --------------------------------------------------------
    # Verification-link case
    # --------------------------------------------------------

    elif is_url_case:

        expected_url = normalize_url(
            gt_raw
        )

        # Exact match
        url_correct = (
            bool(predicted_url)
            and predicted_url == expected_url
        )

        if source in link_stats:
            link_stats[source]["total"] += 1
            link_stats[source]["hit"] += int(
                url_correct
            )

    # --------------------------------------------------------
    # Negative case
    # --------------------------------------------------------

    else:

        # Final system output is used for negative false-extraction.
        # This keeps the metric aligned with the OpenAI-enhanced system.
        false_extraction = (
            bool(llm_codes)
            or bool(predicted_url)
        )

        if source in negative_stats:
            negative_stats[source]["total"] += 1
            negative_stats[source]["false_extraction"] += int(
                false_extraction
            )

    # --------------------------------------------------------
    # Detail row
    # --------------------------------------------------------

    results.append({
        "row": idx + 1,
        "category": category,
        "ground_truth": gt_raw,

        "BGE Top-1": bge_top1,
        "BGE Top-3": top3_text(
            bge_codes
        ),

        "LLM Top-1": llm_top1,
        "LLM Top-3": top3_text(
            llm_codes
        ),

        "BGE Correct": (
            "✅"
            if is_otp_case and bge_top1_correct
            else (
                "❌"
                if is_otp_case
                else "—"
            )
        ),

        "LLM Correct": (
            "✅"
            if is_otp_case and llm_top1_correct
            else (
                "❌"
                if is_otp_case
                else "—"
            )
        ),

        "LLM Saved": llm_status,

        "BGE Top-3 Correct": (
            "✅"
            if is_otp_case and bge_top3_correct
            else (
                "❌"
                if is_otp_case
                else "—"
            )
        ),

        "LLM Top-3 Correct": (
            "✅"
            if is_otp_case and llm_top3_correct
            else (
                "❌"
                if is_otp_case
                else "—"
            )
        ),

        "URL Prediction": predicted_url,

        "URL Correct": (
            "✅"
            if is_url_case and url_correct
            else (
                "❌"
                if is_url_case
                else "—"
            )
        ),

        "False Extraction": (
            "YES"
            if is_negative and false_extraction
            else (
                "NO"
                if is_negative
                else "—"
            )
        ),

        "source": source,
        "original_credential_preference": original_pref,
        "evaluation_credential_preference": eval_pref,
        "type": row_type,
        "error": error,
    })

    print(
        "BGE FINAL:",
        bge_codes[:3]
    )

    print(
        "LLM FINAL:",
        llm_codes[:3]
    )

    print(
        "URL FINAL:",
        predicted_url or None
    )

    print(
        "LLM STATUS:",
        llm_status
    )


# ============================================================
# 8. Aggregate metrics
# ============================================================

sms_otp = otp_stats["sms"]
email_otp = otp_stats["email"]

overall_otp_total = (
    sms_otp["total"]
    + email_otp["total"]
)

overall_bge_top1 = (
    sms_otp["bge_top1"]
    + email_otp["bge_top1"]
)

overall_bge_top3 = (
    sms_otp["bge_top3"]
    + email_otp["bge_top3"]
)

overall_llm_top1 = (
    sms_otp["llm_top1"]
    + email_otp["llm_top1"]
)

overall_llm_top3 = (
    sms_otp["llm_top3"]
    + email_otp["llm_top3"]
)

overall_saved = (
    sms_otp["saved"]
    + email_otp["saved"]
)

overall_regression = (
    sms_otp["regression"]
    + email_otp["regression"]
)


sms_link = link_stats["sms"]
email_link = link_stats["email"]

overall_link_total = (
    sms_link["total"]
    + email_link["total"]
)

overall_link_hit = (
    sms_link["hit"]
    + email_link["hit"]
)


sms_neg = negative_stats["sms"]
email_neg = negative_stats["email"]

overall_neg_total = (
    sms_neg["total"]
    + email_neg["total"]
)

overall_false = (
    sms_neg["false_extraction"]
    + email_neg["false_extraction"]
)


# ============================================================
# 9. Build paper-style Summary tables
# ============================================================

baseline_summary = pd.DataFrame([
    {
        "Metric": "OTP Top-1",
        "SMS": metric_text(
            sms_otp["bge_top1"],
            sms_otp["total"],
        ),
        "Email": metric_text(
            email_otp["bge_top1"],
            email_otp["total"],
        ),
        "Overall": metric_text(
            overall_bge_top1,
            overall_otp_total,
        ),
    },
    {
        "Metric": "OTP Top-3",
        "SMS": metric_text(
            sms_otp["bge_top3"],
            sms_otp["total"],
        ),
        "Email": metric_text(
            email_otp["bge_top3"],
            email_otp["total"],
        ),
        "Overall": metric_text(
            overall_bge_top3,
            overall_otp_total,
        ),
    },
    {
        "Metric": "Verification Link Extraction",
        "SMS": metric_text(
            sms_link["hit"],
            sms_link["total"],
        ),
        "Email": metric_text(
            email_link["hit"],
            email_link["total"],
        ),
        "Overall": metric_text(
            overall_link_hit,
            overall_link_total,
        ),
    },
    {
        "Metric": "False Extractions on Negative Examples",
        "SMS": metric_text(
            sms_neg["false_extraction"],
            sms_neg["total"],
        ),
        "Email": metric_text(
            email_neg["false_extraction"],
            email_neg["total"],
        ),
        "Overall": metric_text(
            overall_false,
            overall_neg_total,
        ),
    },
])


proposed_summary = pd.DataFrame([
    {
        "Metric": "OTP Top-1",
        "SMS": metric_text(
            sms_otp["llm_top1"],
            sms_otp["total"],
        ),
        "Email": metric_text(
            email_otp["llm_top1"],
            email_otp["total"],
        ),
        "Overall": metric_text(
            overall_llm_top1,
            overall_otp_total,
        ),
    },
    {
        "Metric": "OTP Top-3",
        "SMS": metric_text(
            sms_otp["llm_top3"],
            sms_otp["total"],
        ),
        "Email": metric_text(
            email_otp["llm_top3"],
            email_otp["total"],
        ),
        "Overall": metric_text(
            overall_llm_top3,
            overall_otp_total,
        ),
    },
    {
        "Metric": "Verification Link Extraction",
        "SMS": metric_text(
            sms_link["hit"],
            sms_link["total"],
        ),
        "Email": metric_text(
            email_link["hit"],
            email_link["total"],
        ),
        "Overall": metric_text(
            overall_link_hit,
            overall_link_total,
        ),
    },
    {
        "Metric": "False Extractions on Negative Examples",
        "SMS": metric_text(
            sms_neg["false_extraction"],
            sms_neg["total"],
        ),
        "Email": metric_text(
            email_neg["false_extraction"],
            email_neg["total"],
        ),
        "Overall": metric_text(
            overall_false,
            overall_neg_total,
        ),
    },
])


detail_df = pd.DataFrame(
    results
)


# ============================================================
# 10. Export XLSX
# ============================================================

with pd.ExcelWriter(
    RESULT_PATH,
    engine="openpyxl",
) as writer:

    detail_df.to_excel(
        writer,
        sheet_name="Detail",
        index=False,
    )

    # BASELINE first
    baseline_summary.to_excel(
        writer,
        sheet_name="Summary",
        index=False,
        startrow=1,
    )

    summary_ws = writer.book["Summary"]

    summary_ws["A1"] = "BASELINE: BGE"

    # PROPOSED second
    proposed_startrow = len(
        baseline_summary
    ) + 5

    proposed_summary.to_excel(
        writer,
        sheet_name="Summary",
        index=False,
        startrow=proposed_startrow,
    )

    summary_ws.cell(
        row=proposed_startrow,
        column=1,
        value="PROPOSED: BGE + OPENAI",
    )

    # Additional LLM effect summary
    llm_info_row = (
        proposed_startrow
        + len(proposed_summary)
        + 3
    )

    summary_ws.cell(
        row=llm_info_row,
        column=1,
        value="LLM saved BGE errors",
    )

    summary_ws.cell(
        row=llm_info_row,
        column=2,
        value=overall_saved,
    )

    summary_ws.cell(
        row=llm_info_row + 1,
        column=1,
        value="LLM caused regression",
    )

    summary_ws.cell(
        row=llm_info_row + 1,
        column=2,
        value=overall_regression,
    )

    # Dataset breakdown
    breakdown_row = (
        llm_info_row + 4
    )

    summary_ws.cell(
        row=breakdown_row,
        column=1,
        value="Dataset Breakdown",
    )

    breakdown_values = [
        (
            "OTP",
            overall_otp_total,
        ),
        (
            "Verification Link",
            overall_link_total,
        ),
        (
            "Negative",
            overall_neg_total,
        ),
        (
            "Total",
            (
                overall_otp_total
                + overall_link_total
                + overall_neg_total
            ),
        ),
    ]

    for offset, (label, value) in enumerate(
        breakdown_values,
        start=1,
    ):
        summary_ws.cell(
            row=breakdown_row + offset,
            column=1,
            value=label,
        )
        summary_ws.cell(
            row=breakdown_row + offset,
            column=2,
            value=value,
        )

    detail_ws = writer.book[
        "Detail"
    ]

    detail_ws.freeze_panes = "A2"

    # Detail widths
    widths = {
        "A": 8,
        "B": 12,
        "C": 55,
        "D": 18,
        "E": 40,
        "F": 18,
        "G": 40,
        "H": 16,
        "I": 16,
        "J": 18,
        "K": 20,
        "L": 20,
        "M": 70,
        "N": 16,
        "O": 18,
        "P": 12,
        "Q": 30,
        "R": 32,
        "S": 16,
        "T": 50,
    }

    for col_letter, width in widths.items():
        detail_ws.column_dimensions[
            col_letter
        ].width = width

    summary_ws.column_dimensions[
        "A"
    ].width = 44

    summary_ws.column_dimensions[
        "B"
    ].width = 24

    summary_ws.column_dimensions[
        "C"
    ].width = 24

    summary_ws.column_dimensions[
        "D"
    ].width = 24


# ============================================================
# 11. Console summary
# ============================================================

print("\n")
print("=" * 72)
print("DATASET BREAKDOWN")
print("=" * 72)

print(
    f"OTP: {overall_otp_total} "
    f"(SMS={sms_otp['total']}, Email={email_otp['total']})"
)

print(
    f"Link: {overall_link_total} "
    f"(SMS={sms_link['total']}, Email={email_link['total']})"
)

print(
    f"Negative: {overall_neg_total} "
    f"(SMS={sms_neg['total']}, Email={email_neg['total']})"
)

print(
    f"Total classified: "
    f"{overall_otp_total + overall_link_total + overall_neg_total}"
)


print("\n")
print("==============================")
print("BASELINE: BGE")
print("==============================")

print(
    f"Top-1: "
    f"{overall_bge_top1} / {overall_otp_total}"
)

print(
    f"Top-1 Accuracy: "
    f"{safe_pct(overall_bge_top1, overall_otp_total) * 100:.1f}%"
)

print()

print(
    f"Top-3: "
    f"{overall_bge_top3} / {overall_otp_total}"
)

print(
    f"Top-3 Accuracy: "
    f"{safe_pct(overall_bge_top3, overall_otp_total) * 100:.1f}%"
)


print("\n")
print("==============================")
print("PROPOSED: BGE + OPENAI")
print("==============================")

print(
    f"Top-1: "
    f"{overall_llm_top1} / {overall_otp_total}"
)

print(
    f"Top-1 Accuracy: "
    f"{safe_pct(overall_llm_top1, overall_otp_total) * 100:.1f}%"
)

print()

print(
    f"Top-3: "
    f"{overall_llm_top3} / {overall_otp_total}"
)

print(
    f"Top-3 Accuracy: "
    f"{safe_pct(overall_llm_top3, overall_otp_total) * 100:.1f}%"
)

print()

print(
    f"LLM saved BGE errors: "
    f"{overall_saved}"
)

print(
    f"LLM caused regression: "
    f"{overall_regression}"
)


print("\n")
print("==============================")
print("LINK / NEGATIVE")
print("==============================")

print(
    "Verification Link:",
    metric_text(
        overall_link_hit,
        overall_link_total,
    ),
)

print(
    "False Extractions on Negatives:",
    metric_text(
        overall_false,
        overall_neg_total,
    ),
)

print("\nBASELINE detail:")
print(
    baseline_summary.to_string(
        index=False
    )
)

print("\nPROPOSED detail:")
print(
    proposed_summary.to_string(
        index=False
    )
)

print("\nResult XLSX:")
print(RESULT_PATH)
