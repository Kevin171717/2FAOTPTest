"""
Standalone evaluator for otp_code_extractor_openai_annotated.py

This script DOES NOT modify the original extractor.
It imports the extractor dynamically, wraps the existing OpenAI reranker
at runtime, and records:

- BGE Top-1 / Top-3 before OpenAI reranking
- LLM Top-1 / Top-3 after OpenAI reranking
- Whether BGE Top-1 is correct
- Whether LLM Top-1 is correct
- Whether LLM saved a BGE error
- Whether LLM caused a regression

Output:
    otp_experiment_results.xlsx
      - Detail sheet
      - Summary sheet

Environment variables:
    OTP_EXTRACTOR_PATH  path to original .py
    OTP_DATASET_PATH    path to dataset .xlsx
    OTP_RESULT_PATH     optional output path
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
    "/content/otp_experiment_results.xlsx",
)


# ============================================================
# 2. Dynamically import the ORIGINAL extractor
#    No modification to the original source file is required.
# ============================================================

def load_extractor_class(py_path: str):
    py_path = str(Path(py_path).resolve())

    if not os.path.exists(py_path):
        raise FileNotFoundError(
            f"Extractor file not found: {py_path}\n"
            f"Set OTP_EXTRACTOR_PATH to the correct Colab path."
        )

    spec = importlib.util.spec_from_file_location(
        "otp_extractor_original",
        py_path,
    )

    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to import extractor from: {py_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["otp_extractor_original"] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "OTPCodeExtractor"):
        raise AttributeError(
            "OTPCodeExtractor class was not found in the extractor file."
        )

    return module.OTPCodeExtractor


OTPCodeExtractor = load_extractor_class(EXTRACTOR_PATH)


# ============================================================
# 3. Create extractor and wrap the OpenAI reranker
#
# Original flow:
#     BGE candidates
#         -> _llm_rerank_otp_candidates(...)
#         -> Final Top-3
#
# We intercept the arguments passed into that method.
# The arguments are exactly the BGE Top-K candidates.
# ============================================================

extractor = OTPCodeExtractor()

_original_llm_reranker = extractor._llm_rerank_otp_candidates


def capture_llm_reranker(text, candidates):
    """
    candidates = BGE ranking BEFORE OpenAI
    result     = ranking AFTER OpenAI
    """

    extractor._experiment_bge_candidates = [
        dict(c) for c in candidates
    ]

    result = _original_llm_reranker(text, candidates)

    extractor._experiment_llm_candidates = [
        dict(c) for c in result
    ]

    return result


extractor._llm_rerank_otp_candidates = capture_llm_reranker


# ============================================================
# 4. Helpers
# ============================================================

def clean_code(value):
    if value is None:
        return ""
    return str(value).replace(" ", "").strip()


def codes_from_candidates(candidates):
    return [
        clean_code(c.get("code"))
        for c in (candidates or [])
        if clean_code(c.get("code"))
    ]


def top3_text(codes):
    return ", ".join(codes[:3])


# ============================================================
# 5. Load dataset
#
# dtype=str is important:
# ground truth such as 061960 must remain "061960".
# ============================================================

if not os.path.exists(DATASET_PATH):
    raise FileNotFoundError(
        f"Dataset not found: {DATASET_PATH}\n"
        f"Set OTP_DATASET_PATH to the correct Colab path."
    )

df = pd.read_excel(
    DATASET_PATH,
    dtype=str,
    keep_default_na=False,
)

df.columns = [str(c).strip().lower() for c in df.columns]

if "text" not in df.columns:
    raise ValueError("Dataset must contain a 'text' column.")

if "ground_truth" not in df.columns:
    raise ValueError("Dataset must contain a 'ground_truth' column.")


# ============================================================
# 6. Run evaluation
# ============================================================

results = []

bge_top1_hits = 0
bge_top3_hits = 0
llm_top1_hits = 0
llm_top3_hits = 0

llm_saved = 0
llm_regression = 0

otp_evaluated = 0


for idx, row in df.iterrows():

    text = str(row.get("text", "") or "")
    ground_truth = clean_code(row.get("ground_truth", ""))

    source = str(row.get("source", "") or "email").strip().lower()

    pref = str(
        row.get("credential_preference", "") or ""
    ).strip().lower()

    # Many datasets use `type=otp` / `type=otp+link`
    # but do not have credential_preference.
    if not pref:
        row_type = str(row.get("type", "") or "").strip().lower()

        if row_type == "otp+link":
            pref = "otp+link"
        else:
            pref = "otp"

    # Skip empty ground truth and URL ground truth from OTP ranking metrics.
    is_otp_case = (
        bool(ground_truth)
        and not ground_truth.lower().startswith(("http://", "https://"))
    )

    print("\n" + "=" * 80)
    print(f"ROW {idx}")
    print("Ground Truth:", ground_truth)

    # Reset capture for this row.
    extractor._experiment_bge_candidates = []
    extractor._experiment_llm_candidates = []

    error = ""

    try:
        final_candidates, url_value = extractor.extract_otp_code(
            {"text": text},
            source,
            pref,
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

        # Important fallback:
        # If extract_otp_code never reached the LLM reranker
        # (e.g. no valid OTP candidate), both rankings are empty.
        bge_codes = codes_from_candidates(bge_candidates)
        llm_codes = codes_from_candidates(llm_candidates)

    except Exception as exc:
        bge_codes = []
        llm_codes = []
        url_value = None
        error = str(exc)
        print("ERROR:", error)

    bge_top1 = bge_codes[0] if bge_codes else ""
    llm_top1 = llm_codes[0] if llm_codes else ""

    bge_top1_correct = False
    bge_top3_correct = False
    llm_top1_correct = False
    llm_top3_correct = False

    status = "—"

    if is_otp_case:
        otp_evaluated += 1

        bge_top1_correct = (
            bool(bge_codes)
            and bge_codes[0] == ground_truth
        )

        bge_top3_correct = (
            ground_truth in bge_codes[:3]
        )

        llm_top1_correct = (
            bool(llm_codes)
            and llm_codes[0] == ground_truth
        )

        llm_top3_correct = (
            ground_truth in llm_codes[:3]
        )

        bge_top1_hits += int(bge_top1_correct)
        bge_top3_hits += int(bge_top3_correct)

        llm_top1_hits += int(llm_top1_correct)
        llm_top3_hits += int(llm_top3_correct)

        if (not bge_top1_correct) and llm_top1_correct:
            llm_saved += 1
            status = "YES"

        elif bge_top1_correct and (not llm_top1_correct):
            llm_regression += 1
            status = "REGRESSION"

    result_row = {
        # Use 1-based display row as requested.
        "row": idx + 1,
        "ground_truth": ground_truth,

        "BGE Top-1": bge_top1,
        "BGE Top-3": top3_text(bge_codes),

        "LLM Top-1": llm_top1,
        "LLM Top-3": top3_text(llm_codes),

        "BGE Correct": "✅" if bge_top1_correct else "❌",
        "LLM Correct": "✅" if llm_top1_correct else "❌",

        "LLM Saved": status,

        # Extra fields are useful for later failure analysis.
        "BGE Top-3 Correct": "✅" if bge_top3_correct else "❌",
        "LLM Top-3 Correct": "✅" if llm_top3_correct else "❌",
        "source": source,
        "credential_preference": pref,
        "error": error,
    }

    results.append(result_row)

    print("BGE FINAL:", bge_codes[:3])
    print("LLM FINAL:", llm_codes[:3])
    print("STATUS:", status)


# ============================================================
# 7. Summary
# ============================================================

def accuracy(hit, total):
    if total == 0:
        return 0.0
    return hit / total * 100


summary_rows = [
    {
        "Method": "BASELINE: BGE",
        "Metric": "Top-1",
        "Hits": bge_top1_hits,
        "Total": otp_evaluated,
        "Accuracy": accuracy(bge_top1_hits, otp_evaluated) / 100,
    },
    {
        "Method": "BASELINE: BGE",
        "Metric": "Top-3",
        "Hits": bge_top3_hits,
        "Total": otp_evaluated,
        "Accuracy": accuracy(bge_top3_hits, otp_evaluated) / 100,
    },
    {
        "Method": "PROPOSED: BGE + OPENAI",
        "Metric": "Top-1",
        "Hits": llm_top1_hits,
        "Total": otp_evaluated,
        "Accuracy": accuracy(llm_top1_hits, otp_evaluated) / 100,
    },
    {
        "Method": "PROPOSED: BGE + OPENAI",
        "Metric": "Top-3",
        "Hits": llm_top3_hits,
        "Total": otp_evaluated,
        "Accuracy": accuracy(llm_top3_hits, otp_evaluated) / 100,
    },
]

detail_df = pd.DataFrame(results)
summary_df = pd.DataFrame(summary_rows)


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

    summary_df.to_excel(
        writer,
        sheet_name="Summary",
        index=False,
        startrow=0,
    )

    # Additional summary values.
    ws = writer.book["Summary"]

    ws["G1"] = "LLM saved BGE errors"
    ws["H1"] = llm_saved

    ws["G2"] = "LLM caused regression"
    ws["H2"] = llm_regression

    # Percentage formatting
    for cell in ws["E"][1:]:
        cell.number_format = "0.0%"

    # Detail column widths
    detail_ws = writer.book["Detail"]

    widths = {
        "A": 8,
        "B": 18,
        "C": 18,
        "D": 40,
        "E": 18,
        "F": 40,
        "G": 15,
        "H": 15,
        "I": 18,
        "J": 18,
        "K": 18,
        "L": 12,
        "M": 24,
        "N": 50,
    }

    for col_letter, width in widths.items():
        detail_ws.column_dimensions[col_letter].width = width

    for col_letter in ["A", "B", "C", "D", "E", "F", "G", "H"]:
        ws.column_dimensions[col_letter].width = 25


# ============================================================
# 9. Console summary
# ============================================================

print("\n")
print("==============================")
print("BASELINE: BGE")
print("==============================")
print(f"Top-1: {bge_top1_hits} / {otp_evaluated}")
print(
    f"Top-1 Accuracy: "
    f"{accuracy(bge_top1_hits, otp_evaluated):.1f}%"
)
print()
print(f"Top-3: {bge_top3_hits} / {otp_evaluated}")
print(
    f"Top-3 Accuracy: "
    f"{accuracy(bge_top3_hits, otp_evaluated):.1f}%"
)

print("\n")
print("==============================")
print("PROPOSED: BGE + OPENAI")
print("==============================")
print(f"Top-1: {llm_top1_hits} / {otp_evaluated}")
print(
    f"Top-1 Accuracy: "
    f"{accuracy(llm_top1_hits, otp_evaluated):.1f}%"
)
print()
print(f"Top-3: {llm_top3_hits} / {otp_evaluated}")
print(
    f"Top-3 Accuracy: "
    f"{accuracy(llm_top3_hits, otp_evaluated):.1f}%"
)

print()
print(f"LLM saved BGE errors: {llm_saved}")
print(f"LLM caused regression: {llm_regression}")

print()
print("Result XLSX:")
print(RESULT_PATH)
