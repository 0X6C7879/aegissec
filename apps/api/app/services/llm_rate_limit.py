from __future__ import annotations

import asyncio
import json
import random
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from functools import lru_cache

import httpx

from app.core.settings import Settings

WINDOW_SECONDS = 60.0
_TOKEN_ESTIMATION_DIVISOR = 4
_DURATION_PATTERN = re.compile(r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)?\s*$")
_RETRY_HINT_PATTERN = re.compile(
    r"(?:retry after|try again in)\s+(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LLMRateLimitConfig:
    max_concurrent_requests: int
    max_output_tokens: int
    max_retries: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float
    safety_ratio: float
    requests_per_minute_limit: int | None = None
    tokens_per_minute_limit: int | None = None
    input_tokens_per_minute_limit: int | None = None
    output_tokens_per_minute_limit: int | None = None


@dataclass(frozen=True, slots=True)
class LLMQuotaReservation:
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


def build_llm_rate_limit_config(settings: Settings) -> LLMRateLimitConfig:
    return LLMRateLimitConfig(
        max_concurrent_requests=settings.llm_max_concurrency,
        max_output_tokens=settings.llm_max_output_tokens,
        max_retries=settings.llm_rate_limit_max_retries,
        retry_base_delay_seconds=settings.llm_rate_limit_base_delay_ms / 1000.0,
        retry_max_delay_seconds=float(settings.llm_rate_limit_max_delay_seconds),
        safety_ratio=settings.llm_rate_limit_safety_ratio,
        requests_per_minute_limit=settings.llm_rate_limit_rpm,
        tokens_per_minute_limit=settings.llm_rate_limit_tpm_total,
        input_tokens_per_minute_limit=settings.llm_rate_limit_tpm_input,
        output_tokens_per_minute_limit=settings.llm_rate_limit_tpm_output,
    )


def build_quota_reservation(
    payload: dict[str, object],
    *,
    default_output_tokens: int,
) -> LLMQuotaReservation:
    serialized_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    input_tokens = max(1, len(serialized_payload) // _TOKEN_ESTIMATION_DIVISOR)
    output_tokens = _positive_int(payload.get("max_tokens")) or default_output_tokens
    return LLMQuotaReservation(
        request_count=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def compute_retry_delay_seconds(
    response: httpx.Response,
    *,
    attempt: int,
    config: LLMRateLimitConfig,
) -> float:
    server_hint = parse_retry_after_seconds(response) or 0.0
    exponential_ceiling = min(
        config.retry_max_delay_seconds,
        config.retry_base_delay_seconds * (2**attempt),
    )
    if exponential_ceiling <= config.retry_base_delay_seconds:
        jitter_delay = config.retry_base_delay_seconds
    else:
        jitter_delay = random.uniform(config.retry_base_delay_seconds, exponential_ceiling)
    return min(config.retry_max_delay_seconds, max(server_hint, jitter_delay))


def parse_retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after_ms = response.headers.get("retry-after-ms")
    if retry_after_ms:
        milliseconds = _parse_numeric_duration(retry_after_ms)
        if milliseconds is not None:
            return milliseconds / 1000.0

    retry_after = response.headers.get("retry-after")
    if retry_after:
        parsed_retry_after = _parse_retry_after_header(retry_after)
        if parsed_retry_after is not None:
            return parsed_retry_after

    for header_name in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        header_value = response.headers.get(header_name)
        if not header_value:
            continue
        parsed_header = _parse_numeric_duration(header_value)
        if parsed_header is not None:
            return parsed_header

    try:
        response_text = response.text
    except Exception:
        return None

    match = _RETRY_HINT_PATTERN.search(response_text)
    if match is None:
        return None
    return _duration_to_seconds(match.group("value"), match.group("unit"))


class LLMRateLimiter:
    def __init__(self, config: LLMRateLimitConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(config.max_concurrent_requests)
        self._cooldown_until = 0.0
        self._reservations: deque[tuple[float, LLMQuotaReservation]] = deque()

    async def acquire(self, reservation: LLMQuotaReservation) -> None:
        while True:
            await self._slots.acquire()
            wait_seconds = await self._reserve_or_get_delay(reservation)
            if wait_seconds <= 0:
                return
            self._slots.release()
            await asyncio.sleep(wait_seconds + random.uniform(0.0, min(0.25, wait_seconds * 0.1)))

    def release(self) -> None:
        self._slots.release()

    async def note_backoff(self, delay_seconds: float) -> None:
        if delay_seconds <= 0:
            return
        async with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay_seconds)

    async def _reserve_or_get_delay(self, reservation: LLMQuotaReservation) -> float:
        async with self._lock:
            now = time.monotonic()
            self._prune_reservations(now)
            cooldown_wait = max(0.0, self._cooldown_until - now)
            quota_wait = self._compute_quota_wait_seconds(now, reservation)
            wait_seconds = max(cooldown_wait, quota_wait)
            if wait_seconds <= 0:
                self._reservations.append((now, reservation))
            return wait_seconds

    def _prune_reservations(self, now: float) -> None:
        while self._reservations and (now - self._reservations[0][0]) >= WINDOW_SECONDS:
            self._reservations.popleft()

    def _compute_quota_wait_seconds(self, now: float, reservation: LLMQuotaReservation) -> float:
        totals = LLMQuotaReservation(
            request_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )
        for _, existing_reservation in self._reservations:
            totals = LLMQuotaReservation(
                request_count=totals.request_count + existing_reservation.request_count,
                input_tokens=totals.input_tokens + existing_reservation.input_tokens,
                output_tokens=totals.output_tokens + existing_reservation.output_tokens,
                total_tokens=totals.total_tokens + existing_reservation.total_tokens,
            )

        wait_seconds = 0.0
        wait_seconds = max(
            wait_seconds,
            self._wait_for_dimension(
                now=now,
                current_total=totals.request_count,
                incoming=reservation.request_count,
                limit=self._effective_limit(self._config.requests_per_minute_limit),
                selector=lambda item: item.request_count,
            ),
        )
        wait_seconds = max(
            wait_seconds,
            self._wait_for_dimension(
                now=now,
                current_total=totals.input_tokens,
                incoming=reservation.input_tokens,
                limit=self._effective_limit(self._config.input_tokens_per_minute_limit),
                selector=lambda item: item.input_tokens,
            ),
        )
        wait_seconds = max(
            wait_seconds,
            self._wait_for_dimension(
                now=now,
                current_total=totals.output_tokens,
                incoming=reservation.output_tokens,
                limit=self._effective_limit(self._config.output_tokens_per_minute_limit),
                selector=lambda item: item.output_tokens,
            ),
        )
        wait_seconds = max(
            wait_seconds,
            self._wait_for_dimension(
                now=now,
                current_total=totals.total_tokens,
                incoming=reservation.total_tokens,
                limit=self._effective_limit(self._config.tokens_per_minute_limit),
                selector=lambda item: item.total_tokens,
            ),
        )
        return wait_seconds

    def _wait_for_dimension(
        self,
        *,
        now: float,
        current_total: int,
        incoming: int,
        limit: int | None,
        selector: Callable[[LLMQuotaReservation], int],
    ) -> float:
        if limit is None or current_total + incoming <= limit:
            return 0.0

        remaining_total = current_total
        for timestamp, reservation in self._reservations:
            remaining_total -= selector(reservation)
            if remaining_total + incoming <= limit:
                return max(0.0, (timestamp + WINDOW_SECONDS) - now)
        return WINDOW_SECONDS

    def _effective_limit(self, raw_limit: int | None) -> int | None:
        if raw_limit is None:
            return None
        return max(1, int(raw_limit * self._config.safety_ratio))


@lru_cache(maxsize=16)
def get_llm_rate_limiter(config: LLMRateLimitConfig) -> LLMRateLimiter:
    return LLMRateLimiter(config)


def reset_llm_rate_limiter_cache() -> None:
    get_llm_rate_limiter.cache_clear()


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _parse_retry_after_header(value: str) -> float | None:
    parsed_duration = _parse_numeric_duration(value)
    if parsed_duration is not None:
        return parsed_duration
    try:
        parsed_date = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed_date.tzinfo is None:
        return None
    return max(0.0, parsed_date.timestamp() - time.time())


def _parse_numeric_duration(value: str) -> float | None:
    match = _DURATION_PATTERN.match(value)
    if match is None:
        return None
    return _duration_to_seconds(match.group("value"), match.group("unit"))


def _duration_to_seconds(value: str, unit: str | None) -> float:
    numeric_value = float(value)
    normalized_unit = (unit or "s").lower()
    if normalized_unit == "ms":
        return numeric_value / 1000.0
    if normalized_unit == "m":
        return numeric_value * 60.0
    if normalized_unit == "h":
        return numeric_value * 3600.0
    return numeric_value
