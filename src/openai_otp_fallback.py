from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


SYSTEM_INSTRUCTIONS = """You are the final adjudicator after Regex candidate generation and BGE ranking.
Classify the message and select an actionable OTP candidate, or select NO_OTP.

An actionable OTP is a one-time code that the recipient is being asked to enter or use now.
Do not select discount codes, referral codes, order numbers, phone suffixes, dates, years,
postal codes, street numbers, IP fragments, tracking tokens, or footer text.
Messages saying that 2FA was enabled, disabled, added, removed, or changed are security
notifications unless they also provide a new actionable OTP.

Return only candidate IDs supplied by the caller. Never invent, copy, or transform a code.
If no candidate is an actionable OTP, select null and return an empty ranking.
Rank at most three actionable candidates. The selected candidate must be first in the ranking.
BGE scores and ranks are supporting evidence, not ground truth; use the message context to
correct BGE when its preferred candidate is noise.
"""

REGEX_ONLY_SYSTEM_INSTRUCTIONS = """You are the final adjudicator after Regex candidate generation.
Classify the message and select an actionable OTP candidate, or select NO_OTP.

An actionable OTP is a one-time code that the recipient is being asked to enter or use now.
Do not select discount codes, referral codes, order numbers, phone suffixes, dates, years,
postal codes, street numbers, IP fragments, tracking tokens, or footer text.
Messages saying that 2FA was enabled, disabled, added, removed, or changed are security
notifications unless they also provide a new actionable OTP.

Return only candidate IDs supplied by the caller. Never invent, copy, or transform a code.
If no candidate is an actionable OTP, select null and return an empty ranking.
Rank at most three actionable candidates. The selected candidate must be first in the ranking.
Use only the message, subject, Regex pattern type, and local candidate context. No embedding
or BGE score is available.
"""

RAW_MESSAGE_SYSTEM_INSTRUCTIONS = """Extract actionable authentication information directly from the raw message.

An actionable OTP is a one-time code that the recipient is being asked to enter or use now.
Do not return discount codes, referral codes, order numbers, phone suffixes, dates, years,
postal codes, street numbers, IP fragments, tracking tokens, or footer text.
Messages saying that 2FA was enabled, disabled, added, removed, or changed are security
notifications unless they also contain a new actionable OTP.

Return OTP codes exactly as they appear in the message, without correcting, normalizing,
joining, or inventing characters. Rank at most three codes, with the most likely code first.
Return an actionable verification URL exactly as it appears in the message when present.
If there is no actionable OTP or verification URL, return an empty otp_codes array and null URL.
"""


@dataclass
class OpenAIConfig:
    api_key: str
    model: str = "gpt-4o"
    input_usd_per_m: float = 2.50
    cached_input_usd_per_m: float = 1.25
    cache_write_usd_per_m: float = 2.50
    output_usd_per_m: float = 10.00
    timeout_seconds: float = 60.0
    max_retries: int = 3
    max_output_tokens: int = 300
    reasoning_effort: str = "none"

    @classmethod
    def from_environment(cls) -> "OpenAIConfig":
        return cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            model=os.getenv("OPENAI_MODEL", "gpt-4o").strip(),
            input_usd_per_m=float(os.getenv("OPENAI_INPUT_USD_PER_M", "2.50")),
            cached_input_usd_per_m=float(os.getenv("OPENAI_CACHED_INPUT_USD_PER_M", "1.25")),
            cache_write_usd_per_m=float(os.getenv("OPENAI_CACHE_WRITE_USD_PER_M", "2.50")),
            output_usd_per_m=float(os.getenv("OPENAI_OUTPUT_USD_PER_M", "10.00")),
            timeout_seconds=float(os.getenv("OPENAI_REQUEST_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "3")),
            max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "300")),
            reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "none").strip(),
        )

    def public_dict(self) -> dict:
        result = asdict(self)
        result.pop("api_key", None)
        return result


@dataclass
class APIUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class APIDecision:
    message_type: str
    selected_candidate_id: str | None
    ranked_candidate_ids: list[str]
    reason_code: str
    usage: APIUsage
    latency_ms: int
    attempts: int
    response_id: str
    model: str

    def to_dict(self) -> dict:
        result = asdict(self)
        result["usage"] = self.usage.to_dict()
        return result


@dataclass
class RawAPIDecision:
    message_type: str
    otp_codes: list[str]
    verification_url: str | None
    reason_code: str
    usage: APIUsage
    latency_ms: int
    attempts: int
    response_id: str
    model: str

    def to_dict(self) -> dict:
        result = asdict(self)
        result["usage"] = self.usage.to_dict()
        return result


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def sanitize_text(value: str) -> str:
    text = value or ""
    text = re.sub(r"https?://\S+", "<URL>", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<EMAIL>", text)
    text = re.sub(r"(?<![A-Za-z0-9])[A-Za-z0-9_=-]{32,}(?![A-Za-z0-9])", "<TOKEN>", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_message_for_api(text: str, limit: int = 4000) -> str:
    cleaned = sanitize_text(text)
    if len(cleaned) <= limit:
        return cleaned
    head_size = int(limit * 0.72)
    tail_size = limit - head_size
    return f"{cleaned[:head_size]} <TRUNCATED> {cleaned[-tail_size:]}"


def response_schema(candidate_ids: list[str]) -> dict:
    allowed_ids = list(dict.fromkeys(candidate_ids))
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message_type": {
                "type": "string",
                "enum": [
                    "OTP",
                    "SECURITY_NOTIFICATION",
                    "PROMOTION",
                    "OTHER_NO_OTP",
                    "UNCERTAIN",
                ],
            },
            "selected_candidate_id": {
                "enum": [*allowed_ids, None],
            },
            "ranked_candidate_ids": {
                "type": "array",
                "items": {"type": "string", "enum": allowed_ids},
                "maxItems": min(3, len(allowed_ids)),
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "EXPLICIT_ACTIONABLE_OTP",
                    "OTP_WITH_DISTRACTORS",
                    "PROMO_CODE",
                    "PHONE_SUFFIX",
                    "ADDRESS_OR_FOOTER",
                    "STATUS_NOTIFICATION",
                    "NO_ACTIONABLE_OTP",
                    "AMBIGUOUS",
                ],
            },
        },
        "required": [
            "message_type",
            "selected_candidate_id",
            "ranked_candidate_ids",
            "reason_code",
        ],
    }


def raw_response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message_type": {
                "type": "string",
                "enum": [
                    "OTP",
                    "VERIFICATION_LINK",
                    "OTP_AND_LINK",
                    "SECURITY_NOTIFICATION",
                    "PROMOTION",
                    "OTHER_NO_OTP",
                    "UNCERTAIN",
                ],
            },
            "otp_codes": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 3,
            },
            "verification_url": {
                "type": ["string", "null"],
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "EXPLICIT_ACTIONABLE_OTP",
                    "EXPLICIT_VERIFICATION_LINK",
                    "OTP_AND_VERIFICATION_LINK",
                    "PROMO_CODE",
                    "PHONE_SUFFIX",
                    "ADDRESS_OR_FOOTER",
                    "STATUS_NOTIFICATION",
                    "NO_ACTIONABLE_AUTH",
                    "AMBIGUOUS",
                ],
            },
        },
        "required": ["message_type", "otp_codes", "verification_url", "reason_code"],
    }


def _nested_int(obj, parent_name: str, child_name: str) -> int:
    parent = getattr(obj, parent_name, None)
    return int(getattr(parent, child_name, 0) or 0) if parent is not None else 0


class OpenAIFallbackClient:
    def __init__(self, config: OpenAIConfig):
        if not config.api_key:
            raise ValueError("OPENAI_API_KEY is empty. Fill it in .env before an API run.")
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            timeout=config.timeout_seconds,
            max_retries=0,
        )

    def _request_options(
        self,
        *,
        instructions: str,
        request_text: str,
        schema_name: str,
        schema: dict,
        prompt_cache_key: str,
    ) -> dict:
        text_config = {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        }
        request = {
            "model": self.config.model,
            "instructions": instructions,
            "input": request_text,
            "text": text_config,
            "max_output_tokens": self.config.max_output_tokens,
            "prompt_cache_key": prompt_cache_key,
            "store": False,
        }

        # GPT-4o supports Structured Outputs, but not GPT-5 reasoning/verbosity controls.
        if self.config.model.lower().startswith("gpt-5"):
            text_config["verbosity"] = "low"
            request["reasoning"] = {"effort": self.config.reasoning_effort}

        return request

    def _usage(self, response) -> APIUsage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return APIUsage()

        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
        cached_tokens = _nested_int(usage, "input_tokens_details", "cached_tokens")
        cache_write_tokens = _nested_int(usage, "input_tokens_details", "cache_write_tokens")
        reasoning_tokens = _nested_int(usage, "output_tokens_details", "reasoning_tokens")
        uncached_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
        estimated_cost = (
            uncached_tokens * self.config.input_usd_per_m
            + cached_tokens * self.config.cached_input_usd_per_m
            + cache_write_tokens * self.config.cache_write_usd_per_m
            + output_tokens * self.config.output_usd_per_m
        ) / 1_000_000

        return APIUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost,
        )

    def decide(
        self,
        subject: str,
        message: str,
        candidates: list[dict],
        route_reasons: list[str],
        bge_top3: list[str],
    ) -> APIDecision:
        candidate_ids = [str(candidate["candidate_id"]) for candidate in candidates]
        payload = {
            "subject": sanitize_text(subject),
            "message_excerpt": compact_message_for_api(message),
            "routing_reasons": route_reasons,
            "bge_top3_values": bge_top3,
            "candidates": [
                {
                    "id": candidate["candidate_id"],
                    "value": candidate["value"],
                    "pattern": candidate["pattern"],
                    "context": sanitize_text(candidate["context"]),
                    "bge_otp_score": candidate["bge_otp_score"],
                    "bge_notification_score": candidate["bge_notification_score"],
                    "bge_rank": candidate["bge_rank"],
                    "structural_flags": candidate["structural_flags"],
                }
                for candidate in candidates
            ],
        }
        request_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        start = time.perf_counter()
        last_error = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.client.responses.create(
                    **self._request_options(
                        instructions=SYSTEM_INSTRUCTIONS,
                        request_text=request_text,
                        schema_name="otp_fallback_decision",
                        schema=response_schema(candidate_ids),
                        prompt_cache_key="otp-bge-openai-fallback-v1",
                    )
                )
                raw_decision = json.loads(response.output_text)
                selected_id = raw_decision["selected_candidate_id"]
                ranked_ids = list(dict.fromkeys(raw_decision["ranked_candidate_ids"]))

                if selected_id is not None and selected_id not in candidate_ids:
                    raise ValueError(f"Model selected unknown candidate ID: {selected_id}")
                if any(candidate_id not in candidate_ids for candidate_id in ranked_ids):
                    raise ValueError("Model ranking contains an unknown candidate ID.")

                message_type = raw_decision["message_type"]
                if message_type != "OTP" or selected_id is None:
                    selected_id = None
                    ranked_ids = []
                else:
                    ranked_ids = [selected_id, *[item for item in ranked_ids if item != selected_id]][:3]

                return APIDecision(
                    message_type=message_type,
                    selected_candidate_id=selected_id,
                    ranked_candidate_ids=ranked_ids,
                    reason_code=raw_decision["reason_code"],
                    usage=self._usage(response),
                    latency_ms=round((time.perf_counter() - start) * 1000),
                    attempts=attempt,
                    response_id=str(response.id),
                    model=str(response.model),
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise
                last_error = exc

            if attempt < self.config.max_retries:
                time.sleep(min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35))

        raise RuntimeError(
            f"OpenAI request failed after {self.config.max_retries} attempts: {last_error}"
        ) from last_error

    def decide_regex_only(
        self,
        subject: str,
        message: str,
        candidates: list[dict],
    ) -> APIDecision:
        candidate_ids = [str(candidate["candidate_id"]) for candidate in candidates]
        if not candidate_ids:
            raise ValueError("decide_regex_only requires at least one Regex candidate.")

        payload = {
            "subject": sanitize_text(subject),
            "message_excerpt": compact_message_for_api(message),
            "candidates": [
                {
                    "id": candidate["candidate_id"],
                    "value": candidate["value"],
                    "pattern": candidate["pattern"],
                    "context": sanitize_text(candidate["context"]),
                }
                for candidate in candidates
            ],
        }
        request_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        start = time.perf_counter()
        last_error = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.client.responses.create(
                    **self._request_options(
                        instructions=REGEX_ONLY_SYSTEM_INSTRUCTIONS,
                        request_text=request_text,
                        schema_name="otp_regex_decision",
                        schema=response_schema(candidate_ids),
                        prompt_cache_key="otp-regex-openai-v1",
                    )
                )
                raw_decision = json.loads(response.output_text)
                selected_id = raw_decision["selected_candidate_id"]
                ranked_ids = list(dict.fromkeys(raw_decision["ranked_candidate_ids"]))

                if selected_id is not None and selected_id not in candidate_ids:
                    raise ValueError(f"Model selected unknown candidate ID: {selected_id}")
                if any(candidate_id not in candidate_ids for candidate_id in ranked_ids):
                    raise ValueError("Model ranking contains an unknown candidate ID.")

                message_type = raw_decision["message_type"]
                if message_type != "OTP" or selected_id is None:
                    selected_id = None
                    ranked_ids = []
                else:
                    ranked_ids = [selected_id, *[item for item in ranked_ids if item != selected_id]][:3]

                return APIDecision(
                    message_type=message_type,
                    selected_candidate_id=selected_id,
                    ranked_candidate_ids=ranked_ids,
                    reason_code=raw_decision["reason_code"],
                    usage=self._usage(response),
                    latency_ms=round((time.perf_counter() - start) * 1000),
                    attempts=attempt,
                    response_id=str(response.id),
                    model=str(response.model),
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise
                last_error = exc

            if attempt < self.config.max_retries:
                time.sleep(min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35))

        raise RuntimeError(
            f"OpenAI request failed after {self.config.max_retries} attempts: {last_error}"
        ) from last_error

    def decide_raw_message(self, message: str) -> RawAPIDecision:
        request_text = json.dumps({"message": message}, ensure_ascii=False, separators=(",", ":"))
        start = time.perf_counter()
        last_error = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                request_options = self._request_options(
                    instructions=RAW_MESSAGE_SYSTEM_INSTRUCTIONS,
                    request_text=request_text,
                    schema_name="raw_otp_extraction",
                    schema=raw_response_schema(),
                    prompt_cache_key="otp-raw-openai-v1",
                )
                request_options["max_output_tokens"] = max(self.config.max_output_tokens, 1200)
                response = self.client.responses.create(
                    **request_options
                )
                raw_decision = json.loads(response.output_text)
                otp_codes = list(dict.fromkeys(str(code) for code in raw_decision["otp_codes"]))[:3]
                verification_url = raw_decision["verification_url"]
                if verification_url is not None:
                    verification_url = str(verification_url)

                return RawAPIDecision(
                    message_type=raw_decision["message_type"],
                    otp_codes=otp_codes,
                    verification_url=verification_url,
                    reason_code=raw_decision["reason_code"],
                    usage=self._usage(response),
                    latency_ms=round((time.perf_counter() - start) * 1000),
                    attempts=attempt,
                    response_id=str(response.id),
                    model=str(response.model),
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise
                last_error = exc

            if attempt < self.config.max_retries:
                time.sleep(min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35))

        raise RuntimeError(
            f"OpenAI request failed after {self.config.max_retries} attempts: {last_error}"
        ) from last_error
