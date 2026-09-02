import os
import re

import pandas as pd
import torch
from sentence_transformers import util

from otp_code_extractor import OTPCodeExtractor


DATASET_PATH = os.getenv("OTP_DATASET_PATH", "otp_dataset_dedup.xlsx")
RESULT_PATH = os.getenv("OTP_RESULT_PATH", "reproduction_original_results.xlsx")

NOTIFICATION_TEMPLATES = [
    "This is a security notification about recent account activity.",
    "A new device or browser signed in to your account.",
    "Two-factor authentication was enabled on your account.",
    "Two-factor authentication was disabled on your account.",
    "Your authentication method or phone number was changed.",
    "Review this account activity if it was not you.",
    "這是一封帳戶安全活動通知。",
    "有新的裝置或瀏覽器登入您的帳戶。",
    "您的雙重驗證已啟用。",
    "您的雙重驗證已停用。",
    "您的驗證方式或電話號碼已變更。",
    "如果不是您本人操作，請檢查帳戶活動。",
]


def split_segments(text: str) -> list[str]:
    return [
        match.group(0).strip()
        for match in re.finditer(r"[^。\n\r！？!?,，；;]+", text)
        if match.group(0).strip()
    ]


def main() -> None:
    dataset = pd.read_excel(DATASET_PATH, dtype=str).fillna("")
    results = pd.read_excel(RESULT_PATH, sheet_name="Detail", dtype=str).fillna("")
    extractor = OTPCodeExtractor()

    target_positions = [
        position
        for position, result in results.iterrows()
        if result["category"] == "negative"
        and bool(result["BGE Top-1"] or result["URL Prediction"])
    ]

    all_segments: list[str] = []
    row_slices: list[tuple[int, int]] = []
    for position in target_positions:
        text = dataset.iloc[position]["text"]
        segments = split_segments(text)
        start = len(all_segments)
        all_segments.extend(segments)
        row_slices.append((start, len(all_segments)))

    queries = ["為這個句子生成表示以用於檢索相關文章: " + value for value in all_segments]
    with torch.no_grad():
        segment_embeddings = extractor.bge_model.encode(
            queries,
            batch_size=32,
            normalize_embeddings=True,
            convert_to_tensor=True,
            show_progress_bar=True,
        )
        notification_embeddings = extractor.bge_model.encode(
            NOTIFICATION_TEMPLATES,
            normalize_embeddings=True,
            convert_to_tensor=True,
        )
        otp_scores = util.cos_sim(segment_embeddings, extractor.otp_template_embeddings).max(dim=1).values
        url_scores = util.cos_sim(segment_embeddings, extractor.url_template_embeddings).max(dim=1).values
        notification_scores = util.cos_sim(segment_embeddings, notification_embeddings).max(dim=1).values

    rows = []
    for position, (start, end) in zip(target_positions, row_slices):
        source = dataset.iloc[position]
        otp_score = float(otp_scores[start:end].max().item())
        url_score = float(url_scores[start:end].max().item())
        notification_score = float(notification_scores[start:end].max().item())
        result = results.iloc[position]
        has_output = bool(result["BGE Top-1"] or result["URL Prediction"])
        semantic_score = max(otp_score, url_score) if result["URL Prediction"] else otp_score
        rows.append(
            {
                "id": source["id"],
                "category": result["category"],
                "prediction": result["BGE Top-1"] or result["URL Prediction"],
                "otp": otp_score,
                "url": url_score,
                "notification": notification_score,
                "delta": notification_score - semantic_score,
                "reject_003": has_output and notification_score >= semantic_score + 0.03,
            }
        )

    diagnostic = pd.DataFrame(rows)
    print("\nNEGATIVE OUTPUTS")
    print(
        diagnostic.loc[
            diagnostic["category"].eq("negative") & diagnostic["prediction"].ne("")
        ].to_string(index=False, float_format=lambda value: f"{value:.4f}")
    )


if __name__ == "__main__":
    main()
