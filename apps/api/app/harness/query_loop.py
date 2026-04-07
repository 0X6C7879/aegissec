from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from .messages import ChatRuntimeError, GenerationCallbacks, ToolCallResult


class QueryLoop:
    def __init__(self, *, max_turns: int) -> None:
        self._max_turns = max_turns

    async def run(
        self,
        engine: BaseQueryEngine,
        *,
        execute_tool: Callable[[object], Awaitable[object]] | None,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        for _ in range(self._max_turns):
            engine.usage.model_turns += 1
            turn_result = await engine.request_turn(
                allow_tools=execute_tool is not None,
                callbacks=callbacks,
            )

            if turn_result.tool_calls:
                if execute_tool is None:
                    raise ChatRuntimeError("LLM requested tools but no executor is available.")
                engine.pending_continuation = True
                tool_results: list[ToolCallResult] = []
                for tool_call in turn_result.tool_calls:
                    tool_results.append(cast(ToolCallResult, await execute_tool(tool_call)))
                engine.usage.tool_rounds += 1
                engine.usage.tool_calls += len(tool_results)
                engine.append_tool_results(
                    assistant_payload=turn_result.assistant_payload,
                    tool_calls=turn_result.tool_calls,
                    tool_results=tool_results,
                )
                engine.maybe_auto_compact()
                continue

            engine.pending_continuation = False
            if turn_result.text_content:
                return turn_result.text_content

            raise ChatRuntimeError("Model returned neither tool calls nor final content.")

        engine.pending_continuation = False
        return await engine.generate_tool_budget_reply(callbacks=callbacks)


from .query_engine import BaseQueryEngine  # noqa: E402  # isort: skip
