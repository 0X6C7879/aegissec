from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import Any, cast

from app.core.settings import get_settings

from .messages import ChatRuntimeError, GenerationCallbacks, ToolCallRequest, ToolCallResult


class QueryLoop:
    def __init__(
        self,
        *,
        max_turns: int | None = None,
        max_budget_cycles: int | None = None,
    ) -> None:
        settings = get_settings()
        self._max_turns = max_turns or settings.chat_auto_tool_turn_limit
        self._max_budget_cycles = max_budget_cycles or settings.chat_auto_tool_budget_cycles
        self._attempt_history: list[dict[str, str | None]] = []
        self._loop_start_time = time.monotonic()
        self._total_tool_calls = 0
        self._no_progress_turns = 0
        self._last_tool_result_hash: str | None = None

    async def run(
        self,
        engine: BaseQueryEngine,
        *,
        execute_tool: Callable[[object], Awaitable[object]] | None,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        self._reset_limit_state()
        for cycle_index in range(1, self._max_budget_cycles + 1):
            for _ in range(self._max_turns):
                stop_reason = self._check_limits()
                if stop_reason is not None:
                    engine.pending_continuation = False
                    return self._build_limit_stop_reply(stop_reason)

                synthetic_tool_call = engine.dequeue_synthetic_tool_call()
                if synthetic_tool_call is not None:
                    if execute_tool is None:
                        raise ChatRuntimeError(
                            "Slash action requested a tool but no executor is available."
                        )
                    stop_reason = self._check_limits()
                    if stop_reason is not None:
                        engine.pending_continuation = False
                        return self._build_limit_stop_reply(stop_reason)
                    projected_stop_reason = self._projected_tool_call_limit_reason(1)
                    if projected_stop_reason is not None:
                        engine.pending_continuation = False
                        return self._build_limit_stop_reply(projected_stop_reason)
                    engine.pending_continuation = True
                    synthetic_tool_result = cast(
                        ToolCallResult, await execute_tool(synthetic_tool_call)
                    )
                    self._total_tool_calls += 1
                    self._record_tool_progress([synthetic_tool_call], [synthetic_tool_result])
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
                    stop_reason = self._check_limits()
                    if stop_reason is not None:
                        engine.pending_continuation = False
                        return self._build_limit_stop_reply(stop_reason)
                    projected_stop_reason = self._projected_tool_call_limit_reason(
                        len(turn_result.tool_calls)
                    )
                    if projected_stop_reason is not None:
                        engine.pending_continuation = False
                        return self._build_limit_stop_reply(projected_stop_reason)
                    engine.pending_continuation = True
                    tool_results = await self._execute_tool_round(
                        turn_result.tool_calls,
                        execute_tool=execute_tool,
                    )
                    self._total_tool_calls += len(tool_results)
                    self._record_tool_progress(turn_result.tool_calls, tool_results)
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

            stop_reason = self._check_limits()
            if stop_reason is not None:
                engine.pending_continuation = False
                return self._build_limit_stop_reply(stop_reason)

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

    def _reset_limit_state(self) -> None:
        self._loop_start_time = time.monotonic()
        self._total_tool_calls = 0
        self._no_progress_turns = 0
        self._last_tool_result_hash = None

    def _check_limits(self) -> str | None:
        settings = get_settings()
        elapsed = time.monotonic() - self._loop_start_time

        if elapsed > settings.agent_max_elapsed_seconds:
            return f"elapsed time limit ({settings.agent_max_elapsed_seconds}s) exceeded"

        if self._total_tool_calls > settings.agent_max_total_tool_calls:
            return f"total tool call limit ({settings.agent_max_total_tool_calls}) exceeded"

        if self._no_progress_turns >= settings.agent_max_no_progress_turns:
            return f"no progress for {settings.agent_max_no_progress_turns} consecutive turns"

        return None

    def _projected_tool_call_limit_reason(self, tool_call_count: int) -> str | None:
        settings = get_settings()
        if self._total_tool_calls + tool_call_count > settings.agent_max_total_tool_calls:
            return f"total tool call limit ({settings.agent_max_total_tool_calls}) exceeded"
        return None

    @staticmethod
    def _build_limit_stop_reply(reason: str) -> str:
        return f"Autonomous loop stopped because {reason}."

    def _record_tool_progress(self, tool_calls: list[ToolCallRequest], tool_results: list[ToolCallResult]) -> None:
        result_hash = self._tool_results_hash(tool_calls, tool_results)
        if result_hash == self._last_tool_result_hash:
            self._no_progress_turns += 1
        else:
            self._no_progress_turns = 0
        self._last_tool_result_hash = result_hash

    @staticmethod
    def _tool_results_hash(tool_calls: list[ToolCallRequest], tool_results: list[ToolCallResult]) -> str:
        try:
            call_data = [
                {"name": tc.tool_name, "args": tc.arguments} for tc in tool_calls
            ]
            result_data = [
                tool_result.model_dump(mode="json") for tool_result in tool_results
            ]
            serialized = json.dumps(
                {"calls": call_data, "results": result_data},
                ensure_ascii=False,
                sort_keys=True,
            )
        except (TypeError, AttributeError):
            call_part = "\n".join(
                f"{getattr(tc, 'tool_name', '?')}:{getattr(tc, 'arguments', {})}" for tc in tool_calls
            )
            result_part = "\n".join(str(tool_result) for tool_result in tool_results)
            serialized = f"calls:\n{call_part}\nresults:\n{result_part}"
        return hashlib.sha256(serialized.encode()).hexdigest()

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
        typed_reflection_generator = cast(
            Callable[..., Awaitable[str | None]], reflection_generator
        )
        engine.usage.model_turns += 1
        recent_attempts_summary = self._recent_attempts_summary()
        reflection = await typed_reflection_generator(
            callbacks=None,
            cycle_index=cycle_index,
            max_cycles=self._max_budget_cycles,
            recent_attempts_summary=recent_attempts_summary,
        )
        normalized = reflection.strip() if isinstance(reflection, str) else ""
        return normalized or None

    async def _execute_tool_round(
        self,
        tool_calls: list[Any],
        *,
        execute_tool: Callable[[object], Awaitable[object]],
    ) -> list[ToolCallResult]:
        blocked_results: dict[int, ToolCallResult] = {}
        executable_calls: list[Any] = []
        executable_indexes: list[int] = []
        seen_attempts = list(self._attempt_history)

        for index, tool_call in enumerate(tool_calls):
            duplicate = self._find_duplicate_attempt(tool_call, seen_attempts=seen_attempts)
            if duplicate is not None:
                blocked_result = self._build_repeated_attempt_result(tool_call, duplicate=duplicate)
                blocked_results[index] = blocked_result
                self._remember_attempt(tool_call, blocked_result)
                seen_attempts.append(self._attempt_history[-1])
                continue
            executable_calls.append(tool_call)
            executable_indexes.append(index)
            seen_attempts.append(self._attempt_projection(tool_call))

        executed_results: list[ToolCallResult] = []
        if executable_calls:
            batch_execute = cast(
                Callable[[list[Any]], Awaitable[list[ToolCallResult]]] | None,
                getattr(execute_tool, "__batch_execute__", None),
            )
            if callable(batch_execute):
                executed_results = await batch_execute(executable_calls)
            else:
                for tool_call in executable_calls:
                    executed_results.append(cast(ToolCallResult, await execute_tool(tool_call)))
            for tool_call, tool_result in zip(executable_calls, executed_results, strict=True):
                self._remember_attempt(tool_call, tool_result)

        ordered_results: list[ToolCallResult] = []
        executed_by_index = dict(zip(executable_indexes, executed_results, strict=True))
        for index in range(len(tool_calls)):
            if index in blocked_results:
                ordered_results.append(blocked_results[index])
                continue
            ordered_results.append(executed_by_index[index])
        return ordered_results

    def _find_duplicate_attempt(
        self,
        tool_call: Any,
        *,
        seen_attempts: list[dict[str, str | None]],
    ) -> dict[str, str | None] | None:
        candidate = self._attempt_projection(tool_call)
        for attempt in reversed(seen_attempts[-12:]):
            if self._is_duplicate_attempt(candidate, attempt):
                return attempt
        return None

    def _remember_attempt(self, tool_call: Any, tool_result: ToolCallResult) -> None:
        record = self._attempt_projection(tool_call)
        payload_status = tool_result.payload.get("status")
        record["status"] = (
            payload_status if isinstance(payload_status, str) else tool_result.safe_summary
        )
        self._attempt_history.append(record)
        if len(self._attempt_history) > 24:
            self._attempt_history = self._attempt_history[-24:]

    def _attempt_projection(self, tool_call: Any) -> dict[str, str | None]:
        tool_name = getattr(tool_call, "tool_name", "")
        arguments = getattr(tool_call, "arguments", {}) or {}
        command = self._normalized_command(arguments)
        return {
            "tool_name": str(tool_name),
            "arg_signature": self._argument_signature(arguments),
            "command": command,
            "program": self._primary_program(command),
            "target": self._primary_target(arguments, command),
            "summary": self._attempt_summary(str(tool_name), arguments, command),
            "status": None,
        }

    @staticmethod
    def _argument_signature(arguments: Any) -> str:
        try:
            return json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(arguments or {})

    @staticmethod
    def _normalized_command(arguments: Any) -> str | None:
        if not isinstance(arguments, dict):
            return None
        command = arguments.get("command")
        if not isinstance(command, str):
            return None
        normalized = " ".join(command.strip().split())
        return normalized or None

    @staticmethod
    def _primary_program(command: str | None) -> str | None:
        if not command:
            return None
        first_token = command.split(" ", 1)[0].strip()
        return first_token.casefold() or None

    @staticmethod
    def _primary_target(arguments: Any, command: str | None) -> str | None:
        if isinstance(arguments, dict):
            for key in ("terminal_id", "job_id", "skill_name_or_id", "mcp_tool_name"):
                value = arguments.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().casefold()
        if not command:
            return None
        matches = re.findall(r"https?://[^\s'\"`]+", command)
        if not matches:
            return None
        target = matches[0]
        target = target.split("?", 1)[0].rstrip("/")
        return target.casefold() or None

    @staticmethod
    def _attempt_summary(tool_name: str, arguments: Any, command: str | None) -> str:
        if command:
            preview = command[:160]
            return f"{tool_name}: {preview}"
        if isinstance(arguments, dict):
            keys = ", ".join(sorted(str(key) for key in arguments.keys())[:6])
            return f"{tool_name}: args[{keys}]"
        return tool_name

    @staticmethod
    def _is_duplicate_attempt(
        candidate: dict[str, str | None],
        previous: dict[str, str | None],
    ) -> bool:
        if candidate.get("tool_name") != previous.get("tool_name"):
            return False
        if candidate.get("arg_signature") == previous.get("arg_signature"):
            return True
        candidate_command = candidate.get("command")
        previous_command = previous.get("command")
        if not candidate_command or not previous_command:
            return False
        if candidate.get("program") != previous.get("program"):
            return False
        candidate_target = candidate.get("target")
        previous_target = previous.get("target")
        if candidate_target and previous_target and candidate_target != previous_target:
            return False
        similarity = SequenceMatcher(
            None,
            candidate_command.casefold(),
            previous_command.casefold(),
        ).ratio()
        if candidate_target and previous_target:
            return similarity >= 0.96
        return similarity >= 0.985

    @staticmethod
    def _build_repeated_attempt_result(
        tool_call: Any,
        *,
        duplicate: dict[str, str | None],
    ) -> ToolCallResult:
        duplicate_summary = duplicate.get("summary") or "recent attempt"
        payload = {
            "status": "blocked_repeated_attempt",
            "blocked": True,
            "reason": (
                "This tool call was blocked because it is materially too similar to a recent "
                "attempt and is unlikely to produce new evidence."
            ),
            "duplicate_of": duplicate_summary,
            "guidance": (
                "Pivot to a different endpoint, credential strategy, artifact source, or attack "
                "surface instead of repeating the same command family."
            ),
        }
        return ToolCallResult(
            tool_name=str(getattr(tool_call, "tool_name", "tool")),
            tool_call_id=getattr(tool_call, "tool_call_id", None),
            payload=payload,
            safe_summary=(
                "Blocked repeated tool attempt; choose a materially different next action."
            ),
        )

    def _recent_attempts_summary(self) -> str:
        if not self._attempt_history:
            return ""
        lines = ["Recent autonomous attempts:"]
        for attempt in self._attempt_history[-6:]:
            summary = attempt.get("summary") or attempt.get("tool_name") or "tool"
            status = attempt.get("status") or "unknown"
            lines.append(f"- {summary} | status={status}")
        lines.append("Avoid repeating these families unless new evidence justifies it.")
        return "\n".join(lines)

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
