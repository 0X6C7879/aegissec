from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from .messages import ChatRuntimeError, GenerationCallbacks, ToolCallResult


class QueryLoop:
    def __init__(self, *, max_turns: int, max_budget_cycles: int = 1) -> None:
        self._max_turns = max_turns
        self._max_budget_cycles = max_budget_cycles

    async def run(
        self,
        engine: BaseQueryEngine,
        *,
        execute_tool: Callable[[object], Awaitable[object]] | None,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        for cycle_index in range(1, self._max_budget_cycles + 1):
            for _ in range(self._max_turns):
                synthetic_tool_call = engine.dequeue_synthetic_tool_call()
                if synthetic_tool_call is not None:
                    if execute_tool is None:
                        raise ChatRuntimeError(
                            "Slash action requested a tool but no executor is available."
                        )
                    engine.pending_continuation = True
                    synthetic_tool_result = cast(
                        ToolCallResult, await execute_tool(synthetic_tool_call)
                    )
                    engine.usage.tool_rounds += 1
                    engine.usage.tool_calls += 1
                    engine.append_tool_results(
                        assistant_payload=engine.build_synthetic_assistant_payload(
                            [synthetic_tool_call]
                        ),
                        tool_calls=[synthetic_tool_call],
                        tool_results=[synthetic_tool_result],
                    )
                    engine.maybe_auto_compact()
                    continue

                pending_injections = await self._drain_context_injections(callbacks)
                if pending_injections:
                    engine.append_context_injections(pending_injections)
                    engine.pending_continuation = True
                    await self._notify_context_injections_applied(callbacks, pending_injections)

                engine.usage.model_turns += 1
                turn_result = await engine.request_turn(
                    allow_tools=execute_tool is not None,
                    callbacks=callbacks,
                )

                if turn_result.tool_calls:
                    if execute_tool is None:
                        raise ChatRuntimeError("LLM requested tools but no executor is available.")
                    engine.pending_continuation = True
                    batch_execute = cast(
                        Callable[[list[Any]], Awaitable[list[ToolCallResult]]] | None,
                        getattr(execute_tool, "__batch_execute__", None),
                    )
                    if callable(batch_execute):
                        tool_results = await batch_execute(turn_result.tool_calls)
                    else:
                        tool_results = []
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

                post_turn_injections = await self._drain_context_injections(callbacks)
                if post_turn_injections:
                    engine.append_assistant_response_to_history(turn_result.assistant_payload)
                    engine.append_context_injections(post_turn_injections)
                    engine.pending_continuation = True
                    engine.maybe_auto_compact()
                    await self._notify_context_injections_applied(callbacks, post_turn_injections)
                    continue

                engine.pending_continuation = False
                if turn_result.text_content:
                    return turn_result.text_content

                raise ChatRuntimeError("Model returned neither tool calls nor final content.")

            if cycle_index >= self._max_budget_cycles:
                break

            reflection = await self._generate_budget_reflection(
                engine,
                callbacks=callbacks,
                cycle_index=cycle_index,
            )
            if not reflection:
                break

            engine.append_budget_reflection(
                reflection,
                cycle_index=cycle_index,
                max_cycles=self._max_budget_cycles,
            )
            engine.pending_continuation = True
            engine.maybe_auto_compact()

        engine.pending_continuation = False
        return await engine.generate_tool_budget_reply(callbacks=callbacks)

    async def _generate_budget_reflection(
        self,
        engine: BaseQueryEngine,
        *,
        callbacks: GenerationCallbacks | None,
        cycle_index: int,
    ) -> str | None:
        del callbacks
        reflection_generator = getattr(engine, "generate_tool_budget_reflection", None)
        if not callable(reflection_generator):
            return None
        engine.usage.model_turns += 1
        reflection = await reflection_generator(
            callbacks=None,
            cycle_index=cycle_index,
            max_cycles=self._max_budget_cycles,
        )
        normalized = reflection.strip() if isinstance(reflection, str) else ""
        return normalized or None

    async def _drain_context_injections(
        self,
        callbacks: GenerationCallbacks | None,
    ) -> list[str]:
        if callbacks is None or callbacks.consume_context_injections is None:
            return []
        injections = await callbacks.consume_context_injections()
        return [injection for injection in injections if injection.strip()]

    async def _notify_context_injections_applied(
        self,
        callbacks: GenerationCallbacks | None,
        injections: list[str],
    ) -> None:
        if not injections or callbacks is None or callbacks.on_context_injection_applied is None:
            return
        await callbacks.on_context_injection_applied(injections)


from .query_engine import BaseQueryEngine  # noqa: E402  # isort: skip
