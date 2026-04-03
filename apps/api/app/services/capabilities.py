from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.compat.mcp.service import MCPService
from app.compat.skills.service import SkillService
from app.db.models import (
    MCPServerRead,
    RuntimeExecuteRequest,
    RuntimeExecutionRunRead,
    RuntimePolicy,
    SkillRecordRead,
)
from app.db.repositories import RunLogRepository
from app.prompt import CAPABILITY_LAYER, PromptFragmentBuilder


class CapabilityFacade:
    INVENTORY_SUMMARY_CACHE_EVENT = "capability.skills.inventory_summary.cache"
    INVENTORY_SUMMARY_CACHE_HIT_EVENT = "capability.skills.inventory_summary.cache_hit"
    SCHEMA_SUMMARY_CACHE_EVENT = "capability.skills.schema_summary.cache"
    SCHEMA_SUMMARY_CACHE_HIT_EVENT = "capability.skills.schema_summary.cache_hit"
    PROMPT_FRAGMENT_CACHE_EVENT = "capability.skills.prompt_fragment.cache"
    PROMPT_FRAGMENT_CACHE_HIT_EVENT = "capability.skills.prompt_fragment.cache_hit"

    def __init__(
        self,
        *,
        skill_service: SkillService,
        mcp_service: MCPService,
        runtime_runner: (
            Callable[[RuntimeExecuteRequest, RuntimePolicy | None], RuntimeExecutionRunRead] | None
        ) = None,
        run_log_repository: RunLogRepository | None = None,
    ) -> None:
        self._skill_service = skill_service
        self._mcp_service = mcp_service
        self._runtime_runner = runtime_runner
        self._run_log_repository = run_log_repository
        self._prompt_fragment_builder = PromptFragmentBuilder()

    def list_skills(self) -> list[SkillRecordRead]:
        records = self._skill_service.list_skills()
        self._log_capability_event(
            event_type="capability.skills.list",
            message="Listed skills from capability facade.",
            payload={"count": len(records)},
        )
        return records

    def get_skill(self, skill_id: str) -> SkillRecordRead | None:
        record = self._skill_service.get_skill(skill_id)
        self._log_capability_event(
            event_type="capability.skills.get",
            message=f"Fetched skill '{skill_id}'.",
            payload={"skill_id": skill_id, "found": record is not None},
        )
        return record

    def list_mcp_servers(self) -> list[MCPServerRead]:
        servers = self._mcp_service.list_servers()
        self._log_capability_event(
            event_type="capability.mcp.list",
            message="Listed MCP servers from capability facade.",
            payload={"count": len(servers)},
        )
        return servers

    async def call_mcp_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object] | None:
        result = await self._mcp_service.call_tool(server_id, tool_name, arguments)
        self._log_capability_event(
            event_type="capability.mcp.call_tool",
            message=f"Called MCP tool '{tool_name}' on server '{server_id}'.",
            payload={
                "server_id": server_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result_present": result is not None,
            },
        )
        return result

    def run_command(
        self,
        payload: RuntimeExecuteRequest,
        runtime_policy: RuntimePolicy | None = None,
    ) -> RuntimeExecutionRunRead:
        if self._runtime_runner is None:
            raise RuntimeError("Runtime command execution is not configured for CapabilityFacade.")
        run = self._runtime_runner(payload, runtime_policy)
        self._log_capability_event(
            event_type="capability.runtime.run_command",
            message="Executed runtime command via capability facade.",
            payload={
                "run_id": run.id,
                "session_id": run.session_id,
                "status": run.status.value,
                "exit_code": run.exit_code,
                "artifact_count": len(run.artifacts),
            },
            session_id=run.session_id,
            run_id=run.id,
        )
        return run

    def build_skill_snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "id": record.id,
                "name": record.name,
                "source": record.source.value,
                "scope": record.scope.value,
                "status": record.status.value,
                "enabled": record.enabled,
                "compatibility": list(record.compatibility),
                "parameter_schema": dict(record.parameter_schema),
            }
            for record in self.list_skills()
            if record.enabled
        ]

    def build_mcp_snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "id": server.id,
                "name": server.name,
                "source": server.source.value,
                "scope": server.scope.value,
                "transport": server.transport.value,
                "status": server.status.value,
                "enabled": server.enabled,
                "health": {
                    "status": server.health_status,
                    "latency_ms": server.health_latency_ms,
                    "error": server.health_error,
                    "checked_at": (
                        server.health_checked_at.isoformat()
                        if server.health_checked_at is not None
                        else None
                    ),
                },
                "capability_count": len(server.capabilities),
            }
            for server in self.list_mcp_servers()
            if server.enabled
        ]

    def build_snapshot(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
    ) -> dict[str, object]:
        if use_cache:
            cached = self._load_cached_snapshot(
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
            )
            if cached is not None:
                self._log_capability_event(
                    event_type="capability.snapshot.cache_hit",
                    message="Returned cached capability snapshot.",
                    payload={"max_cache_age_seconds": max_cache_age_seconds},
                    session_id=session_id,
                )
                return cached

        snapshot: dict[str, object] = {
            "skills": self.build_skill_snapshot(),
            "mcp_servers": self.build_mcp_snapshot(),
        }
        self._save_cached_snapshot(snapshot, session_id=session_id)
        self._log_capability_event(
            event_type="capability.snapshot.refresh",
            message="Built fresh capability snapshot.",
            payload={
                "skill_count": len(cast(list[dict[str, object]], snapshot["skills"])),
                "mcp_server_count": len(cast(list[dict[str, object]], snapshot["mcp_servers"])),
            },
            session_id=session_id,
        )
        return snapshot

    def build_skill_context(self) -> dict[str, object]:
        payload = self._skill_service.build_skill_context_payload()
        skills = payload.get("skills")
        self._log_capability_event(
            event_type="capability.skills.context",
            message="Built structured skill context payload.",
            payload={"skill_count": len(skills) if isinstance(skills, list) else 0},
        )
        return payload

    def build_skill_inventory_summary(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
    ) -> str:
        if use_cache:
            cached = self._load_cached_fragment(
                event_type=self.INVENTORY_SUMMARY_CACHE_EVENT,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
            )
            if cached is not None:
                self._log_capability_event(
                    event_type=self.INVENTORY_SUMMARY_CACHE_HIT_EVENT,
                    message="Returned cached capability inventory summary.",
                    payload={"max_cache_age_seconds": max_cache_age_seconds},
                    session_id=session_id,
                )
                return cached

        payload = self.build_skill_context()
        skills = payload.get("skills")
        if not isinstance(skills, list) or not skills:
            summary = "No loaded skills are currently available."
        else:
            lines = ["Loaded skills inventory:"]
            for item in skills:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("directory_name") or item.get("name") or "unknown")
                description = str(item.get("description") or "No description provided.")
                lines.append(f"- {label}: {description}")
            summary = "\n".join(lines)
        self._save_cached_fragment(
            event_type=self.INVENTORY_SUMMARY_CACHE_EVENT,
            content=summary,
            session_id=session_id,
        )
        return summary

    def build_skill_schema_summary(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
    ) -> str:
        if use_cache:
            cached = self._load_cached_fragment(
                event_type=self.SCHEMA_SUMMARY_CACHE_EVENT,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
            )
            if cached is not None:
                self._log_capability_event(
                    event_type=self.SCHEMA_SUMMARY_CACHE_HIT_EVENT,
                    message="Returned cached capability schema summary.",
                    payload={"max_cache_age_seconds": max_cache_age_seconds},
                    session_id=session_id,
                )
                return cached

        payload = self.build_skill_context()
        skills = payload.get("skills")
        if not isinstance(skills, list) or not skills:
            summary = "No loaded skill parameter schemas are currently available."
        else:
            lines = ["Loaded skill parameter schemas:"]
            for item in skills:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("directory_name") or item.get("name") or "unknown")
                parameter_schema = item.get("parameter_schema")
                if isinstance(parameter_schema, dict) and parameter_schema:
                    lines.append(f"- {label}: {parameter_schema}")
                else:
                    lines.append(f"- {label}: no parameters")
            summary = "\n".join(lines)
        self._save_cached_fragment(
            event_type=self.SCHEMA_SUMMARY_CACHE_EVENT,
            content=summary,
            session_id=session_id,
        )
        return summary

    def build_skill_prompt_fragment(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
        role_prompt: str | None = None,
        sub_agent_role_prompt: str | None = None,
        task_name: str | None = None,
        task_description: str | None = None,
        projection_summary: str | None = None,
    ) -> str:
        role_text = "\n".join(
            part
            for part in ((role_prompt or "").strip(), (sub_agent_role_prompt or "").strip())
            if part
        )
        task_local_text = (
            f"Task: {task_name or 'workflow-context'}. "
            f"Description: {task_description or 'N/A'}. "
            f"Projection summary: {projection_summary or 'N/A'}."
        )
        cache_bundle = self._prompt_fragment_builder.build_by_role_and_task(
            core_text="",
            role_text=role_text,
            capability_text="",
            task_local_text=task_local_text,
            session_id=session_id,
            role=role_prompt,
            task_name=task_name,
        )
        cache_key = cache_bundle.capability.cache_key
        if use_cache:
            cached = self._load_cached_fragment(
                event_type=self.PROMPT_FRAGMENT_CACHE_EVENT,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
                cache_key=cache_key,
            )
            if cached is not None:
                self._log_capability_event(
                    event_type=self.PROMPT_FRAGMENT_CACHE_HIT_EVENT,
                    message="Returned cached skill prompt fragment.",
                    payload={"max_cache_age_seconds": max_cache_age_seconds},
                    session_id=session_id,
                )
                return cached

        inventory_summary = self.build_skill_inventory_summary(
            use_cache=use_cache,
            max_cache_age_seconds=max_cache_age_seconds,
            session_id=session_id,
        )
        schema_summary = self.build_skill_schema_summary(
            use_cache=use_cache,
            max_cache_age_seconds=max_cache_age_seconds,
            session_id=session_id,
        )
        prompt_fragment = "\n\n".join(
            [
                inventory_summary,
                schema_summary,
                (
                    "Never call a skill slug or skill name directly as a tool. The only callable "
                    "tool names are execute_kali_command, list_available_skills, and "
                    "read_skill_content. If you need a skill, call read_skill_content with the "
                    "skill slug, name, or id."
                ),
            ]
        )
        prompt_bundle = self._prompt_fragment_builder.build_by_role_and_task(
            core_text="",
            role_text=role_text,
            capability_text=prompt_fragment,
            task_local_text=task_local_text,
            session_id=session_id,
            role=role_prompt,
            task_name=task_name,
        )
        self._log_capability_event(
            event_type="capability.skills.context_prompt",
            message="Built skill context prompt fragment.",
            payload={
                "length": len(prompt_bundle.capability.content),
                "layer": CAPABILITY_LAYER,
                "cache_key": prompt_bundle.capability.cache_key,
            },
            session_id=session_id,
        )
        self._save_cached_fragment(
            event_type=self.PROMPT_FRAGMENT_CACHE_EVENT,
            content=prompt_bundle.capability.content,
            session_id=session_id,
            cache_key=prompt_bundle.capability.cache_key,
        )
        return prompt_bundle.capability.content

    def build_prompt_fragments(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
        role_prompt: str | None = None,
        sub_agent_role_prompt: str | None = None,
        task_name: str | None = None,
        task_description: str | None = None,
        projection_summary: str | None = None,
    ) -> dict[str, str]:
        return {
            "inventory_summary": self.build_skill_inventory_summary(
                use_cache=use_cache,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
            ),
            "schema_summary": self.build_skill_schema_summary(
                use_cache=use_cache,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
            ),
            "prompt_fragment": self.build_skill_prompt_fragment(
                use_cache=use_cache,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
                role_prompt=role_prompt,
                sub_agent_role_prompt=sub_agent_role_prompt,
                task_name=task_name,
                task_description=task_description,
                projection_summary=projection_summary,
            ),
        }

    def _save_cached_snapshot(self, snapshot: dict[str, object], *, session_id: str | None) -> None:
        self._log_capability_event(
            event_type="capability.snapshot.cache",
            message="Persisted latest capability snapshot cache.",
            payload={"snapshot": snapshot},
            session_id=session_id,
        )

    def _load_cached_snapshot(
        self,
        *,
        max_cache_age_seconds: int,
        session_id: str | None,
    ) -> dict[str, object] | None:
        if self._run_log_repository is None:
            return None
        latest_log = self._run_log_repository.get_latest_log(
            source="capability_facade",
            event_type="capability.snapshot.cache",
            session_id=session_id,
        )
        if latest_log is None:
            return None
        created_at = latest_log.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        if created_at < datetime.now(UTC) - timedelta(seconds=max_cache_age_seconds):
            return None
        snapshot = latest_log.payload_json.get("snapshot")
        if isinstance(snapshot, dict):
            return snapshot
        return None

    def _save_cached_fragment(
        self,
        *,
        event_type: str,
        content: str,
        session_id: str | None,
        cache_key: str | None = None,
    ) -> None:
        self._log_capability_event(
            event_type=event_type,
            message=f"Persisted latest capability fragment cache for {event_type}.",
            payload={"content": content, "cache_key": cache_key},
            session_id=session_id,
        )

    def _load_cached_fragment(
        self,
        *,
        event_type: str,
        max_cache_age_seconds: int,
        session_id: str | None,
        cache_key: str | None = None,
    ) -> str | None:
        if self._run_log_repository is None:
            return None
        latest_log = self._run_log_repository.get_latest_log(
            source="capability_facade",
            event_type=event_type,
            session_id=session_id,
        )
        if latest_log is None:
            return None
        created_at = latest_log.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = created_at.astimezone(UTC)
        if created_at < datetime.now(UTC) - timedelta(seconds=max_cache_age_seconds):
            return None
        if cache_key is not None:
            payload_cache_key = latest_log.payload_json.get("cache_key")
            if payload_cache_key != cache_key:
                return None
        content = latest_log.payload_json.get("content")
        if isinstance(content, str):
            return content
        return None

    def _log_capability_event(
        self,
        *,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        if self._run_log_repository is None:
            return
        self._run_log_repository.create_log(
            session_id=session_id,
            project_id=None,
            run_id=run_id,
            level="info",
            source="capability_facade",
            event_type=event_type,
            message=message,
            payload=payload,
        )
