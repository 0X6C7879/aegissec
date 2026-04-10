from __future__ import annotations

import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from app.core.settings import Settings
from app.services.llm_rate_limit import (
    LLMQuotaReservation,
    LLMRateLimitConfig,
    LLMRateLimiter,
    build_llm_rate_limit_config,
    build_quota_reservation,
    get_llm_rate_limiter,
)


@dataclass(frozen=True, slots=True)
class LLMRateLease:
    reservation: LLMQuotaReservation


class LLMRateController:
    def __init__(self, config: LLMRateLimitConfig, limiter: LLMRateLimiter) -> None:
        self.config = config
        self._limiter = limiter

    async def acquire(
        self,
        payload: dict[str, object],
        *,
        max_output_tokens: int,
    ) -> LLMRateLease:
        reservation = build_quota_reservation(payload, default_output_tokens=max_output_tokens)
        await self._limiter.acquire(reservation)
        return LLMRateLease(reservation=reservation)

    async def finalize(
        self,
        lease: LLMRateLease,
        *,
        rate_limited: bool,
        actual_input_tokens: int | None = None,
        actual_output_tokens: int | None = None,
        actual_total_tokens: int | None = None,
    ) -> None:
        del lease, rate_limited, actual_input_tokens, actual_output_tokens, actual_total_tokens
        self._limiter.release()

    async def note_backoff(self, delay_seconds: float) -> None:
        await self._limiter.note_backoff(delay_seconds)


def get_llm_rate_controller(settings: Settings) -> LLMRateController:
    config = build_llm_rate_limit_config(settings)
    return LLMRateController(config=config, limiter=get_llm_rate_limiter(config))


def compute_retry_delay_seconds(
    *,
    headers: Mapping[str, str] | Mapping[str, object],
    attempt: int,
    previous_delay: float | None,
    config: LLMRateLimitConfig,
) -> float:
    server_hint = _parse_retry_after_seconds(headers)
    if server_hint is not None and server_hint > 0:
        return server_hint
    exponential_cap = min(
        config.retry_max_delay_seconds,
        config.retry_base_delay_seconds * (2 ** max(0, attempt - 1)),
    )
    if previous_delay is None:
        jitter_delay = random.uniform(config.retry_base_delay_seconds, exponential_cap)
    else:
        upper_bound = max(previous_delay, config.retry_base_delay_seconds) * 3
        jitter_delay = min(
            config.retry_max_delay_seconds,
            random.uniform(config.retry_base_delay_seconds, upper_bound),
        )
    return min(config.retry_max_delay_seconds, jitter_delay)


def _parse_retry_after_seconds(headers: Mapping[str, str] | Mapping[str, object]) -> float | None:
    retry_after_ms = headers.get("retry-after-ms")
    if isinstance(retry_after_ms, str):
        parsed_milliseconds = _parse_plain_float(retry_after_ms)
        if parsed_milliseconds is not None:
            return parsed_milliseconds / 1000.0

    retry_after = headers.get("retry-after")
    if isinstance(retry_after, str):
        parsed_retry_after = _parse_retry_after_header(retry_after)
        if parsed_retry_after is not None:
            return parsed_retry_after

    for header_name in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        header_value = headers.get(header_name)
        if not isinstance(header_value, str):
            continue
        parsed_header_value = _parse_duration_value(header_value)
        if parsed_header_value is not None:
            return parsed_header_value
    return None


def _parse_retry_after_header(value: str) -> float | None:
    parsed_seconds = _parse_duration_value(value)
    if parsed_seconds is not None:
        return parsed_seconds
    try:
        parsed_date = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed_date.tzinfo is None:
        return None
    return max(0.0, parsed_date.timestamp() - time.time())


def _parse_duration_value(value: str) -> float | None:
    stripped = value.strip().lower()
    if not stripped:
        return None
    for suffix, scale in (("ms", 0.001), ("s", 1.0), ("m", 60.0), ("h", 3600.0)):
        if stripped.endswith(suffix):
            number = stripped[: -len(suffix)].strip()
            return float(number) * scale if number else None
    try:
        return float(stripped)
    except ValueError:
        return None


def _parse_plain_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except ValueError:
        return None
