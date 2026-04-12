from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import blake2b
from typing import Any, cast

from app.compat.mcp.service import MCPService
from app.compat.skills.service import SkillService, SkillServiceError
from app.db.models import (
    MCPCapabilityKind,
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
    MCP_TOOL_ALIAS_MAX_LENGTH = 64

    _MCP_ALIAS_COMPONENT_RE = re.compile(r"[^a-z0-9]+")

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

    def build_skill_snapshot(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, object]]:
        payload = self._skill_service.build_skill_context_payload(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
        )
        skills = payload.get("prepared_selected_skills") or payload.get("skills")
        return skills if isinstance(skills, list) else []

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
                "capabilities": [
                    {
                        "kind": capability.kind.value,
                        "name": capability.name,
                        "title": capability.title,
                        "description": capability.description,
                        "uri": capability.uri,
                    }
                    for capability in server.capabilities
                ],
            }
            for server in self.list_mcp_servers()
            if server.enabled
        ]

    def build_mcp_tool_inventory(self) -> list[dict[str, object]]:
        inventory: list[dict[str, object]] = []
        seen_aliases: set[str] = set()
        for server in self.list_mcp_servers():
            if not server.enabled:
                continue
            for capability in server.capabilities:
                if capability.kind != MCPCapabilityKind.TOOL:
                    continue
                tool_alias = self._build_mcp_tool_alias(
                    server_id=server.id,
                    server_name=server.name,
                    tool_name=capability.name,
                    seen_aliases=seen_aliases,
                )
                inventory.append(
                    {
                        "tool_alias": tool_alias,
                        "server_id": server.id,
                        "server_name": server.name,
                        "source": server.source.value,
                        "scope": server.scope.value,
                        "transport": server.transport.value,
                        "tool_name": capability.name,
                        "tool_title": capability.title,
                        "tool_description": capability.description,
                        "input_schema": self._normalize_mcp_input_schema(capability.input_schema),
                    }
                )
        return inventory

    def build_snapshot(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
    ) -> dict[str, object]:
        cache_allowed = use_cache and not touched_paths and workspace_path is None
        if cache_allowed:
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

        skill_context = self.build_skill_context(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
        )
        skills = skill_context.get("prepared_selected_skills") or skill_context.get("skills")
        selected_skill = skill_context.get("selected_skill")
        primary_skill = skill_context.get("primary_skill")
        snapshot: dict[str, object] = {
            "skills": skills if isinstance(skills, list) else [],
            "selected_skill": selected_skill if isinstance(selected_skill, dict) else None,
            "selected_skill_id": skill_context.get("selected_skill_id"),
            "primary_skill": primary_skill if isinstance(primary_skill, dict) else None,
            "supporting_skills": skill_context.get("supporting_skills", []),
            "selected_skills": skill_context.get("selected_skills", []),
            "selected_skill_ids": skill_context.get("selected_skill_ids", []),
            "skill_budget": skill_context.get("skill_budget", {}),
            "skill_set_plan": skill_context.get("skill_set_plan", {}),
            "intent_profile": skill_context.get("intent_profile"),
            "prepared_selected_skills": skill_context.get("prepared_selected_skills", []),
            "prepared_supporting_skills": skill_context.get("prepared_supporting_skills", []),
            "primary_prepared": skill_context.get("primary_prepared"),
            "suppressed_skills": skill_context.get("suppressed_skills", []),
            "suppression_reasons": skill_context.get("suppression_reasons", {}),
            "pruning_applied": (
                bool(
                    cast(dict[str, object], skill_context.get("skill_set_plan", {})).get(
                        "pruning_applied", False
                    )
                )
                if isinstance(skill_context.get("skill_set_plan"), dict)
                else False
            ),
            "pruned_supporting_skills": skill_context.get("pruned_supporting_skills", []),
            "pruned_reference_skills": skill_context.get("pruned_reference_skills", []),
            "skill_runtime_usage": skill_context.get("skill_runtime_usage", []),
            "reference_skills": skill_context.get("reference_skills", []),
            "rejected_skills": skill_context.get("rejected_skills", []),
            "mcp_servers": self.build_mcp_snapshot(),
        }
        if cache_allowed:
            self._save_cached_snapshot(snapshot, session_id=session_id)
        self._log_capability_event(
            event_type="capability.snapshot.refresh",
            message="Built fresh capability snapshot.",
            payload={
                "skill_count": len(cast(list[dict[str, object]], snapshot["skills"])),
                "selected_skill_id": snapshot.get("selected_skill_id"),
                "pruning_applied": snapshot.get("pruning_applied", False),
                "mcp_server_count": len(cast(list[dict[str, object]], snapshot["mcp_servers"])),
            },
            session_id=session_id,
        )
        return snapshot

    def build_skill_context(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
    ) -> dict[str, object]:
        try:
            payload = self._skill_service.build_skill_context_payload(
                touched_paths=touched_paths,
                workspace_path=workspace_path,
                session_id=session_id,
                user_goal=user_goal,
                current_prompt=current_prompt,
                scenario_type=scenario_type,
                agent_role=agent_role,
                workflow_stage=workflow_stage,
            )
        except SkillServiceError as exc:
            try:
                fallback_skills = self._skill_service.list_ranked_skill_candidates(
                    touched_paths=touched_paths,
                    workspace_path=workspace_path,
                    session_id=session_id,
                    user_goal=user_goal,
                    current_prompt=current_prompt,
                    scenario_type=scenario_type,
                    agent_role=agent_role,
                    workflow_stage=workflow_stage,
                    include_reference_only=True,
                )
            except SkillServiceError:
                fallback_skills = []

            payload = {
                "skills": fallback_skills,
                "selected_skills": fallback_skills,
                "prepared_selected_skills": fallback_skills,
                "primary_skill": None,
                "selected_skill": None,
                "selected_skill_id": None,
                "selected_skill_ids": [],
                "supporting_skills": [],
                "reference_skills": [],
                "rejected_skills": [],
                "skill_budget": {},
                "skill_set_plan": {},
                "skill_runtime_usage": [],
                "suppressed_skills": [],
                "suppression_reasons": {},
                "prepared_supporting_skills": [],
                "prepared_primary_skill": None,
                "prepared_context_prompt": "",
                "degraded_reason": str(exc),
            }
        skills = payload.get("prepared_selected_skills") or payload.get("skills")
        self._log_capability_event(
            event_type="capability.skills.context",
            message="Built structured skill context payload.",
            payload={
                "skill_count": len(skills) if isinstance(skills, list) else 0,
                "selected_skill_id": payload.get("selected_skill_id"),
            },
        )
        return payload

    def build_skill_inventory_summary(
        self,
        *,
        use_cache: bool = True,
        max_cache_age_seconds: int = 120,
        session_id: str | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
    ) -> str:
        context_sensitive = any(
            part for part in (user_goal, current_prompt, scenario_type, agent_role, workflow_stage)
        )
        if use_cache and not context_sensitive:
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

        payload = self.build_skill_context(
            session_id=session_id,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
        )
        skills = payload.get("prepared_selected_skills") or payload.get("skills")
        mcp_servers = self.build_mcp_snapshot()
        if (not isinstance(skills, list) or not skills) and not mcp_servers:
            summary = "No loaded skills or enabled MCP servers are currently available."
        else:
            lines: list[str] = []
            if isinstance(skills, list) and skills:
                lines.append("Loaded skills inventory:")
            else:
                lines.append("Loaded skills inventory: none")
            skill_set_plan = payload.get("skill_set_plan")
            skill_budget = payload.get("skill_budget")
            if isinstance(skill_set_plan, dict):
                lines.extend(["Skill set plan for this stage:"])
                if isinstance(skill_budget, dict):
                    lines.append(
                        "- budget: "
                        f"primary={skill_budget.get('max_primary', 1)} "
                        f"supporting={skill_budget.get('max_supporting', 0)} "
                        f"reference={skill_budget.get('max_reference', 0)}"
                    )
                notes = skill_set_plan.get("notes", [])
                if isinstance(notes, list):
                    for note in notes:
                        if isinstance(note, str) and note.strip():
                            lines.append(f"- {note}")
                intent_profile = payload.get("intent_profile")
                if isinstance(intent_profile, dict):
                    lines.append(
                        "- intent: "
                        f"domain={intent_profile.get('dominant_domain')} "
                        f"ctf={intent_profile.get('is_ctf')} "
                        f"remote_service={intent_profile.get('is_remote_service')} "
                        f"http_target={intent_profile.get('is_http_target')}"
                    )
                lines.append("")
            if isinstance(skills, list):
                for item in skills:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("directory_name") or item.get("name") or "unknown")
                    description = str(item.get("description") or "No description provided.")
                    invocable = str(item.get("invocable", False)).lower()
                    total_score = item.get("total_score")
                    reasons = item.get("reasons")
                    selected = bool(item.get("selected"))
                    conditional_suffix = ""
                    if bool(item.get("conditional")):
                        conditional_suffix = f" | paths={item.get('paths') or []}"
                        if bool(item.get("active_due_to_touched_paths")):
                            conditional_suffix += " | active=touched_paths"
                    prepared_invocation = item.get("prepared_invocation")
                    prepared_suffix = ""
                    if isinstance(prepared_invocation, dict) and prepared_invocation:
                        shell_expansion_count = prepared_invocation.get("shell_expansion_count", 0)
                        pending_action_count = prepared_invocation.get("pending_action_count", 0)
                        prepared_suffix = (
                            " | prepared="
                            f"shell_expansions={shell_expansion_count},"
                            f"pending_actions={pending_action_count}"
                        )
                    lines.append(
                        f"- {label}: {description} | score={total_score} "
                        f"| selected={str(selected).lower()} "
                        f"| role={item.get('role') or 'unassigned'} | invocable={invocable}"
                        f"{conditional_suffix}{prepared_suffix}"
                    )
                    if isinstance(reasons, list) and reasons:
                        lines.append(f"  why: {'; '.join(str(reason) for reason in reasons[:3])}")
            suppressed_skills = payload.get("suppressed_skills")
            suppression_reasons = payload.get("suppression_reasons")
            if isinstance(suppressed_skills, list) and suppressed_skills:
                lines.extend(["", "Suppressed skills:"])
                for item in suppressed_skills:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("directory_name") or item.get("name") or "unknown")
                    raw_reason = None
                    if isinstance(suppression_reasons, dict):
                        raw_reason = suppression_reasons.get(label) or suppression_reasons.get(
                            item.get("id")
                        )
                    if isinstance(raw_reason, list):
                        reason_text = "; ".join(str(reason) for reason in raw_reason)
                    elif isinstance(raw_reason, str) and raw_reason.strip():
                        reason_text = raw_reason
                    else:
                        reason_text = str(item.get("rejected_reason") or "suppressed_by_intent")
                    lines.append(f"- {label}: {reason_text}")
            pruned_items: list[str] = []
            pruned_supporting_skills = payload.get("pruned_supporting_skills")
            if isinstance(pruned_supporting_skills, list):
                pruned_items.extend(
                    str(item.get("directory_name") or item.get("name") or "unknown")
                    for item in pruned_supporting_skills
                    if isinstance(item, dict)
                )
            pruned_reference_skills = payload.get("pruned_reference_skills")
            if isinstance(pruned_reference_skills, list):
                pruned_items.extend(
                    str(item.get("directory_name") or item.get("name") or "unknown")
                    for item in pruned_reference_skills
                    if isinstance(item, dict)
                )
            if pruned_items:
                lines.extend(["", "Related skills pruned for context budget:"])
                lines.append(f"- {', '.join(pruned_items)}")
            reference_skills = payload.get("reference_skills")
            if isinstance(reference_skills, list) and reference_skills:
                lines.extend(["", "Reference-only ranked skills:"])
                for item in reference_skills:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("directory_name") or item.get("name") or "unknown")
                    reasons = cast(list[object], item.get("reasons", []))
                    lines.append(
                        f"- {label}: score={item.get('total_score')} | invocable=false "
                        f"| why={' ; '.join(str(reason) for reason in reasons[:2])}"
                    )
            if mcp_servers:
                lines.extend(["", "Enabled MCP capability inventory:"])
                tool_inventory = self.build_mcp_tool_inventory()
                if tool_inventory:
                    lines.append("Callable MCP tools:")
                    for item in tool_inventory:
                        alias = str(item.get("tool_alias") or "unknown")
                        server_name = str(item.get("server_name") or "unknown")
                        tool_name = str(item.get("tool_name") or "unknown")
                        description = str(
                            item.get("tool_description")
                            or item.get("tool_title")
                            or "No description provided."
                        )
                        lines.append(f"- {alias}: {server_name} / {tool_name} — {description}")
                resource_like_items: list[str] = []
                for server in mcp_servers:
                    server_name = str(server.get("name") or "unknown")
                    raw_capabilities = server.get("capabilities", [])
                    if not isinstance(raw_capabilities, list):
                        continue
                    for capability in raw_capabilities:
                        if not isinstance(capability, dict):
                            continue
                        capability_kind = str(capability.get("kind") or "unknown")
                        if capability_kind == MCPCapabilityKind.TOOL.value:
                            continue
                        capability_name = str(
                            capability.get("name") or capability.get("uri") or "unknown"
                        )
                        resource_like_items.append(
                            f"- {server_name}: {capability_kind} / {capability_name}"
                        )
                if resource_like_items:
                    lines.append("Non-callable MCP resources/prompts/templates (visible only):")
                    lines.extend(resource_like_items)
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
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
    ) -> str:
        context_sensitive = any(
            part for part in (user_goal, current_prompt, scenario_type, agent_role, workflow_stage)
        )
        if use_cache and not context_sensitive:
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

        payload = self.build_skill_context(
            session_id=session_id,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
        )
        skills = payload.get("prepared_selected_skills") or payload.get("skills")
        mcp_tool_inventory = self.build_mcp_tool_inventory()
        if (not isinstance(skills, list) or not skills) and not mcp_tool_inventory:
            summary = "No loaded skill or MCP tool parameter schemas are currently available."
        else:
            lines: list[str] = []
            if isinstance(skills, list) and skills:
                lines.append("Loaded skill parameter schemas:")
            else:
                lines.append("Loaded skill parameter schemas: none")
            if isinstance(skills, list):
                for item in skills:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("directory_name") or item.get("name") or "unknown")
                    parameter_schema = item.get("parameter_schema")
                    total_score = item.get("total_score")
                    if isinstance(parameter_schema, dict) and parameter_schema:
                        lines.append(f"- {label}: score={total_score} | {parameter_schema}")
                    else:
                        lines.append(f"- {label}: score={total_score} | no parameters")
                    prepared_invocation = item.get("prepared_invocation")
                    if isinstance(prepared_invocation, dict) and prepared_invocation:
                        lines.append(
                            f"  prepared_invocation: request={prepared_invocation.get('request')}"
                        )
            if mcp_tool_inventory:
                lines.extend(["", "Callable MCP tool input schemas:"])
                for item in mcp_tool_inventory:
                    alias = str(item.get("tool_alias") or "unknown")
                    lines.append(f"- {alias}: {item.get('input_schema')}")
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
            user_goal=task_description,
            current_prompt=projection_summary,
            scenario_type=task_name,
            agent_role=role_prompt,
            workflow_stage=sub_agent_role_prompt,
        )
        schema_summary = self.build_skill_schema_summary(
            use_cache=use_cache,
            max_cache_age_seconds=max_cache_age_seconds,
            session_id=session_id,
            user_goal=task_description,
            current_prompt=projection_summary,
            scenario_type=task_name,
            agent_role=role_prompt,
            workflow_stage=sub_agent_role_prompt,
        )
        prompt_fragment = "\n\n".join(
            [
                inventory_summary,
                schema_summary,
                (
                    "Never call a skill slug or skill name directly as a tool. Skills now expose "
                    "ranked compiled metadata, selection rationale, and prepared invocation "
                    "hints, but execution still flows "
                    "through the existing runtime approval and tool pipeline. Use execute_skill "
                    "when you need the server-side facade to resolve a specific skill and prepare "
                    "its invocation metadata, and use read_skill_content with the skill slug, "
                    "name, or id when you only need SKILL.md content. Callable tools always "
                    "include the fixed tools execute_kali_command, list_available_skills, "
                    "execute_skill, read_skill_content, create_terminal_session, "
                    "list_terminal_sessions, execute_terminal_command, read_terminal_buffer, "
                    "and stop_terminal_job, plus any MCP tool aliases listed above in the "
                    "format mcp__{server}__{tool}. MCP resources, prompts, and templates are "
                    "visible for context but are not callable tools."
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
                user_goal=task_description,
                current_prompt=projection_summary,
                scenario_type=task_name,
                agent_role=role_prompt,
                workflow_stage=sub_agent_role_prompt,
            ),
            "schema_summary": self.build_skill_schema_summary(
                use_cache=use_cache,
                max_cache_age_seconds=max_cache_age_seconds,
                session_id=session_id,
                user_goal=task_description,
                current_prompt=projection_summary,
                scenario_type=task_name,
                agent_role=role_prompt,
                workflow_stage=sub_agent_role_prompt,
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

    @classmethod
    def _slugify_alias_component(cls, value: str, *, fallback: str) -> str:
        normalized = cls._MCP_ALIAS_COMPONENT_RE.sub("_", value.casefold()).strip("_")
        return normalized or fallback

    @classmethod
    def _build_mcp_tool_alias(
        cls,
        *,
        server_id: str,
        server_name: str,
        tool_name: str,
        seen_aliases: set[str],
    ) -> str:
        server_slug = cls._slugify_alias_component(server_name, fallback="server")
        tool_slug = cls._slugify_alias_component(tool_name, fallback="tool")
        base_alias = f"mcp__{server_slug}__{tool_slug}"
        unique_key = f"{server_id}:{tool_name}".encode()
        suffix = f"__{blake2b(unique_key, digest_size=4).hexdigest()}"
        alias = base_alias
        if len(alias) > cls.MCP_TOOL_ALIAS_MAX_LENGTH:
            alias = f"{base_alias[: cls.MCP_TOOL_ALIAS_MAX_LENGTH - len(suffix)]}{suffix}".rstrip(
                "_"
            )
        if alias in seen_aliases:
            trimmed = base_alias[: cls.MCP_TOOL_ALIAS_MAX_LENGTH - len(suffix)].rstrip("_")
            alias = f"{trimmed}{suffix}"
        seen_aliases.add(alias)
        return alias

    @staticmethod
    def _normalize_mcp_input_schema(schema: object) -> dict[str, object]:
        fallback_schema: dict[str, object] = {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
        if not isinstance(schema, dict):
            return fallback_schema

        schema_type = schema.get("type")
        if schema_type != "object":
            return fallback_schema

        normalized_schema = dict(schema)
        properties = normalized_schema.get("properties")
        normalized_schema["properties"] = properties if isinstance(properties, dict) else {}
        required = normalized_schema.get("required")
        if required is not None and not isinstance(required, list):
            normalized_schema.pop("required", None)
        if "additionalProperties" not in normalized_schema:
            normalized_schema["additionalProperties"] = True
        return normalized_schema
