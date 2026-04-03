from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.executor import ExecutionResult
from app.agent.reflector import ReflectionResult
from app.agent.turn_models import (
    AgentTurn,
    NextTurnDirective,
    ToolResultRecord,
    ToolUseRecord,
    TranscriptDelta,
)
from app.db.models import TaskNode, TaskNodeStatus, WorkflowRunStatus


@dataclass(frozen=True)
class TranscriptExecutionAppendResult:
    turn: AgentTurn
    delta: TranscriptDelta
    tool_use_record: ToolUseRecord
    tool_result_record: ToolResultRecord
    directive: NextTurnDirective


class TranscriptRuntimeService:
    _DIRECTIVE_PRIORITY = {
        NextTurnDirective.CONTINUE: 0,
        NextTurnDirective.RETRY_SAME_WAVE: 1,
        NextTurnDirective.REPLAN_SUBGRAPH: 2,
        NextTurnDirective.FINALIZE: 3,
        NextTurnDirective.STOP_LOOP: 4,
        NextTurnDirective.AWAIT_USER_INPUT: 5,
        NextTurnDirective.AWAIT_APPROVAL: 6,
    }

    def empty_state(self) -> dict[str, object]:
        return {
            "turns": [],
            "deltas": [],
            "tool_use_records": [],
            "tool_result_records": [],
            "compact_events": [],
            "reinjection_events": [],
            "last_directive": NextTurnDirective.CONTINUE.value,
        }

    def ensure_state(self, mutable_state: dict[str, object]) -> dict[str, object]:
        existing = mutable_state.get("runtime_transcript")
        runtime_state = existing if isinstance(existing, dict) else self.empty_state()
        for key in (
            "turns",
            "deltas",
            "tool_use_records",
            "tool_result_records",
            "compact_events",
            "reinjection_events",
        ):
            value = runtime_state.get(key)
            runtime_state[key] = (
                [item for item in value if isinstance(item, dict)]
                if isinstance(value, list)
                else []
            )
        last_directive = runtime_state.get("last_directive")
        runtime_state["last_directive"] = (
            str(last_directive)
            if isinstance(last_directive, str)
            else NextTurnDirective.CONTINUE.value
        )
        mutable_state["runtime_transcript"] = runtime_state
        return runtime_state

    def last_directive(self, mutable_state: dict[str, object]) -> NextTurnDirective:
        runtime_state = self.ensure_state(mutable_state)
        raw = runtime_state.get("last_directive")
        try:
            return NextTurnDirective(str(raw))
        except ValueError:
            return NextTurnDirective.CONTINUE

    def set_last_directive(
        self, mutable_state: dict[str, object], directive: NextTurnDirective
    ) -> NextTurnDirective:
        runtime_state = self.ensure_state(mutable_state)
        runtime_state["last_directive"] = directive.value
        return directive

    def append_execution(
        self,
        *,
        mutable_state: dict[str, object],
        task: TaskNode,
        execution: ExecutionResult,
        reflection: ReflectionResult,
        cycle_id: str,
        scheduler_group: str | None,
    ) -> TranscriptExecutionAppendResult:
        runtime_state = self.ensure_state(mutable_state)
        directive = self.directive_for_execution(execution=execution, reflection=reflection)
        turn_id = f"turn-{uuid4()}"
        delta = self._build_execution_delta(turn_id=turn_id, task=task, execution=execution)
        turn = AgentTurn(
            turn_id=turn_id,
            cycle_id=cycle_id,
            phase=self._task_phase(task),
            current_stage=self._task_stage(task),
            current_task_names=[task.name],
            assistant_reasoning_summary=self._assistant_reasoning_summary(
                task, execution, reflection
            ),
            transcript_delta_id=delta.delta_id,
            next_turn_directive=directive,
            started_at=execution.started_at.isoformat(),
            ended_at=execution.ended_at.isoformat(),
        )
        tool_use_record = ToolUseRecord(
            trace_id=execution.trace_id,
            tool_name=str(execution.tool_name or "workflow.tool"),
            task_id=task.id,
            task_name=task.name,
            cycle_id=cycle_id,
            scheduler_group=scheduler_group,
            started_at=execution.started_at.isoformat(),
        )
        tool_result_record = ToolResultRecord(
            trace_id=execution.trace_id,
            tool_name=str(execution.tool_name or "workflow.tool"),
            task_id=task.id,
            task_name=task.name,
            cycle_id=cycle_id,
            status=execution.status.value,
            transcript_block_count=len(execution.transcript_blocks),
            source_type=execution.source_type,
            source_name=execution.source_name,
            command_or_action=execution.command_or_action,
            input_payload=dict(execution.input_payload),
            output_payload=dict(execution.output_payload),
            citations=self._dict_list(execution.output_payload.get("citations")),
            artifacts=self._dict_list(execution.output_payload.get("artifacts")),
            started_at=execution.started_at.isoformat(),
            ended_at=execution.ended_at.isoformat(),
        )
        turns = self._dict_list(runtime_state.get("turns"))
        deltas = self._dict_list(runtime_state.get("deltas"))
        tool_use_records = self._dict_list(runtime_state.get("tool_use_records"))
        tool_result_records = self._dict_list(runtime_state.get("tool_result_records"))
        turns.append(turn.to_state())
        deltas.append(delta.to_state())
        tool_use_records.append(tool_use_record.to_state())
        tool_result_records.append(tool_result_record.to_state())
        runtime_state["turns"] = turns
        runtime_state["deltas"] = deltas
        runtime_state["tool_use_records"] = tool_use_records
        runtime_state["tool_result_records"] = tool_result_records
        runtime_state["last_directive"] = directive.value
        return TranscriptExecutionAppendResult(
            turn=turn,
            delta=delta,
            tool_use_record=tool_use_record,
            tool_result_record=tool_result_record,
            directive=directive,
        )

    def append_compact_boundary(
        self,
        *,
        mutable_state: dict[str, object],
        boundary: dict[str, object],
        cycle_id: str,
        current_stage: str | None,
        task_name: str,
    ) -> dict[str, object]:
        runtime_state = self.ensure_state(mutable_state)
        event_id = f"compact-event-{uuid4()}"
        now = str(boundary.get("created_at") or datetime.now(UTC).isoformat())
        block: dict[str, object] = {
            "kind": "compact_boundary",
            "content": str(boundary.get("compact_summary") or ""),
            "metadata": {
                "boundary_marker": str(boundary.get("boundary_marker") or ""),
                "created_at": now,
                "compact_metadata": self._dict(boundary.get("compact_metadata")),
            },
            "is_metadata_only": False,
        }
        turn = AgentTurn(
            turn_id=f"turn-{uuid4()}",
            cycle_id=cycle_id,
            phase="compact_runtime",
            current_stage=current_stage,
            current_task_names=[task_name] if task_name else [],
            assistant_reasoning_summary="Recorded compact boundary continuity event.",
            transcript_delta_id=None,
            next_turn_directive=self.last_directive(mutable_state),
            started_at=now,
            ended_at=now,
        )
        delta = TranscriptDelta(
            delta_id=f"delta-{uuid4()}",
            turn_id=turn.turn_id,
            compact_boundary_blocks=[block],
            metadata={
                "event_id": event_id,
                "event_type": "compact_boundary",
                "boundary_marker": str(boundary.get("boundary_marker") or ""),
            },
        )
        turn = AgentTurn(
            turn_id=turn.turn_id,
            cycle_id=turn.cycle_id,
            phase=turn.phase,
            current_stage=turn.current_stage,
            current_task_names=list(turn.current_task_names),
            assistant_reasoning_summary=turn.assistant_reasoning_summary,
            transcript_delta_id=delta.delta_id,
            next_turn_directive=turn.next_turn_directive,
            started_at=turn.started_at,
            ended_at=turn.ended_at,
        )
        event: dict[str, object] = {
            "event_id": event_id,
            "turn_id": turn.turn_id,
            "delta_id": delta.delta_id,
            "boundary_marker": str(boundary.get("boundary_marker") or ""),
            "summary": str(boundary.get("compact_summary") or ""),
            "created_at": now,
            "metadata": self._dict(boundary.get("compact_metadata")),
        }
        turns = self._dict_list(runtime_state.get("turns"))
        deltas = self._dict_list(runtime_state.get("deltas"))
        compact_events = self._dict_list(runtime_state.get("compact_events"))
        turns.append(turn.to_state())
        deltas.append(delta.to_state())
        compact_events.append(event)
        runtime_state["turns"] = turns
        runtime_state["deltas"] = deltas
        runtime_state["compact_events"] = compact_events
        return event

    def append_reinjection_event(
        self,
        *,
        mutable_state: dict[str, object],
        reinjection: dict[str, object],
        cycle_id: str,
        current_stage: str | None,
        task_name: str,
    ) -> dict[str, object]:
        runtime_state = self.ensure_state(mutable_state)
        existing_events = self.recent_reinjection_events(mutable_state, limit=1)
        if existing_events:
            latest = existing_events[-1]
            if latest.get("summary") == reinjection.get("summary") and latest.get(
                "boundary_marker"
            ) == reinjection.get("boundary_marker"):
                return latest
        event_id = f"reinjection-event-{uuid4()}"
        now = datetime.now(UTC).isoformat()
        block: dict[str, object] = {
            "kind": "reinjection",
            "content": str(reinjection.get("summary") or ""),
            "metadata": self._dict(reinjection.get("provenance")),
            "is_metadata_only": False,
        }
        turn = AgentTurn(
            turn_id=f"turn-{uuid4()}",
            cycle_id=cycle_id,
            phase="post_compact_reinjection",
            current_stage=current_stage,
            current_task_names=[task_name] if task_name else [],
            assistant_reasoning_summary="Recorded reinjection continuity event.",
            transcript_delta_id=None,
            next_turn_directive=self.last_directive(mutable_state),
            started_at=now,
            ended_at=now,
        )
        delta = TranscriptDelta(
            delta_id=f"delta-{uuid4()}",
            turn_id=turn.turn_id,
            reinjection_blocks=[block],
            metadata={"event_id": event_id, "event_type": "reinjection"},
        )
        turn = AgentTurn(
            turn_id=turn.turn_id,
            cycle_id=turn.cycle_id,
            phase=turn.phase,
            current_stage=turn.current_stage,
            current_task_names=list(turn.current_task_names),
            assistant_reasoning_summary=turn.assistant_reasoning_summary,
            transcript_delta_id=delta.delta_id,
            next_turn_directive=turn.next_turn_directive,
            started_at=turn.started_at,
            ended_at=turn.ended_at,
        )
        event: dict[str, object] = {
            "event_id": event_id,
            "turn_id": turn.turn_id,
            "delta_id": delta.delta_id,
            "summary": str(reinjection.get("summary") or ""),
            "boundary_marker": str(reinjection.get("boundary_marker") or ""),
            "created_at": now,
            "provenance": self._dict(reinjection.get("provenance")),
        }
        turns = self._dict_list(runtime_state.get("turns"))
        deltas = self._dict_list(runtime_state.get("deltas"))
        reinjection_events = self._dict_list(runtime_state.get("reinjection_events"))
        turns.append(turn.to_state())
        deltas.append(delta.to_state())
        reinjection_events.append(event)
        runtime_state["turns"] = turns
        runtime_state["deltas"] = deltas
        runtime_state["reinjection_events"] = reinjection_events
        return event

    def project_execution_record(
        self,
        *,
        session_id: str,
        task: TaskNode,
        execution: ExecutionResult,
        reflection: ReflectionResult,
        batch_cycle: int,
        retry_attempt: int,
        retry_count: int,
        transcript_delta_id: str,
        tool_result_record: ToolResultRecord,
    ) -> dict[str, object]:
        return {
            "id": tool_result_record.trace_id,
            "session_id": session_id,
            "task_node_id": task.id,
            "task_name": task.name,
            "source_type": execution.source_type,
            "source_name": execution.source_name,
            "command_or_action": execution.command_or_action,
            "input_json": dict(execution.input_payload),
            "output_json": dict(execution.output_payload),
            "status": execution.status.value,
            "batch_cycle": batch_cycle,
            "retry_attempt": retry_attempt,
            "retry_count": retry_count,
            "summary": task.metadata_json.get("summary"),
            "evidence_confidence": reflection.evidence_confidence,
            "started_at": execution.started_at.isoformat(),
            "ended_at": execution.ended_at.isoformat(),
            "transcript_delta_id": transcript_delta_id,
            "tool_name": tool_result_record.tool_name,
        }

    def recent_deltas(
        self, mutable_state: dict[str, object], *, limit: int = 6
    ) -> list[dict[str, object]]:
        runtime_state = self.ensure_state(mutable_state)
        deltas = runtime_state.get("deltas")
        return (
            [item for item in deltas if isinstance(item, dict)][-limit:]
            if isinstance(deltas, list)
            else []
        )

    def recent_tool_result_records(
        self, mutable_state: dict[str, object], *, limit: int = 6
    ) -> list[dict[str, object]]:
        runtime_state = self.ensure_state(mutable_state)
        records = runtime_state.get("tool_result_records")
        return (
            [item for item in records if isinstance(item, dict)][-limit:]
            if isinstance(records, list)
            else []
        )

    def recent_turns(
        self, mutable_state: dict[str, object], *, limit: int = 6
    ) -> list[dict[str, object]]:
        runtime_state = self.ensure_state(mutable_state)
        turns = runtime_state.get("turns")
        return (
            [item for item in turns if isinstance(item, dict)][-limit:]
            if isinstance(turns, list)
            else []
        )

    def recent_compact_events(
        self, mutable_state: dict[str, object], *, limit: int = 3
    ) -> list[dict[str, object]]:
        runtime_state = self.ensure_state(mutable_state)
        events = runtime_state.get("compact_events")
        return (
            [item for item in events if isinstance(item, dict)][-limit:]
            if isinstance(events, list)
            else []
        )

    def recent_reinjection_events(
        self, mutable_state: dict[str, object], *, limit: int = 3
    ) -> list[dict[str, object]]:
        runtime_state = self.ensure_state(mutable_state)
        events = runtime_state.get("reinjection_events")
        return (
            [item for item in events if isinstance(item, dict)][-limit:]
            if isinstance(events, list)
            else []
        )

    def history_summary(self, mutable_state: dict[str, object]) -> str:
        deltas = self.recent_deltas(mutable_state, limit=5)
        if not deltas:
            return "No prior workflow transcript runtime deltas are currently active."
        lines = ["Recent workflow transcript runtime history:"]
        for delta in deltas:
            metadata = self._dict(delta.get("metadata"))
            label = str(
                metadata.get("task_name")
                or metadata.get("tool_name")
                or metadata.get("event_type")
                or metadata.get("trace_id")
                or "runtime"
            )
            status = str(metadata.get("status") or metadata.get("event_type") or "recorded")
            content = self._preview_delta(delta)
            lines.append(f"- {label}: {status} {content}".strip())
        return "\n".join(lines)

    def prompt_continuity(
        self, mutable_state: dict[str, object], *, limit: int = 4
    ) -> dict[str, object]:
        deltas = self.recent_deltas(mutable_state, limit=limit)
        compact_events = self.recent_compact_events(mutable_state, limit=2)
        reinjection_events = self.recent_reinjection_events(mutable_state, limit=2)
        result_lines: list[str] = []
        for delta in deltas:
            for block in self._dict_list(delta.get("tool_result_blocks"))[-1:]:
                content = str(block.get("content") or "").strip()
                if content:
                    result_lines.append(content)
        compact_lines = [
            str(item.get("summary") or "")
            for item in compact_events
            if str(item.get("summary") or "").strip()
        ]
        reinjection_lines = [
            str(item.get("summary") or "")
            for item in reinjection_events
            if str(item.get("summary") or "").strip()
        ]
        provenance = {
            "source": "runtime_transcript",
            "recent_delta_ids": [str(delta.get("delta_id") or "") for delta in deltas],
            "tool_result_delta_ids": [
                str(delta.get("delta_id") or "")
                for delta in deltas
                if self._dict_list(delta.get("tool_result_blocks"))
            ],
            "compact_event_ids": [str(item.get("event_id") or "") for item in compact_events],
            "reinjection_event_ids": [
                str(item.get("event_id") or "") for item in reinjection_events
            ],
            "reinjected_components": ["tool_result_blocks", "compact_events", "reinjection_events"],
        }
        return {
            "recent_tool_result_continuity": "\n".join(result_lines),
            "compact_continuity": "\n".join(compact_lines),
            "reinjection_continuity": "\n".join(reinjection_lines),
            "provenance": provenance,
        }

    def session_source_text(self, mutable_state: dict[str, object], retrieval_summary: str) -> str:
        parts = [retrieval_summary]
        for turn in self.recent_turns(mutable_state, limit=4):
            summary = str(turn.get("assistant_reasoning_summary") or "")
            if summary:
                parts.append(f"Turn {turn.get('turn_id')}: {summary}")
        for delta in self.recent_deltas(mutable_state, limit=6):
            for collection_name in (
                "tool_result_blocks",
                "assistant_blocks",
                "compact_boundary_blocks",
                "reinjection_blocks",
            ):
                for block in self._dict_list(delta.get(collection_name))[:2]:
                    content = str(block.get("content") or "").strip()
                    if content:
                        parts.append(content)
        return "\n".join(part for part in parts if part.strip())

    def directive_for_execution(
        self, *, execution: ExecutionResult, reflection: ReflectionResult
    ) -> NextTurnDirective:
        interrupt_behavior = str(execution.output_payload.get("interrupt_behavior") or "")
        if execution.status is TaskNodeStatus.BLOCKED and interrupt_behavior == "require_approval":
            return NextTurnDirective.AWAIT_APPROVAL
        if execution.status is TaskNodeStatus.BLOCKED and interrupt_behavior == "user_interaction":
            return NextTurnDirective.AWAIT_USER_INPUT
        if reflection.replanning_suggestion is not None:
            return NextTurnDirective.REPLAN_SUBGRAPH
        if execution.status is TaskNodeStatus.COMPLETED:
            return NextTurnDirective.CONTINUE
        return NextTurnDirective.RETRY_SAME_WAVE

    def directive_for_run_status(self, status: WorkflowRunStatus) -> NextTurnDirective:
        if status is WorkflowRunStatus.DONE:
            return NextTurnDirective.FINALIZE
        if status is WorkflowRunStatus.NEEDS_APPROVAL:
            return NextTurnDirective.AWAIT_APPROVAL
        if status is WorkflowRunStatus.BLOCKED:
            return NextTurnDirective.AWAIT_USER_INPUT
        if status is WorkflowRunStatus.ERROR:
            return NextTurnDirective.REPLAN_SUBGRAPH
        return NextTurnDirective.STOP_LOOP

    @staticmethod
    def directive_to_next_action(directive: NextTurnDirective) -> str:
        mapping = {
            NextTurnDirective.CONTINUE: "continue",
            NextTurnDirective.RETRY_SAME_WAVE: "continue",
            NextTurnDirective.REPLAN_SUBGRAPH: "continue",
            NextTurnDirective.AWAIT_USER_INPUT: "await_user_input",
            NextTurnDirective.AWAIT_APPROVAL: "await_approval",
            NextTurnDirective.FINALIZE: "complete",
            NextTurnDirective.STOP_LOOP: "idle",
        }
        return mapping[directive]

    @classmethod
    def preferred_directive(
        cls,
        directives: list[NextTurnDirective],
        *,
        current: NextTurnDirective,
    ) -> NextTurnDirective:
        return max([current, *directives], key=lambda directive: cls._DIRECTIVE_PRIORITY[directive])

    @staticmethod
    def should_stop_current_cycle(directive: NextTurnDirective) -> bool:
        return directive in {
            NextTurnDirective.AWAIT_APPROVAL,
            NextTurnDirective.AWAIT_USER_INPUT,
            NextTurnDirective.FINALIZE,
            NextTurnDirective.STOP_LOOP,
        }

    def _build_execution_delta(
        self, *, turn_id: str, task: TaskNode, execution: ExecutionResult
    ) -> TranscriptDelta:
        tool_use_blocks: list[dict[str, object]] = []
        tool_result_blocks: list[dict[str, object]] = []
        tool_error_blocks: list[dict[str, object]] = []
        assistant_blocks: list[dict[str, object]] = []
        runtime_protocol: dict[str, object] = (
            dict(execution.runtime_protocol) if isinstance(execution.runtime_protocol, dict) else {}
        )
        if execution.transcript_blocks:
            for raw_block in execution.transcript_blocks:
                block = raw_block.as_dict()
                kind = str(block.get("kind") or "")
                if kind == "tool_use":
                    tool_use_blocks.append(block)
                elif kind == "tool_result":
                    tool_result_blocks.append(block)
                elif kind == "tool_error":
                    tool_error_blocks.append(block)
                else:
                    assistant_blocks.append(block)
        else:
            tool_use_blocks.append(
                {
                    "kind": "tool_use",
                    "content": (
                        f"Invoke {execution.tool_name or 'workflow.tool'} for task {task.name}."
                    ),
                    "metadata": {
                        "tool_name": execution.tool_name,
                        "task_name": task.name,
                        "status": execution.status.value,
                    },
                    "is_metadata_only": False,
                }
            )
            synthesized_block: dict[str, object] = {
                "kind": (
                    "tool_error"
                    if execution.status in {TaskNodeStatus.BLOCKED, TaskNodeStatus.FAILED}
                    else "tool_result"
                ),
                "content": str(
                    execution.output_payload.get("stderr")
                    or execution.output_payload.get("stdout")
                    or execution.command_or_action
                ),
                "metadata": {
                    "tool_name": execution.tool_name,
                    "task_name": task.name,
                    "status": execution.status.value,
                },
                "is_metadata_only": False,
            }
            if synthesized_block["kind"] == "tool_error":
                tool_error_blocks.append(synthesized_block)
            else:
                tool_result_blocks.append(synthesized_block)
        assistant_blocks.append(
            {
                "kind": "assistant_summary",
                "content": (f"Task {task.name} completed with status {execution.status.value}."),
                "metadata": {"trace_id": execution.trace_id},
                "is_metadata_only": False,
            }
        )
        return TranscriptDelta(
            delta_id=f"delta-{uuid4()}",
            turn_id=turn_id,
            tool_use_blocks=tool_use_blocks,
            tool_result_blocks=tool_result_blocks,
            tool_error_blocks=tool_error_blocks,
            assistant_blocks=assistant_blocks,
            metadata={
                "trace_id": execution.trace_id,
                "task_id": task.id,
                "task_name": task.name,
                "tool_name": execution.tool_name,
                "status": execution.status.value,
                "runtime_protocol": runtime_protocol,
            },
        )

    @staticmethod
    def _assistant_reasoning_summary(
        task: TaskNode, execution: ExecutionResult, reflection: ReflectionResult
    ) -> str:
        if reflection.conclusion == "success":
            return (
                f"{task.name} advanced with evidence confidence "
                f"{reflection.evidence_confidence:.2f}."
            )
        return (
            f"{task.name} ended as {execution.status.value}; "
            f"reflection suggested {reflection.replanning_suggestion or 'follow-up review'}."
        )

    @staticmethod
    def _task_phase(task: TaskNode) -> str:
        workflow_phase = task.metadata_json.get("workflow_phase")
        if isinstance(workflow_phase, str) and workflow_phase:
            return workflow_phase
        stage_key = task.metadata_json.get("stage_key")
        return str(stage_key or "workflow")

    @staticmethod
    def _task_stage(task: TaskNode) -> str | None:
        stage_key = task.metadata_json.get("stage_key")
        return stage_key if isinstance(stage_key, str) else None

    @staticmethod
    def _preview_delta(delta: dict[str, object]) -> str:
        for key in (
            "tool_result_blocks",
            "tool_error_blocks",
            "compact_boundary_blocks",
            "reinjection_blocks",
            "assistant_blocks",
        ):
            for block in TranscriptRuntimeService._dict_list(delta.get(key)):
                content = str(block.get("content") or "").strip()
                if content:
                    return content[:160]
        return ""

    @staticmethod
    def _dict(raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]
