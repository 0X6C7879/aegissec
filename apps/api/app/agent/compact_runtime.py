from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.agent.context_models import ContextProjection
from app.agent.token_budget import estimate_token_count


@dataclass(frozen=True)
class CompactRuntimeMetrics:
    rough_token_estimate: int
    message_count: int
    execution_record_count: int

    def to_state(self) -> dict[str, object]:
        return {
            "rough_token_estimate": self.rough_token_estimate,
            "message_count": self.message_count,
            "execution_record_count": self.execution_record_count,
        }


@dataclass(frozen=True)
class CompactRuntimeThresholds:
    rough_token_threshold: int = 3200
    message_count_threshold: int = 12
    execution_record_threshold: int = 8

    def to_state(self) -> dict[str, object]:
        return {
            "rough_token_threshold": self.rough_token_threshold,
            "message_count_threshold": self.message_count_threshold,
            "execution_record_threshold": self.execution_record_threshold,
        }


class CompactRuntimeService:
    DEFAULT_THRESHOLDS = CompactRuntimeThresholds()

    def build_runtime_state(
        self,
        *,
        mutable_state: dict[str, object],
        retrieval_summary: str,
        memory_summary: str,
        history_summary: str,
        projection: ContextProjection,
        active_task_name: str,
        current_stage: str | None,
    ) -> dict[str, object]:
        thresholds = self._resolve_thresholds(mutable_state)
        metrics = self._build_metrics(
            mutable_state=mutable_state,
            retrieval_summary=retrieval_summary,
            memory_summary=memory_summary,
            history_summary=history_summary,
            projection=projection,
        )
        runtime_store = self._ensure_runtime_store(mutable_state, thresholds)
        latest_boundary = self._dict(runtime_store.get("latest_boundary"))
        triggered = self._should_compact(metrics, thresholds)

        if triggered and self._should_create_boundary(latest_boundary, metrics):
            latest_boundary = self._build_boundary(
                mutable_state=mutable_state,
                metrics=metrics,
                thresholds=thresholds,
                projection=projection,
                retrieval_summary=retrieval_summary,
                memory_summary=memory_summary,
                history_summary=history_summary,
                active_task_name=active_task_name,
                current_stage=current_stage,
                boundary_index=self._next_boundary_index(runtime_store),
            )
            boundaries = self._dict_list(runtime_store.get("boundaries"))
            boundaries.append(latest_boundary)
            runtime_store["boundaries"] = boundaries[-20:]
            runtime_store["latest_boundary"] = latest_boundary
            runtime_store["last_compacted_at"] = latest_boundary.get("created_at")

        runtime_store["last_metrics"] = metrics.to_state()
        runtime_store["triggered"] = triggered
        compaction = self._dict(mutable_state.get("compaction"))
        compaction["runtime"] = runtime_store
        mutable_state["compaction"] = compaction

        return {
            "triggered": triggered,
            "compacted": bool(latest_boundary),
            "boundary_marker": str(latest_boundary.get("boundary_marker") or ""),
            "compact_summary": str(latest_boundary.get("compact_summary") or ""),
            "retained_live_state": self._dict(latest_boundary.get("retained_live_state")),
            "compact_metadata": self._dict(latest_boundary.get("compact_metadata")),
            "latest_boundary": latest_boundary,
            "metrics": metrics.to_state(),
            "thresholds": thresholds.to_state(),
        }

    def _resolve_thresholds(self, mutable_state: dict[str, object]) -> CompactRuntimeThresholds:
        runtime_store = self._dict(self._dict(mutable_state.get("compaction")).get("runtime"))
        config = self._dict(runtime_store.get("config"))
        return CompactRuntimeThresholds(
            rough_token_threshold=self._int(
                config.get("rough_token_threshold"),
                default=self.DEFAULT_THRESHOLDS.rough_token_threshold,
            ),
            message_count_threshold=self._int(
                config.get("message_count_threshold"),
                default=self.DEFAULT_THRESHOLDS.message_count_threshold,
            ),
            execution_record_threshold=self._int(
                config.get("execution_record_threshold"),
                default=self.DEFAULT_THRESHOLDS.execution_record_threshold,
            ),
        )

    def _build_metrics(
        self,
        *,
        mutable_state: dict[str, object],
        retrieval_summary: str,
        memory_summary: str,
        history_summary: str,
        projection: ContextProjection,
    ) -> CompactRuntimeMetrics:
        messages = self._dict_list(mutable_state.get("messages"))
        archived_messages = self._dict_list(mutable_state.get("archived_messages"))
        execution_records = self._dict_list(mutable_state.get("execution_records"))
        archived_execution_records = self._dict_list(
            mutable_state.get("archived_execution_records")
        )
        rough_text = "\n\n".join(
            part
            for part in [
                self._message_text(messages + archived_messages),
                self._execution_text(execution_records + archived_execution_records),
                retrieval_summary,
                memory_summary,
                history_summary,
                projection.summary,
            ]
            if part
        )
        return CompactRuntimeMetrics(
            rough_token_estimate=estimate_token_count(rough_text),
            message_count=len(messages) + len(archived_messages),
            execution_record_count=len(execution_records) + len(archived_execution_records),
        )

    @staticmethod
    def _should_compact(
        metrics: CompactRuntimeMetrics, thresholds: CompactRuntimeThresholds
    ) -> bool:
        return any(
            [
                metrics.rough_token_estimate >= thresholds.rough_token_threshold,
                metrics.message_count >= thresholds.message_count_threshold,
                metrics.execution_record_count >= thresholds.execution_record_threshold,
            ]
        )

    def _should_create_boundary(
        self,
        latest_boundary: dict[str, object],
        metrics: CompactRuntimeMetrics,
    ) -> bool:
        latest_metadata = self._dict(latest_boundary.get("compact_metadata"))
        latest_metrics = self._dict(latest_metadata.get("metrics"))
        return latest_metrics != metrics.to_state()

    def _build_boundary(
        self,
        *,
        mutable_state: dict[str, object],
        metrics: CompactRuntimeMetrics,
        thresholds: CompactRuntimeThresholds,
        projection: ContextProjection,
        retrieval_summary: str,
        memory_summary: str,
        history_summary: str,
        active_task_name: str,
        current_stage: str | None,
        boundary_index: int,
    ) -> dict[str, object]:
        created_at = datetime.now(UTC).isoformat()
        boundary_marker = f"compact-boundary:{boundary_index}"
        recent_messages = self._recent_message_previews(mutable_state)
        recent_records = self._recent_execution_previews(mutable_state)
        compact_summary = "\n".join(
            [
                f"Boundary marker: {boundary_marker}",
                f"Current stage: {current_stage or 'unknown'} | Current task: {active_task_name}",
                f"Projection summary: {projection.summary}",
                f"Retrieval summary: {retrieval_summary}",
                f"Memory summary: {memory_summary}",
                f"History summary: {history_summary}",
            ]
        )
        retained_live_state = {
            "current_stage": current_stage,
            "current_task": active_task_name,
            "projection_active_level": projection.active_level,
            "recent_messages": recent_messages,
            "recent_execution_records": recent_records,
        }
        compact_metadata = {
            "created_at": created_at,
            "metrics": metrics.to_state(),
            "thresholds": thresholds.to_state(),
            "trigger_reason": self._trigger_reason(metrics, thresholds),
        }
        return {
            "boundary_marker": boundary_marker,
            "compact_summary": compact_summary,
            "retained_live_state": retained_live_state,
            "compact_metadata": compact_metadata,
            "created_at": created_at,
        }

    def _ensure_runtime_store(
        self,
        mutable_state: dict[str, object],
        thresholds: CompactRuntimeThresholds,
    ) -> dict[str, object]:
        compaction = self._dict(mutable_state.get("compaction"))
        runtime_store = self._dict(compaction.get("runtime"))
        runtime_store.setdefault("boundaries", [])
        runtime_store["config"] = thresholds.to_state()
        compaction["runtime"] = runtime_store
        mutable_state["compaction"] = compaction
        return runtime_store

    @staticmethod
    def _next_boundary_index(runtime_store: dict[str, object]) -> int:
        return len(CompactRuntimeService._dict_list(runtime_store.get("boundaries"))) + 1

    @staticmethod
    def _trigger_reason(
        metrics: CompactRuntimeMetrics,
        thresholds: CompactRuntimeThresholds,
    ) -> list[str]:
        reasons: list[str] = []
        if metrics.rough_token_estimate >= thresholds.rough_token_threshold:
            reasons.append("rough_token_threshold")
        if metrics.message_count >= thresholds.message_count_threshold:
            reasons.append("message_count_threshold")
        if metrics.execution_record_count >= thresholds.execution_record_threshold:
            reasons.append("execution_record_threshold")
        return reasons

    @staticmethod
    def _message_text(items: list[dict[str, object]]) -> str:
        return "\n".join(
            str(item.get("content") or "") for item in items if isinstance(item.get("content"), str)
        )

    @staticmethod
    def _execution_text(items: list[dict[str, object]]) -> str:
        return "\n".join(
            (
                f"{item.get('task_name') or item.get('task_node_id') or 'unknown'}: "
                f"{item.get('summary') or item.get('status') or ''}"
            )
            for item in items
            if isinstance(item, dict)
        )

    def _recent_message_previews(self, mutable_state: dict[str, object]) -> list[dict[str, object]]:
        messages = self._dict_list(mutable_state.get("messages"))
        return [
            {
                "role": str(item.get("role") or "unknown"),
                "content_preview": str(item.get("content") or "")[:160],
            }
            for item in messages[-3:]
        ]

    def _recent_execution_previews(
        self, mutable_state: dict[str, object]
    ) -> list[dict[str, object]]:
        records = self._dict_list(mutable_state.get("execution_records"))
        return [
            {
                "task_name": str(item.get("task_name") or item.get("task_node_id") or "unknown"),
                "status": str(item.get("status") or "unknown"),
                "summary": str(item.get("summary") or ""),
            }
            for item in records[-3:]
        ]

    @staticmethod
    def _dict(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items()}

    @staticmethod
    def _dict_list(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _int(value: object, *, default: int) -> int:
        return value if isinstance(value, int) else default
