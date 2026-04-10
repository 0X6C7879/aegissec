from __future__ import annotations

import asyncio

from pytest import MonkeyPatch

from app.services.llm_rate_limit import LLMQuotaReservation, LLMRateLimitConfig, LLMRateLimiter


def test_rate_limiter_waits_for_request_window_capacity(monkeypatch: MonkeyPatch) -> None:
    now = 1_000.0
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    monkeypatch.setattr("app.services.llm_rate_limit.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.services.llm_rate_limit.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.services.llm_rate_limit.random.uniform", lambda start, end: 0.0)

    limiter = LLMRateLimiter(
        LLMRateLimitConfig(
            max_concurrent_requests=1,
            max_output_tokens=128,
            max_retries=0,
            retry_base_delay_seconds=0.5,
            retry_max_delay_seconds=5.0,
            safety_ratio=1.0,
            requests_per_minute_limit=1,
        )
    )
    reservation = LLMQuotaReservation(
        request_count=1,
        input_tokens=32,
        output_tokens=64,
        total_tokens=96,
    )

    async def run() -> None:
        await limiter.acquire(reservation)
        limiter.release()
        await limiter.acquire(reservation)
        limiter.release()

    asyncio.run(run())

    assert sleeps
    assert sleeps[0] >= 60.0


def test_rate_limiter_respects_shared_backoff_cooldown(monkeypatch: MonkeyPatch) -> None:
    now = 2_000.0
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    monkeypatch.setattr("app.services.llm_rate_limit.time.monotonic", fake_monotonic)
    monkeypatch.setattr("app.services.llm_rate_limit.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.services.llm_rate_limit.random.uniform", lambda start, end: 0.0)

    limiter = LLMRateLimiter(
        LLMRateLimitConfig(
            max_concurrent_requests=1,
            max_output_tokens=128,
            max_retries=0,
            retry_base_delay_seconds=0.5,
            retry_max_delay_seconds=5.0,
            safety_ratio=1.0,
            requests_per_minute_limit=10,
        )
    )
    reservation = LLMQuotaReservation(
        request_count=1,
        input_tokens=16,
        output_tokens=32,
        total_tokens=48,
    )

    async def run() -> None:
        await limiter.note_backoff(2.0)
        await limiter.acquire(reservation)
        limiter.release()

    asyncio.run(run())

    assert sleeps
    assert sleeps[0] >= 2.0
