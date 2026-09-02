from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable


ROUTE_BGE_DIRECT = "BGE_DIRECT"
ROUTE_OPENAI_FALLBACK = "OPENAI_FALLBACK"


CODE_CUE_RE = re.compile(
    r"(?i)\b(?:otp|one[ -]?time password|passcode|verification code|security code|"
    r"authentication code|login code|confirmation code|code)\b|"
    r"驗證碼|驗證代碼|認證碼|安全碼|登入碼|確認碼|一次性密碼"
)
PHONE_SUFFIX_RE = re.compile(
    r"(?i)(?:phone|mobile|telephone|number)[^。.!?\n]{0,60}"
    r"(?:ending in|last four|last \d|suffix)|"
    r"(?:ending in|last four digits)[^。.!?\n]{0,30}<CAND>|"
    r"(?:電話|手機|號碼)[^。.!?\n]{0,50}(?:末四碼|結尾|尾碼)"
)
ADDRESS_RE = re.compile(
    r"(?i)\b\d{1,6}\s+[A-Za-z0-9.' -]{1,50}\s+"
    r"(?:street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|"
    r"drive|dr\.?|lane|ln\.?|parkway|pkwy\.?)\b|"
    r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|"
    r"(?:地址|住址|郵寄地址|通訊地址|街道地址|郵遞區號)[^。.!?\n]{0,60}<CAND>"
)


@dataclass
class CandidateEvidence:
    candidate_id: str
    value: str
    raw: str
    pattern: str
    start: int
    end: int
    context: str
    bge_otp_score: float
    bge_notification_score: float
    bge_rank: int = 0
    structural_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateDecision:
    route: str
    reasons: list[str]
    candidates: list[CandidateEvidence]

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "reasons": self.reasons,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def local_context(text: str, start: int, end: int, window: int = 170) -> str:
    return compact_text(text[max(0, start - window) : min(len(text), end + window)])


def _candidate_pattern(pattern: re.Pattern, raw: str) -> re.Pattern:
    return re.compile(pattern.pattern.replace("<CAND>", re.escape(raw)), pattern.flags)


def build_candidate_evidence(
    text: str,
    records: Iterable[dict],
    semantic_scores: dict[str, tuple[float, float]],
) -> list[CandidateEvidence]:
    evidence = []
    for index, record in enumerate(records):
        candidate_id = str(record.get("candidate_id") or f"C{index}")
        raw = str(record["raw"])
        value = str(record["code"])
        start = int(record["start"])
        end = int(record["end"])
        context = local_context(text, start, end)
        flags = []

        if re.fullmatch(r"(?:19|20)\d{2}", value):
            flags.append("YEAR")
        if _candidate_pattern(PHONE_SUFFIX_RE, raw).search(context):
            flags.append("PHONE_SUFFIX")
        if _candidate_pattern(ADDRESS_RE, raw).search(context):
            flags.append("ADDRESS")

        otp_score, notification_score = semantic_scores.get(candidate_id, (0.0, 0.0))
        evidence.append(
            CandidateEvidence(
                candidate_id=candidate_id,
                value=value,
                raw=raw,
                pattern=str(record.get("pattern") or "unknown"),
                start=start,
                end=end,
                context=context,
                bge_otp_score=float(otp_score),
                bge_notification_score=float(notification_score),
                structural_flags=flags,
            )
        )

    ranked = sorted(evidence, key=lambda item: (-item.bge_otp_score, item.start))
    for rank, candidate in enumerate(ranked, start=1):
        candidate.bge_rank = rank
    return evidence


def route_with_bge(
    text: str,
    candidates: list[CandidateEvidence],
    bge_top3: list[str],
    anchor_threshold: float = 0.50,
    score_gap_threshold: float = 0.05,
    notification_margin: float = 0.03,
) -> GateDecision:
    """Choose whether BGE is confident enough or OpenAI should adjudicate.

    This gate never selects or rejects an OTP. Its only output is BGE_DIRECT or
    OPENAI_FALLBACK, so candidate/NO_OTP decisions remain with BGE or OpenAI.
    """
    if not candidates:
        return GateDecision(ROUTE_BGE_DIRECT, ["NO_REGEX_CANDIDATE"], candidates)

    reasons = []
    if len(candidates) >= 2:
        reasons.append("MULTIPLE_CANDIDATES")

    flags = {flag for candidate in candidates for flag in candidate.structural_flags}
    for flag in ("YEAR", "ADDRESS", "PHONE_SUFFIX"):
        if flag in flags:
            reasons.append(f"STRUCTURAL_{flag}")

    if any(candidate.pattern == "uppercase_alpha" for candidate in candidates):
        reasons.append("UPPERCASE_ALPHA_CANDIDATE")

    ranked = sorted(candidates, key=lambda item: (-item.bge_otp_score, item.start))
    best_otp_score = ranked[0].bge_otp_score
    best_notification_score = max(
        candidate.bge_notification_score for candidate in candidates
    )
    has_code_cue = bool(CODE_CUE_RE.search(text or ""))

    if best_otp_score < anchor_threshold and has_code_cue:
        reasons.append("LOW_BGE_SCORE_WITH_CODE_CUE")
    if not bge_top3 or best_otp_score < anchor_threshold:
        reasons.append("BGE_CANDIDATE_VS_NO_OTP_UNCERTAIN")
    if best_notification_score >= best_otp_score - notification_margin:
        reasons.append("BGE_MIXED_OTP_NOTIFICATION")

    if len(ranked) >= 2 and ranked[0].bge_otp_score - ranked[1].bge_otp_score <= score_gap_threshold:
        reasons.append("TOP1_TOP2_CLOSE")
    if len(ranked) >= 3 and ranked[0].bge_otp_score - ranked[2].bge_otp_score <= score_gap_threshold:
        reasons.append("TOP1_TOP3_CLOSE")

    if reasons:
        return GateDecision(
            ROUTE_OPENAI_FALLBACK,
            list(dict.fromkeys(reasons)),
            candidates,
        )
    return GateDecision(ROUTE_BGE_DIRECT, ["BGE_CONFIDENT"], candidates)
