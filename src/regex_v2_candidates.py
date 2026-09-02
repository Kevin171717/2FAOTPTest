from __future__ import annotations

import re

from urlextract import URLExtract


OTP_PATTERN = re.compile(
    r"""
    (?P<grouped_numeric>
        (?<![A-Za-z0-9])\d{3}[\s-]\d{3}(?![A-Za-z0-9])
    )
    |
    (?P<hyphenated_alnum>
        (?<![A-Za-z0-9])
        (?=[A-Za-z0-9-]{5,9}(?![A-Za-z0-9-]))
        (?=[A-Za-z0-9-]*\d)
        [A-Za-z0-9]{2,4}-[A-Za-z0-9]{2,4}
        (?![A-Za-z0-9])
    )
    |
    (?P<attached_numeric>
        (?<![A-Za-z0-9])\d{4,8}(?=[A-Za-z])
    )
    |
    (?P<bounded_alnum>
        (?<![A-Za-z0-9])
        (?=[A-Za-z0-9]{4,8}(?![A-Za-z0-9]))
        (?=[A-Za-z0-9]{0,7}\d)
        [A-Za-z0-9]{4,8}
    )
    |
    (?P<uppercase_alpha>
        (?<![A-Za-z0-9])[A-Z]{4,8}(?![A-Za-z0-9])
    )
    """,
    re.VERBOSE,
)

NOISE_PATTERNS = [
    re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}"),
    re.compile(r"(?i)(?<![A-Za-z])(NT\$?|\$)\s?[\d,]+(\.\d{2})?"),
    re.compile(r"[0-2]?\d:[0-5]\d"),
    re.compile(r"(?<!\d)\d{10,12}(?!\d)"),
]
IP_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
YEAR_RANGE_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}[–—-](?:19|20)\d{2}(?!\d)")
MONTH_DATE_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:\d{1,2}[-/](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[-/]\d{1,2})(?![A-Za-z0-9])"
)


def normalize_candidate(raw_value: str, pattern_name: str | None = None) -> str:
    compact = re.sub(r"\s+", "", raw_value)
    if pattern_name == "grouped_numeric":
        return compact.replace("-", "")
    return compact


def _ranges(pattern: re.Pattern, text: str, tag: str) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), tag) for match in pattern.finditer(text)]


def _url_ranges(text: str) -> list[tuple[int, int, str]]:
    ranges = []
    extractor = URLExtract()
    for url in extractor.find_urls(text):
        if not str(url).startswith(("http://", "https://")):
            continue
        start_from = 0
        while True:
            start = text.find(url, start_from)
            if start < 0:
                break
            ranges.append((start, start + len(url), "URL"))
            start_from = start + len(url)
    return ranges


def find_candidate_occurrences(text: str) -> list[dict]:
    text = text or ""
    excluded_ranges = _url_ranges(text)
    for noise_pattern in NOISE_PATTERNS:
        excluded_ranges.extend(_ranges(noise_pattern, text, "DATE_TIME_OR_LONG_NUMBER"))
    excluded_ranges.extend(_ranges(IP_PATTERN, text, "IP_ADDRESS"))
    excluded_ranges.extend(_ranges(YEAR_RANGE_PATTERN, text, "YEAR_RANGE"))
    excluded_ranges.extend(_ranges(MONTH_DATE_PATTERN, text, "MONTH_DATE"))

    records = []
    for match in OTP_PATTERN.finditer(text):
        if any(
            start <= match.start() and match.end() <= end
            for start, end, _ in excluded_ranges
        ):
            continue
        records.append(
            {
                "raw": match.group(0),
                "code": normalize_candidate(match.group(0), match.lastgroup),
                "start": match.start(),
                "end": match.end(),
                "pattern": match.lastgroup,
            }
        )
    return records


def candidate_context(text: str, start: int, end: int, radius: int = 170) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]


def deduplicate_candidates(text: str, occurrences: list[dict]) -> list[dict]:
    candidates = []
    seen = set()
    for record in occurrences:
        code = record["code"]
        if code in seen:
            continue
        seen.add(code)
        candidates.append(
            {
                **record,
                "candidate_id": f"C{len(candidates)}",
                "value": code,
                "context": candidate_context(text, record["start"], record["end"]),
            }
        )
    return candidates


def extract_candidates(text: str) -> list[dict]:
    return deduplicate_candidates(text or "", find_candidate_occurrences(text or ""))
