"""
Standalone evaluator for the ORIGINAL otp_code_extractor.py
(no OpenAI / no LLM reranking).

This script DOES NOT modify the original extractor.

It correctly separates:
    - OTP cases
    - Verification-link cases
    - Negative cases

For URL ground-truth rows, credential_preference is forced to "link"
so that the original extractor actually enters extract_url_value().

Output:
    otp_original_experiment_results_fixed.xlsx

Sheets:
    - Detail
    - Summary

Environment variables:
    OTP_ORIGINAL_EXTRACTOR_PATH
        Path to original otp_code_extractor.py

    OTP_DATASET_PATH
        Path to dataset .xlsx

    OTP_ORIGINAL_RESULT_PATH
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
    "OTP_ORIGINAL_EXTRACTOR_PATH",
    "/content/otp_code_extractor.py",
)

DATASET_PATH = os.getenv(
    "OTP_DATASET_PATH",
    "/content/otp_dataset_dedup.xlsx",
)

RESULT_PATH = os.getenv(
    "OTP_ORIGINAL_RESULT_PATH",
    "/content/otp_original_experiment_results_fixed.xlsx",
)


# ============================================================
# 2. Dynamically import ORIGINAL extractor
# ============================================================

def load_extractor_class(py_path: str):
    py_path = str(Path(py_path).resolve())

    if not os.path.exists(py_path):
        raise FileNotFoundError(
            f"Original extractor file not found: {py_path}\n"
            f"Set OTP_ORIGINAL_EXTRACTOR_PATH to the correct Colab path."
        )

    spec = importlib.util.spec_from_file_location(
        "otp_extractor_original_bge",
        py_path,
    )

    if spec is None or spec.loader is None:
        raise ImportError(
            f"Unable to import original extractor from: {py_path}"
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules["otp_extractor_original_bge"] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "OTPCodeExtractor"):
        raise AttributeError(
            "OTPCodeExtractor class was not found in the original extractor file."
        )

    return module.OTPCodeExtractor


OTPCodeExtractor = load_extractor_class(EXTRACTOR_PATH)
notification_margin_raw = os.getenv("OTP_NOTIFICATION_MARGIN", "").strip()
notification_margin = (
    float(notification_margin_raw)
    if notification_margin_raw
    else None
)
extractor = OTPCodeExtractor(notification_margin=notification_margin)


# ============================================================
# 3. Helpers
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
    We intentionally do NOT rewrite the URL because the paper metric
    is intended to compare the extracted verification link itself.
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


# ============================================================
# 4. Load dataset
# ============================================================

if not os.path.exists(DATASET_PATH):
    raise FileNotFoundError(
        f"Dataset not found: {DATASET_PATH}\n"
        f"Set OTP_DATASET_PATH to the correct Colab path."
    )

# dtype=str is important for OTP values such as 061960.
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
    raise ValueError("Dataset must contain a 'text' column.")

if "ground_truth" not in df.columns:
    raise ValueError("Dataset must contain a 'ground_truth' column.")


# ============================================================
# 5. Evaluation accumulators
# ============================================================

results = []

# OTP metrics by source
otp_stats = {
    "sms": {"total": 0, "top1": 0, "top3": 0},
    "email": {"total": 0, "top1": 0, "top3": 0},
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
# 6. Run evaluation
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
        and gt_lower.startswith(("http://", "https://"))
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
    # IMPORTANT FIX:
    # URL ground truth MUST enter the URL extractor.
    # --------------------------------------------------------

    if is_url_case:
        eval_pref = "link"
    elif is_otp_case:
        # Evaluate the modality named by the ground truth.  In particular, an
        # ``otp+link`` message with an OTP ground truth must enter the OTP path;
        # the extractor intentionally prefers the URL in its combined mode.
        eval_pref = "otp"
    elif row_type == "otp+link":
        # Negative combined-modality messages must exercise both extractors so
        # any returned code or URL is counted as a false extraction.
        eval_pref = "otp+link"
    elif row_type == "link":
        eval_pref = "link"
    elif original_pref:
        eval_pref = original_pref
    else:
        eval_pref = "otp"

    # For a negative row, avoid accidentally using an invalid preference.
    if eval_pref not in {"otp", "link", "otp+link", "none"}:
        eval_pref = "otp"

    print("\n" + "=" * 80)
    print(f"ROW {idx}")
    print("Category:", category)
    print("Ground Truth:", gt_raw)
    print("Source:", source)
    print("Original preference:", original_pref or "(blank)")
    print("Evaluation preference:", eval_pref)

    error = ""

    try:
        otp_candidates, url_value = extractor.extract_otp_code(
            {"text": text},
            source,
            eval_pref,
        )
    except Exception as exc:
        otp_candidates = []
        url_value = None
        error = str(exc)
        print("ERROR:", error)

    bge_codes = codes_from_candidates(
        otp_candidates
    )

    bge_top1 = (
        bge_codes[0]
        if bge_codes
        else ""
    )

    predicted_url = normalize_url(
        url_value
    )

    # Default row flags
    bge_top1_correct = False
    bge_top3_correct = False
    url_correct = False
    false_extraction = False

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

        if source in otp_stats:
            otp_stats[source]["total"] += 1
            otp_stats[source]["top1"] += int(
                bge_top1_correct
            )
            otp_stats[source]["top3"] += int(
                bge_top3_correct
            )

    # --------------------------------------------------------
    # Verification-link case
    # --------------------------------------------------------

    elif is_url_case:

        expected_url = normalize_url(
            gt_raw
        )

        # Exact match, as requested by the experimental metric.
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

        # Any extracted OTP or URL is counted as a false extraction.
        false_extraction = (
            bool(bge_codes)
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
        "BGE Top-3": top3_text(bge_codes),

        "BGE Correct": (
            "✅"
            if is_otp_case and bge_top1_correct
            else (
                "❌"
                if is_otp_case
                else "—"
            )
        ),

        "BGE Top-3 Correct": (
            "✅"
            if is_otp_case and bge_top3_correct
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

    print("BGE FINAL:", bge_codes[:3])
    print("URL FINAL:", predicted_url or None)


# ============================================================
# 7. Aggregate summary
# ============================================================

sms_otp = otp_stats["sms"]
email_otp = otp_stats["email"]

overall_otp_total = (
    sms_otp["total"]
    + email_otp["total"]
)
overall_top1 = (
    sms_otp["top1"]
    + email_otp["top1"]
)
overall_top3 = (
    sms_otp["top3"]
    + email_otp["top3"]
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


def metric_text(hit, total):
    if total == 0:
        return "—"

    return (
        f"{safe_pct(hit, total) * 100:.1f}% "
        f"({hit}/{total})"
    )


# Paper-style summary table
summary_table = pd.DataFrame([
    {
        "Metric": "OTP Top-1",
        "SMS": metric_text(
            sms_otp["top1"],
            sms_otp["total"],
        ),
        "Email": metric_text(
            email_otp["top1"],
            email_otp["total"],
        ),
        "Overall": metric_text(
            overall_top1,
            overall_otp_total,
        ),
    },
    {
        "Metric": "OTP Top-3",
        "SMS": metric_text(
            sms_otp["top3"],
            sms_otp["total"],
        ),
        "Email": metric_text(
            email_otp["top3"],
            email_otp["total"],
        ),
        "Overall": metric_text(
            overall_top3,
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
# 8. Export XLSX
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

    summary_table.to_excel(
        writer,
        sheet_name="Summary",
        index=False,
    )

    detail_ws = writer.book["Detail"]
    summary_ws = writer.book["Summary"]

    detail_ws.freeze_panes = "A2"
    summary_ws.freeze_panes = "A2"

    # Detail widths
    widths = {
        "A": 8,
        "B": 12,
        "C": 55,
        "D": 18,
        "E": 40,
        "F": 16,
        "G": 20,
        "H": 70,
        "I": 16,
        "J": 18,
        "K": 12,
        "L": 28,
        "M": 30,
        "N": 16,
        "O": 50,
    }

    for col_letter, width in widths.items():
        detail_ws.column_dimensions[
            col_letter
        ].width = width

    summary_ws.column_dimensions["A"].width = 42
    summary_ws.column_dimensions["B"].width = 24
    summary_ws.column_dimensions["C"].width = 24
    summary_ws.column_dimensions["D"].width = 24


# ============================================================
# 9. Console summary
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
print("=" * 72)
print("BASELINE: ORIGINAL BGE")
print("=" * 72)

print(
    "OTP Top-1 Overall:",
    metric_text(
        overall_top1,
        overall_otp_total,
    ),
)

print(
    "OTP Top-3 Overall:",
    metric_text(
        overall_top3,
        overall_otp_total,
    ),
)

print(
    "Verification Link Overall:",
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

print("\nSMS / Email detail:")
print(summary_table.to_string(index=False))

print("\nResult XLSX:")
print(RESULT_PATH)
