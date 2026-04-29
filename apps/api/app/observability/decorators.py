from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from app.observability.recorder import get_recorder

P = ParamSpec("P")
R = TypeVar("R")


def trace_span(
    name: str | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            span_name = name or func.__name__
            recorder = get_recorder()
            span = recorder.start_span(span_name)
            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                span.finish(error=f"{type(exc).__name__}: {exc}")
                recorder.record(span)
                raise
            span.finish()
            recorder.record(span)
            return result

        return wrapper

    return decorator
