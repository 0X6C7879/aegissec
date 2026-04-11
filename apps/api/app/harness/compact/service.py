from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.agent.token_budget import estimate_token_count, truncate_text_to_token_budget


class HarnessCompactService:
    def __init__(
        self,
        *,
        memory_service: Any,
        retained_tail: int = 10,
        microcompact_chars: int = 1200,
        full_compact_tokens: int = 6000,
    ) -> None:
        self._memory_service = memory_service
        self._retained_tail = retained_tail
        self._microcompact_chars = microcompact_chars
        self._full_compact_tokens = full_compact_tokens

    def maybe_compact(
        self,
        *,
        messages: list[dict[str, Any]],
        session_state: Any | None,
        render_compact_message: Callable[[str], dict[str, Any]],
        turn_count: int,
    ) -> list[dict[str, Any]]:
        compacted_messages = self._microcompact_messages(messages)
        if session_state is None or not self._should_full_compact(compacted_messages):
            return compacted_messages
        leading_messages, history_messages = self._split_leading_messages(compacted_messages)
        if len(history_messages) <= self._retained_tail:
            return compacted_messages
        retained_start = self._adjust_retained_start_for_tool_pairs(
            history_messages,
            max(len(history_messages) - self._retained_tail, 0),
        )
        archived_messages = history_messages[:retained_start]
        retained_messages = history_messages[retained_start:]
        compact_fragment = self._build_compact_fragment(
            archived_messages=archived_messages,
            session_state=session_state,
        )
        durable_entry = self._memory_service.write_compact_boundary(
            session_state=session_state,
            title=f"Compact boundary {turn_count}",
            summary="Compacted harness history for continued query execution.",
            body=compact_fragment,
        )
        session_state.compaction.recent_turns = self._retained_tail
        session_state.compaction.last_compacted_turn = turn_count
        session_state.compaction.active_compact_fragment = compact_fragment
        session_state.compaction.durable_artifact_ref = durable_entry.entry_id
        session_state.compaction.mode = "full"
        session_state.compaction.archived_message_count += len(archived_messages)
        return [
            *leading_messages,
            render_compact_message(compact_fragment),
            *retained_messages,
        ]

    def _microcompact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return messages
        compacted: list[dict[str, Any]] = []
        protected_index = max(len(messages) - self._retained_tail, 0)
        for index, message in enumerate(messages):
            if index >= protected_index:
                compacted.append(message)
                continue
            compacted.append(self._microcompact_message(message))
        return compacted

    def _microcompact_message(self, message: dict[str, Any]) -> dict[str, Any]:
        cloned = dict(message)
        content = cloned.get("content")
        if isinstance(content, str):
            cloned["content"] = truncate_text_to_token_budget(
                content,
                max(self._microcompact_chars // 4, 1),
            )
            return cloned
        if isinstance(content, list):
            compacted_parts: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    compacted_parts.append(part)
                    continue
                compacted_part = dict(part)
                part_content = compacted_part.get("content")
                if isinstance(part_content, str):
                    compacted_part["content"] = truncate_text_to_token_budget(
                        part_content,
                        max(self._microcompact_chars // 4, 1),
                    )
                compacted_parts.append(compacted_part)
            cloned["content"] = compacted_parts
        return cloned

    def _should_full_compact(self, messages: list[dict[str, Any]]) -> bool:
        if len(messages) > self._retained_tail + 8:
            return True
        total_tokens = sum(
            estimate_token_count(json.dumps(message, ensure_ascii=False)) for message in messages
        )
        return total_tokens >= self._full_compact_tokens

    def _split_leading_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        leading_messages: list[dict[str, Any]] = []
        history_start = 0
        for index, message in enumerate(messages):
            if message.get("role") == "system":
                leading_messages.append(message)
                history_start = index + 1
                continue
            break
        return leading_messages, messages[history_start:]

    @staticmethod
    def _adjust_retained_start_for_tool_pairs(
        history_messages: list[dict[str, Any]],
        retained_start: int,
    ) -> int:
        if retained_start <= 0 or retained_start >= len(history_messages):
            return retained_start
        current_message = history_messages[retained_start]
        if current_message.get("role") == "tool":
            for index in range(retained_start - 1, -1, -1):
                candidate = history_messages[index]
                if candidate.get("role") == "assistant" and candidate.get("tool_calls"):
                    if all(
                        message.get("role") == "tool"
                        for message in history_messages[index + 1 : retained_start + 1]
                    ):
                        return index
                    break
                if candidate.get("role") != "tool":
                    break
        if current_message.get(
            "role"
        ) == "user" and HarnessCompactService._has_anthropic_tool_result_blocks(current_message):
            candidate = history_messages[retained_start - 1]
            if candidate.get(
                "role"
            ) == "assistant" and HarnessCompactService._has_anthropic_tool_use_blocks(candidate):
                return retained_start - 1
        return retained_start

    @staticmethod
    def _has_anthropic_tool_use_blocks(message: dict[str, Any]) -> bool:
        content = message.get("content")
        return isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_use" for block in content
        )

    @staticmethod
    def _has_anthropic_tool_result_blocks(message: dict[str, Any]) -> bool:
        content = message.get("content")
        return isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content
        )

    def _build_compact_fragment(
        self,
        *,
        archived_messages: list[dict[str, Any]],
        session_state: Any,
    ) -> str:
        lines = [
            "## Compacted History",
            f"session_id: {session_state.session_id}",
        ]
        if session_state.goal:
            lines.append(f"goal: {session_state.goal}")
        if session_state.current_phase:
            lines.append(f"phase: {session_state.current_phase}")
        if session_state.retrieval_manifest.source_labels:
            lines.append(
                "memory_sources: " + ", ".join(session_state.retrieval_manifest.source_labels[:5])
            )
        if getattr(session_state, "semantic", None) is not None:
            semantic_state = session_state.semantic
            if semantic_state.active_hypotheses:
                lines.append(
                    "active_hypotheses: "
                    + ", ".join(str(item) for item in semantic_state.active_hypotheses[:5])
                )
            if semantic_state.evidence_ids:
                lines.append(
                    "evidence_ids: "
                    + ", ".join(str(item) for item in semantic_state.evidence_ids[:5])
                )
            if semantic_state.artifacts:
                lines.append(
                    "artifacts: " + ", ".join(str(item) for item in semantic_state.artifacts[:5])
                )
            if semantic_state.recent_tools:
                lines.append(
                    "recent_tools: "
                    + ", ".join(str(item) for item in semantic_state.recent_tools[:5])
                )
            if semantic_state.reason:
                lines.append(f"semantic_reason: {semantic_state.reason}")
        lines.append("")
        lines.append("### Archived Messages")
        for message in archived_messages[-12:]:
            role = str(message.get("role", "unknown"))
            rendered = truncate_text_to_token_budget(
                json.dumps(message.get("content", ""), ensure_ascii=False),
                120,
            )
            lines.append(f"- {role}: {rendered}")
        if session_state.retrieval_manifest.rendered_memory_fragment:
            lines.append("")
            lines.append(session_state.retrieval_manifest.rendered_memory_fragment)
        return "\n".join(line for line in lines if line is not None)
