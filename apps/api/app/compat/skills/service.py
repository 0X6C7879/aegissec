from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.compat.skills import models as skill_models
from app.compat.skills.discovery_cache import (
    SkillDiscoveryCache,
    build_compiled_skill_cache_key,
    build_root_cache_key,
)
from app.compat.skills.executor import (
    execute_skill_orchestration_plan as execute_skill_orchestration_runtime,
)
from app.compat.skills.orchestration_planner import (
    build_skill_orchestration_plan as build_skill_orchestration_preview,
)
from app.compat.skills.parser import parse_skill_file, read_skill_markdown
from app.compat.skills.preflight import SkillPreflightCheck, can_auto_run_preflight
from app.compat.skills.scanner import (
    compatibility_skill_scan_placeholders,
    default_skill_scan_roots,
    discover_claude_skill_scan_roots,
    scan_skill_files,
)
from app.core.settings import Settings, get_settings
from app.db.models import (
    CompatibilitySource,
    SkillAgentSummaryRead,
    SkillContentRead,
    SkillRecord,
    SkillRecordRead,
    SkillRecordStatus,
    to_skill_record_read,
)
from app.db.repositories import MCPRepository, SkillRepository
from app.db.session import get_db_session


class SkillServiceError(Exception):
    pass


class SkillLookupError(SkillServiceError):
    pass


class SkillContentReadError(SkillServiceError):
    pass


@dataclass(slots=True)
class SkillBudget:
    workflow_stage: str | None
    agent_role: str | None
    max_primary: int = 1
    max_supporting: int = 1
    max_reference: int = 1

    def to_payload(self) -> dict[str, object]:
        return {
            "workflow_stage": self.workflow_stage,
            "agent_role": self.agent_role,
            "max_primary": self.max_primary,
            "max_supporting": self.max_supporting,
            "max_reference": self.max_reference,
        }


@dataclass(slots=True)
class SkillRuntimeUsageRecord:
    skill_id: str
    role: Literal[
        "primary", "supporting", "reference", "rejected", "pruned_supporting", "pruned_reference"
    ]
    loaded: bool
    surfaced_in_prompt: bool
    prepared_for_context: bool
    prepared_for_execution: bool
    used_by_agent: bool
    reason: str | None = None
    note: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "skill_id": self.skill_id,
            "role": self.role,
            "loaded": self.loaded,
            "surfaced_in_prompt": self.surfaced_in_prompt,
            "prepared_for_context": self.prepared_for_context,
            "prepared_for_execution": self.prepared_for_execution,
            "used_by_agent": self.used_by_agent,
            "reason": self.reason,
            "note": self.note,
        }


@dataclass(slots=True)
class SkillSetPlan:
    primary_candidate: skill_models.ResolvedSkillCandidate | None
    supporting_candidates: list[skill_models.ResolvedSkillCandidate] = field(default_factory=list)
    selected_candidates: list[skill_models.ResolvedSkillCandidate] = field(default_factory=list)
    reference_candidates: list[skill_models.ResolvedSkillCandidate] = field(default_factory=list)
    pruned_supporting_candidates: list[skill_models.ResolvedSkillCandidate] = field(
        default_factory=list
    )
    pruned_reference_candidates: list[skill_models.ResolvedSkillCandidate] = field(
        default_factory=list
    )
    workflow_stage: str | None = None
    agent_role: str | None = None
    selected_skill_ids: list[str] = field(default_factory=list)
    max_supporting: int = 1
    pruning_applied: bool = False
    notes: list[str] = field(default_factory=list)
    intent_profile: skill_models.SkillIntentProfile | None = None
    suppressed_candidates: list[skill_models.ResolvedSkillCandidate] = field(default_factory=list)

    def to_payload(
        self,
        *,
        payload_builder: Any,
        touched_paths: list[str] | None,
    ) -> dict[str, object]:
        return {
            "primary_skill": (
                None
                if self.primary_candidate is None
                else payload_builder(self.primary_candidate, touched_paths=touched_paths)
            ),
            "supporting_skills": [
                payload_builder(candidate, touched_paths=touched_paths)
                for candidate in self.supporting_candidates
            ],
            "selected_skills": [
                payload_builder(candidate, touched_paths=touched_paths)
                for candidate in self.selected_candidates
            ],
            "reference_skills": [
                payload_builder(candidate, touched_paths=touched_paths)
                for candidate in self.reference_candidates
            ],
            "pruned_supporting_skills": [
                payload_builder(candidate, touched_paths=touched_paths)
                for candidate in self.pruned_supporting_candidates
            ],
            "pruned_reference_skills": [
                payload_builder(candidate, touched_paths=touched_paths)
                for candidate in self.pruned_reference_candidates
            ],
            "workflow_stage": self.workflow_stage,
            "agent_role": self.agent_role,
            "selected_skill_ids": list(self.selected_skill_ids),
            "max_supporting": self.max_supporting,
            "pruning_applied": self.pruning_applied,
            "notes": list(self.notes),
            "intent_profile": (
                None if self.intent_profile is None else self.intent_profile.to_payload()
            ),
            "suppressed_skills": [
                payload_builder(candidate, touched_paths=touched_paths)
                for candidate in self.suppressed_candidates
            ],
        }


class _CompiledSkillRegistryProtocol(Protocol):
    def register(self, compiled_skill: skill_models.CompiledSkill) -> object: ...

    def get_by_token(self, token: str) -> skill_models.CompiledSkill | None: ...

    def list_unconditional_skills(self) -> list[skill_models.CompiledSkill]: ...

    def activate_for_touched_paths(
        self, touched_paths: list[str]
    ) -> list[skill_models.CompiledSkill]: ...


class SkillService:
    DEFAULT_SKILL_SHORTLIST_K = 5
    DEFAULT_AVAILABLE_TOOLS = (
        "execute_kali_command",
        "list_available_skills",
        "execute_skill",
        "read_skill_content",
        "call_mcp_tool",
    )

    def __init__(self, db_session: DBSession, settings: Settings) -> None:
        self._db_session = db_session
        self._repository = SkillRepository(db_session)
        self._mcp_repository = MCPRepository(db_session)
        self._settings = settings
        self._cache = SkillDiscoveryCache()

    def list_skills(self) -> list[SkillRecordRead]:
        return [
            self._build_skill_record_read(record) for record in self._list_visible_skill_records()
        ]

    def get_skill(self, skill_id: str) -> SkillRecordRead | None:
        record = self._get_visible_skill_record(skill_id)
        if record is None:
            return None
        return self._build_skill_record_read(record)

    def list_loaded_skills_for_agent(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
    ) -> list[SkillAgentSummaryRead]:
        payload = self.build_skill_context_payload(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
        )
        summaries: list[SkillAgentSummaryRead] = []
        skill_items = (
            payload.get("prepared_selected_skills") or payload.get("selected_skills") or []
        )
        if not skill_items:
            skill_items = self.list_ranked_skill_candidates(
                touched_paths=touched_paths,
                workspace_path=workspace_path,
                session_id=session_id,
                top_k=top_k,
                user_goal=user_goal,
                current_prompt=current_prompt,
                scenario_type=scenario_type,
                agent_role=agent_role,
                workflow_stage=workflow_stage,
                available_tools=available_tools,
                invocation_arguments=invocation_arguments,
            )
        if isinstance(skill_items, list):
            for item in skill_items:
                if isinstance(item, dict):
                    summaries.append(SkillAgentSummaryRead.model_validate(item))
        return summaries

    def determine_skill_budget(
        self,
        *,
        workflow_stage: str | None,
        agent_role: str | None,
    ) -> SkillBudget:
        normalized_stage = (workflow_stage or "").strip().casefold()
        normalized_role = (agent_role or "").strip().casefold()
        combined = " ".join(part for part in (normalized_stage, normalized_role) if part)

        if any(keyword in combined for keyword in ("deep", "analysis", "research", "audit")):
            return SkillBudget(
                workflow_stage=workflow_stage,
                agent_role=agent_role,
                max_supporting=3,
                max_reference=2,
            )
        if any(
            keyword in combined for keyword in ("execution", "validate", "validation", "execute")
        ):
            return SkillBudget(
                workflow_stage=workflow_stage,
                agent_role=agent_role,
                max_supporting=2,
                max_reference=2,
            )
        if any(keyword in combined for keyword in ("reflect", "summary", "summarize", "replan")):
            return SkillBudget(
                workflow_stage=workflow_stage,
                agent_role=agent_role,
                max_supporting=1,
                max_reference=1,
            )
        if any(
            keyword in combined for keyword in ("triage", "planning", "plan", "bootstrap", "scope")
        ):
            return SkillBudget(
                workflow_stage=workflow_stage,
                agent_role=agent_role,
                max_supporting=1,
                max_reference=1,
            )
        return SkillBudget(
            workflow_stage=workflow_stage,
            agent_role=agent_role,
            max_supporting=2,
            max_reference=1,
        )

    def build_skill_set_plan(
        self,
        resolution_result: skill_models.SkillResolutionResult,
        *,
        workflow_stage: str | None,
        agent_role: str | None,
    ) -> SkillSetPlan:
        skill_budget = self.determine_skill_budget(
            workflow_stage=workflow_stage,
            agent_role=agent_role,
        )
        supporting_candidates = list(
            resolution_result.supporting_candidates[: skill_budget.max_supporting]
        )
        pruned_supporting_candidates = list(
            resolution_result.supporting_candidates[skill_budget.max_supporting :]
        )
        reference_candidates = list(
            resolution_result.reference_candidates[: skill_budget.max_reference]
        )
        pruned_reference_candidates = list(
            resolution_result.reference_candidates[skill_budget.max_reference :]
        )
        selected_candidates = [
            candidate
            for candidate in [resolution_result.primary_candidate, *supporting_candidates]
            if candidate is not None
        ]
        suppressed_candidates = [
            candidate
            for candidate in resolution_result.rejected_candidates
            if candidate.rejected_reason == "suppressed_by_intent"
        ]
        selected_skill_ids = [
            candidate.compiled_skill.skill_id for candidate in selected_candidates
        ]
        pruning_applied = bool(pruned_supporting_candidates or pruned_reference_candidates)
        notes = [
            (
                "Stage budget: "
                f"primary={skill_budget.max_primary}, "
                f"supporting={skill_budget.max_supporting}, "
                f"reference={skill_budget.max_reference}."
            )
        ]
        if pruning_applied:
            notes.append(
                "Context budget pruning applied after selection packing so runtime only loads the "
                "stage-appropriate supporting/reference subset."
            )
        if resolution_result.intent_profile is not None:
            notes.append(
                "Intent profile: "
                f"domain={resolution_result.intent_profile.dominant_domain}, "
                f"dispatcher={str(resolution_result.intent_profile.prefers_dispatcher).lower()}, "
                f"remote_http={str(resolution_result.intent_profile.is_http_target).lower()}."
            )
        return SkillSetPlan(
            primary_candidate=resolution_result.primary_candidate,
            supporting_candidates=supporting_candidates,
            selected_candidates=selected_candidates,
            reference_candidates=reference_candidates,
            pruned_supporting_candidates=pruned_supporting_candidates,
            pruned_reference_candidates=pruned_reference_candidates,
            workflow_stage=workflow_stage,
            agent_role=agent_role,
            selected_skill_ids=selected_skill_ids,
            max_supporting=skill_budget.max_supporting,
            pruning_applied=pruning_applied,
            notes=notes,
            intent_profile=resolution_result.intent_profile,
            suppressed_candidates=suppressed_candidates,
        )

    def build_skill_runtime_usage_records(
        self,
        skill_set_plan: SkillSetPlan,
        *,
        resolution_result: skill_models.SkillResolutionResult,
    ) -> list[dict[str, object]]:
        usage_records: list[SkillRuntimeUsageRecord] = []

        def append_records(
            candidates: list[skill_models.ResolvedSkillCandidate],
            *,
            role: Literal[
                "primary",
                "supporting",
                "reference",
                "rejected",
                "pruned_supporting",
                "pruned_reference",
            ],
            loaded: bool,
            surfaced_in_prompt: bool,
            prepared_for_context: bool,
            prepared_for_execution: bool,
            note: str,
        ) -> None:
            for candidate in candidates:
                usage_records.append(
                    SkillRuntimeUsageRecord(
                        skill_id=candidate.compiled_skill.skill_id,
                        role=role,
                        loaded=loaded,
                        surfaced_in_prompt=surfaced_in_prompt,
                        prepared_for_context=prepared_for_context,
                        prepared_for_execution=prepared_for_execution,
                        used_by_agent=False,
                        reason=candidate.rejected_reason
                        or "; ".join(candidate.reasons[:2])
                        or None,
                        note=note,
                    )
                )

        if skill_set_plan.primary_candidate is not None:
            append_records(
                [skill_set_plan.primary_candidate],
                role="primary",
                loaded=True,
                surfaced_in_prompt=True,
                prepared_for_context=True,
                prepared_for_execution=True,
                note="Primary skill retained after stage-aware pruning.",
            )
        append_records(
            skill_set_plan.supporting_candidates,
            role="supporting",
            loaded=True,
            surfaced_in_prompt=True,
            prepared_for_context=True,
            prepared_for_execution=False,
            note="Supporting skill retained inside the stage budget.",
        )
        append_records(
            skill_set_plan.reference_candidates,
            role="reference",
            loaded=False,
            surfaced_in_prompt=True,
            prepared_for_context=False,
            prepared_for_execution=False,
            note="Reference-only skill kept visible for context.",
        )
        append_records(
            skill_set_plan.pruned_supporting_candidates,
            role="pruned_supporting",
            loaded=False,
            surfaced_in_prompt=False,
            prepared_for_context=False,
            prepared_for_execution=False,
            note="Supporting skill pruned by stage-aware budget.",
        )
        append_records(
            skill_set_plan.pruned_reference_candidates,
            role="pruned_reference",
            loaded=False,
            surfaced_in_prompt=False,
            prepared_for_context=False,
            prepared_for_execution=False,
            note="Reference skill pruned by stage-aware budget.",
        )
        append_records(
            resolution_result.rejected_candidates,
            role="rejected",
            loaded=False,
            surfaced_in_prompt=False,
            prepared_for_context=False,
            prepared_for_execution=False,
            note="Rejected during skill resolution before runtime planning.",
        )
        return [record.to_payload() for record in usage_records]

    def build_skill_orchestration_plan(
        self,
        skill_set_plan: SkillSetPlan,
        *,
        resolution_result: skill_models.SkillResolutionResult,
    ) -> dict[str, object]:
        del resolution_result
        planner_output = build_skill_orchestration_preview(skill_set_plan)
        return planner_output.to_payload()

    def build_skill_orchestration_preview_payload(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = True,
    ) -> dict[str, object]:
        preview_payload = self.resolve_best_skill(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=include_reference_only,
        )
        orchestration_plan = cast(
            dict[str, object], preview_payload.get("skill_orchestration_plan", {})
        )
        preview_payload["stage_policy"] = self._active_stage_policy_payload(orchestration_plan)
        preview_payload["worker_promotion_candidates"] = self._worker_promotion_candidates(
            orchestration_plan
        )
        preview_payload["preflight_summary"] = self._orchestration_preflight_summary(
            orchestration_plan
        )
        return preview_payload

    def build_skill_orchestration_preview_prompt_fragment(
        self,
        **kwargs: object,
    ) -> str:
        include_reference_only = (
            cast(bool, kwargs.get("include_reference_only"))
            if isinstance(kwargs.get("include_reference_only"), bool)
            else True
        )
        preview_payload = self.build_skill_orchestration_preview_payload(
            touched_paths=cast(list[str] | None, kwargs.get("touched_paths")),
            workspace_path=cast(str | None, kwargs.get("workspace_path")),
            session_id=cast(str | None, kwargs.get("session_id")),
            top_k=cast(int | None, kwargs.get("top_k")),
            user_goal=cast(str | None, kwargs.get("user_goal")),
            current_prompt=cast(str | None, kwargs.get("current_prompt")),
            scenario_type=cast(str | None, kwargs.get("scenario_type")),
            agent_role=cast(str | None, kwargs.get("agent_role")),
            workflow_stage=cast(str | None, kwargs.get("workflow_stage")),
            available_tools=cast(list[str] | None, kwargs.get("available_tools")),
            invocation_arguments=cast(dict[str, object] | None, kwargs.get("invocation_arguments")),
            include_reference_only=include_reference_only,
        )
        lines = ["Skill orchestration preview:"]
        selected_skills = preview_payload.get("selected_skills")
        if isinstance(selected_skills, list) and selected_skills:
            lines.append(
                "- selected="
                + ", ".join(
                    str(item.get("directory_name") or item.get("name") or "unknown")
                    for item in selected_skills
                    if isinstance(item, dict)
                )
            )
        orchestration_plan = preview_payload.get("skill_orchestration_plan")
        if isinstance(orchestration_plan, dict):
            lines.extend(self._orchestration_plan_preview_lines(orchestration_plan))
        preflight_summary = preview_payload.get("preflight_summary")
        if isinstance(preflight_summary, dict):
            lines.append(
                "- preflight_planned="
                + str(preflight_summary.get("planned_count"))
                + " | auto_runnable="
                + str(preflight_summary.get("auto_runnable_count"))
                + " | approval_gated="
                + str(preflight_summary.get("approval_gated_count"))
            )
        suppression_reasons = preview_payload.get("suppression_reasons")
        if isinstance(suppression_reasons, dict) and suppression_reasons:
            lines.append(
                "- suppressed=" + ", ".join(str(skill_id) for skill_id in suppression_reasons)
            )
        return "\n".join(lines)

    def execute_skill_orchestration_plan(
        self,
        orchestration_plan: dict[str, object],
        *,
        arguments: dict[str, object] | None = None,
        workspace_path: str | None = None,
        touched_paths: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        execution_result = execute_skill_orchestration_runtime(
            orchestration_plan=orchestration_plan,
            execute_skill_facade=self.execute_skill_by_name_or_directory_name,
            arguments=arguments,
            workspace_path=workspace_path,
            touched_paths=touched_paths,
            session_id=session_id,
        )
        return execution_result.to_payload()

    def maybe_replan_skills_for_stage_transition(
        self,
        stage_transition: dict[str, object] | None,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if not isinstance(stage_transition, dict):
            return None
        if stage_transition.get("replan_required") is not True:
            return None
        next_stage = stage_transition.get("to_stage")
        if not isinstance(next_stage, str) or not next_stage.strip():
            return None
        replanned_payload = self.resolve_best_skill(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=next_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=True,
        )
        if replanned_payload.get("status") != "selected":
            return {
                "workflow_stage": next_stage,
                "status": replanned_payload.get("status"),
                "selected_skill_ids": replanned_payload.get("selected_skill_ids", []),
                "skill_orchestration_plan": replanned_payload.get("skill_orchestration_plan", {}),
                "resolution_summary": replanned_payload.get("resolution_summary", {}),
            }
        return {
            "workflow_stage": next_stage,
            "selected_skill_ids": replanned_payload.get("selected_skill_ids", []),
            "skill_budget": replanned_payload.get("skill_budget", {}),
            "skill_set_plan": replanned_payload.get("skill_set_plan", {}),
            "skill_orchestration_plan": replanned_payload.get("skill_orchestration_plan", {}),
            "resolution_summary": replanned_payload.get("resolution_summary", {}),
        }

    def resolve_skill_candidates(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = False,
    ) -> skill_models.SkillResolutionResult:
        active_skills = self.list_active_compiled_skills(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
        )
        request = skill_models.SkillResolutionRequest(
            touched_paths=self._normalize_touched_paths(
                touched_paths or [], workspace_path=workspace_path
            ),
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            workspace_path=workspace_path,
            available_tools=list(available_tools or self.DEFAULT_AVAILABLE_TOOLS),
            invocation_arguments=dict(invocation_arguments or {}),
            top_k=top_k or self.DEFAULT_SKILL_SHORTLIST_K,
            include_reference_only=include_reference_only,
        )
        skill_resolution = import_module("app.compat.skills.resolution")
        return cast(
            skill_models.SkillResolutionResult,
            skill_resolution.resolve_skill_candidates(active_skills, request),
        )

    def list_ranked_skill_candidates(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = False,
    ) -> list[dict[str, object]]:
        resolution_result = self.resolve_skill_candidates(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=include_reference_only,
        )
        skill_set_plan = self.build_skill_set_plan(
            resolution_result,
            workflow_stage=workflow_stage,
            agent_role=agent_role,
        )
        return [
            self._resolved_skill_candidate_payload(candidate)
            for candidate in skill_set_plan.selected_candidates
        ]

    def resolve_best_skill(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = False,
    ) -> dict[str, object]:
        resolution_result = self.resolve_skill_candidates(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=include_reference_only,
        )
        return self._best_skill_payload_from_resolution(
            resolution_result,
            touched_paths=touched_paths,
            include_reference_only=include_reference_only,
        )

    def prepare_best_skill(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = False,
        preferred_skill_identifier: str | None = None,
    ) -> dict[str, object]:
        return self.prepare_selected_skills(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=include_reference_only,
            preferred_skill_identifier=preferred_skill_identifier,
        )

    def prepare_selected_skills(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = False,
        preferred_skill_identifier: str | None = None,
    ) -> dict[str, object]:
        best_skill_result = self.resolve_best_skill(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=include_reference_only,
        )
        if best_skill_result.get("status") != "selected":
            return best_skill_result

        best_skill_result = self._anchor_prepared_result_to_preferred_skill(
            best_skill_result,
            preferred_skill_identifier=preferred_skill_identifier,
        )

        prepared_set = self._prepare_selected_skill_set(
            selected_skills=cast(
                list[dict[str, object]], best_skill_result.get("selected_skills", [])
            ),
            arguments=invocation_arguments,
            workspace_path=workspace_path,
            touched_paths=touched_paths,
            session_id=session_id,
        )
        orchestration_execution = None
        skill_orchestration_plan = cast(
            dict[str, object] | None, best_skill_result.get("skill_orchestration_plan")
        )
        if isinstance(skill_orchestration_plan, dict):
            orchestration_execution = self.execute_skill_orchestration_plan(
                skill_orchestration_plan,
                arguments=invocation_arguments,
                workspace_path=workspace_path,
                touched_paths=touched_paths,
                session_id=session_id,
            )
        stage_transition = None
        replanned_skill_context = None
        if isinstance(orchestration_execution, dict):
            stage_transition = cast(
                dict[str, object] | None, orchestration_execution.get("stage_transition")
            )
            replanned_skill_context = self.maybe_replan_skills_for_stage_transition(
                stage_transition,
                touched_paths=touched_paths,
                workspace_path=workspace_path,
                session_id=session_id,
                top_k=top_k,
                user_goal=user_goal,
                current_prompt=current_prompt,
                scenario_type=scenario_type,
                agent_role=agent_role,
                available_tools=available_tools,
                invocation_arguments=invocation_arguments,
            )
        prepared_context_prompt = self._build_prepared_skill_context_prompt(
            prepared_primary_skill=cast(
                dict[str, object] | None, prepared_set.get("prepared_primary_skill")
            ),
            prepared_supporting_skills=cast(
                list[dict[str, object]], prepared_set.get("prepared_supporting_skills", [])
            ),
            intent_profile=cast(dict[str, object] | None, best_skill_result.get("intent_profile")),
            suppressed_skills=cast(
                list[dict[str, object]], best_skill_result.get("suppressed_skills", [])
            ),
            suppression_reasons=cast(
                dict[str, object], best_skill_result.get("suppression_reasons", {})
            ),
            orchestration_plan=skill_orchestration_plan,
            orchestration_execution=orchestration_execution,
            stage_transition=stage_transition,
            replanned_orchestration_plan=(
                None
                if not isinstance(replanned_skill_context, dict)
                else cast(
                    dict[str, object] | None,
                    replanned_skill_context.get("skill_orchestration_plan"),
                )
            ),
        )
        prepared_result = dict(best_skill_result)
        prepared_result.update(prepared_set)
        prepared_result["skill_orchestration_execution"] = orchestration_execution or {}
        prepared_result["skill_stage_transition"] = stage_transition or {}
        prepared_result["replanned_skill_context"] = replanned_skill_context or {}
        prepared_result["prepared_context_prompt"] = prepared_context_prompt
        return prepared_result

    def _anchor_prepared_result_to_preferred_skill(
        self,
        best_skill_result: dict[str, object],
        *,
        preferred_skill_identifier: str | None,
    ) -> dict[str, object]:
        if (
            not isinstance(preferred_skill_identifier, str)
            or not preferred_skill_identifier.strip()
        ):
            return best_skill_result
        preferred_normalized = preferred_skill_identifier.strip().casefold()
        selected_skills = cast(
            list[dict[str, object]], best_skill_result.get("selected_skills", [])
        )
        primary_skill = cast(dict[str, object] | None, best_skill_result.get("primary_skill"))
        if not selected_skills or not isinstance(primary_skill, dict):
            return best_skill_result

        def _entry_identity(item: dict[str, object]) -> str | None:
            for value in (item.get("id"), item.get("directory_name"), item.get("name")):
                if isinstance(value, str) and value.strip():
                    return value.strip().casefold()
            return None

        def _matches(item: dict[str, object]) -> bool:
            return _entry_identity(item) == preferred_normalized

        def _with_role(item: dict[str, object], role: str) -> dict[str, object]:
            updated = dict(item)
            updated["role"] = role
            selection_explanation = updated.get("selection_explanation")
            if isinstance(selection_explanation, dict):
                updated["selection_explanation"] = {
                    **selection_explanation,
                    "selection_role": role,
                }
            return updated

        if _matches(primary_skill):
            return best_skill_result

        preferred_entry = next((item for item in selected_skills if _matches(item)), None)
        if preferred_entry is None:
            return best_skill_result

        primary_identity = _entry_identity(primary_skill)
        selected_entries = [dict(item) for item in selected_skills if isinstance(item, dict)]
        updated_preferred = _with_role(preferred_entry, "primary")
        updated_supporting: list[dict[str, object]] = []
        for item in selected_entries:
            item_identity = _entry_identity(item)
            if item_identity == _entry_identity(preferred_entry):
                continue
            if item_identity == primary_identity:
                updated_supporting.append(_with_role(item, "supporting"))
                continue
            updated_supporting.append(_with_role(item, str(item.get("role") or "supporting")))
        updated_selected_skills = [updated_preferred, *updated_supporting]

        anchored_result = dict(best_skill_result)
        anchored_result["primary_skill"] = updated_preferred
        anchored_result["selected_skill"] = updated_preferred
        anchored_result["selected_skill_id"] = updated_preferred.get("id")
        anchored_result["selected_skill_rank"] = updated_preferred.get("rank")
        anchored_result["supporting_skills"] = updated_supporting
        anchored_result["selected_skills"] = [updated_preferred, *updated_supporting]
        anchored_result["selected_skill_ids"] = [
            str(item.get("id"))
            for item in cast(list[dict[str, object]], anchored_result["selected_skills"])
            if isinstance(item.get("id"), str)
        ]

        resolution_payload = anchored_result.get("resolution")
        if isinstance(resolution_payload, dict):
            updated_resolution = dict(resolution_payload)
            updated_resolution["primary_candidate"] = updated_preferred
            updated_resolution["selected_skill_id"] = updated_preferred.get("id")
            updated_resolution["selected_candidates"] = list(updated_selected_skills)
            updated_resolution["supporting_candidates"] = list(updated_supporting)
            anchored_result["resolution"] = updated_resolution

        resolution_summary = anchored_result.get("resolution_summary")
        if isinstance(resolution_summary, dict):
            updated_summary = dict(resolution_summary)
            updated_summary["primary_skill_id"] = updated_preferred.get("id")
            updated_summary["selected_skill_id"] = updated_preferred.get("id")
            anchored_result["resolution_summary"] = updated_summary

        skill_set_plan = anchored_result.get("skill_set_plan")
        if isinstance(skill_set_plan, dict):
            updated_plan = dict(skill_set_plan)
            updated_plan["primary_skill"] = updated_preferred
            updated_plan["supporting_skills"] = list(updated_supporting)
            updated_plan["selected_skills"] = list(updated_selected_skills)
            selected_skill_ids = anchored_result.get("selected_skill_ids")
            updated_plan["selected_skill_ids"] = (
                list(selected_skill_ids) if isinstance(selected_skill_ids, list) else []
            )
            anchored_result["skill_set_plan"] = updated_plan
        orchestration_plan = anchored_result.get("skill_orchestration_plan")
        if isinstance(orchestration_plan, dict):
            updated_orchestration_plan = dict(orchestration_plan)
            selected_skill_ids = anchored_result.get("selected_skill_ids")
            updated_orchestration_plan["primary_skill_id"] = updated_preferred.get("id")
            updated_orchestration_plan["selected_skill_ids"] = (
                list(selected_skill_ids) if isinstance(selected_skill_ids, list) else []
            )
            stages_payload = updated_orchestration_plan.get("stages")
            if isinstance(stages_payload, list):
                updated_stages: list[dict[str, object]] = []
                for stage in stages_payload:
                    if not isinstance(stage, dict):
                        continue
                    updated_stage = dict(stage)
                    raw_steps = updated_stage.get("steps")
                    if isinstance(raw_steps, list):
                        updated_steps: list[dict[str, object]] = []
                        for step in raw_steps:
                            if not isinstance(step, dict):
                                continue
                            updated_step = dict(step)
                            step_skill_id = updated_step.get("skill_id")
                            if step_skill_id == updated_preferred.get("id"):
                                updated_step["role"] = "primary"
                                updated_step["execution_intent"] = "execute_primary"
                                updated_step["prepared_for_execution"] = True
                            elif step_skill_id == primary_identity:
                                updated_step["role"] = "supporting"
                                updated_step["execution_intent"] = "candidate_worker"
                                updated_step["prepared_for_execution"] = False
                            updated_steps.append(updated_step)
                        updated_stage["steps"] = updated_steps
                    updated_stages.append(updated_stage)
                updated_orchestration_plan["stages"] = updated_stages
            anchored_result["skill_orchestration_plan"] = updated_orchestration_plan
        return anchored_result

    def _prepare_selected_skill_set(
        self,
        *,
        selected_skills: list[dict[str, object]],
        arguments: dict[str, object] | None = None,
        workspace_path: str | None = None,
        touched_paths: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        prepared_selected_skills: list[dict[str, object]] = []
        prepared_supporting_skills: list[dict[str, object]] = []
        primary_prepared: dict[str, object] | None = None
        primary_execution: dict[str, object] | None = None
        primary_skill_payload: dict[str, object] | None = None

        for item in selected_skills:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id") or item.get("directory_name")
            if not isinstance(identifier, str) or not identifier.strip():
                continue
            execution_result = self.execute_skill_by_name_or_directory_name(
                identifier,
                arguments=arguments,
                workspace_path=workspace_path,
                touched_paths=touched_paths,
                session_id=session_id,
            )
            role = str(item.get("role") or "")
            prepared_entry = dict(item)
            prepared_entry["prepared_for_context"] = True
            prepared_entry["prepared_for_execution"] = role == "primary"
            prepared_entry["execution"] = execution_result.get("execution")
            prepared_entry["prepared_skill"] = execution_result.get("skill")
            prepared_selected_skills.append(prepared_entry)
            if role == "primary":
                primary_prepared = prepared_entry
                primary_execution = cast(
                    dict[str, object] | None, execution_result.get("execution")
                )
                primary_skill_payload = cast(
                    dict[str, object] | None, execution_result.get("skill")
                )
            else:
                prepared_supporting_skills.append(prepared_entry)

        return {
            "execution": primary_execution,
            "skill": primary_skill_payload,
            "prepared_primary_skill": primary_prepared,
            "primary_prepared": primary_prepared,
            "supporting_prepared": prepared_supporting_skills,
            "prepared_selected_skills": prepared_selected_skills,
            "prepared_supporting_skills": prepared_supporting_skills,
        }

    def _build_prepared_skill_context_prompt(
        self,
        *,
        prepared_primary_skill: dict[str, object] | None,
        prepared_supporting_skills: list[dict[str, object]],
        intent_profile: dict[str, object] | None,
        suppressed_skills: list[dict[str, object]],
        suppression_reasons: dict[str, object],
        orchestration_plan: dict[str, object] | None = None,
        orchestration_execution: dict[str, object] | None = None,
        stage_transition: dict[str, object] | None = None,
        replanned_orchestration_plan: dict[str, object] | None = None,
    ) -> str:
        lines: list[str] = []
        execution_payload = (
            prepared_primary_skill.get("execution")
            if isinstance(prepared_primary_skill, dict)
            else None
        )
        prepared_prompt = (
            execution_payload.get("prepared_prompt")
            if isinstance(execution_payload, dict)
            else None
        )
        if isinstance(prepared_prompt, str) and prepared_prompt.strip():
            lines.append(prepared_prompt.strip())

        if isinstance(intent_profile, dict):
            lines.extend(
                [
                    "Task intent profile:",
                    f"- dominant_domain={intent_profile.get('dominant_domain')}",
                    f"- is_ctf={intent_profile.get('is_ctf')}",
                    f"- is_remote_service={intent_profile.get('is_remote_service')}",
                    f"- is_http_target={intent_profile.get('is_http_target')}",
                    f"- is_local_codebase_task={intent_profile.get('is_local_codebase_task')}",
                ]
            )

        if isinstance(prepared_primary_skill, dict):
            primary_name = str(
                prepared_primary_skill.get("directory_name")
                or prepared_primary_skill.get("name")
                or "unknown"
            )
            lines.extend(
                [
                    "",
                    "Primary skill:",
                    f"- {primary_name} | "
                    f"prepared_for_context={prepared_primary_skill.get('prepared_for_context')} | "
                    f"prepared_for_execution={prepared_primary_skill.get('prepared_for_execution')}",
                ]
            )
            selection_explanation = prepared_primary_skill.get("selection_explanation")
            if isinstance(selection_explanation, dict):
                primary_why = selection_explanation.get(
                    "why_high_relevance", "high blended relevance"
                )
                lines.append(f"  why={primary_why}")
            verification_mode = prepared_primary_skill.get("verification_mode")
            trust_level = prepared_primary_skill.get("trust_level")
            version = prepared_primary_skill.get("version")
            model_hint = prepared_primary_skill.get("model_hint")
            if isinstance(verification_mode, str) and verification_mode.strip():
                lines.append(f"  verification_mode={verification_mode}")
            if isinstance(trust_level, str) and trust_level.strip():
                lines.append(f"  trust_level={trust_level}")
            if isinstance(version, str) and version.strip():
                lines.append(f"  version={version}")
            if isinstance(model_hint, str) and model_hint.strip():
                lines.append(f"  model_hint={model_hint}")
            preflight_checks = prepared_primary_skill.get("preflight_checks")
            if isinstance(preflight_checks, list) and preflight_checks:
                preflight_names = [
                    str(item.get("name"))
                    for item in preflight_checks
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                ]
                if preflight_names:
                    lines.append(f"  preflight={', '.join(preflight_names)}")

        if prepared_supporting_skills:
            lines.extend(["", "Supporting skills prepared for context:"])
            for item in prepared_supporting_skills:
                supporting_name = str(item.get("directory_name") or item.get("name") or "unknown")
                lines.append(
                    f"- {supporting_name} | "
                    f"prepared_for_context={item.get('prepared_for_context')} | "
                    f"prepared_for_execution={item.get('prepared_for_execution')}"
                )
                version = item.get("version")
                model_hint = item.get("model_hint")
                if isinstance(version, str) and version.strip():
                    lines.append(f"  version={version}")
                if isinstance(model_hint, str) and model_hint.strip():
                    lines.append(f"  model_hint={model_hint}")
                packing_explanation = item.get("packing_explanation")
                if isinstance(packing_explanation, dict):
                    supporting_why = packing_explanation.get(
                        "why_selected", "selected for complement"
                    )
                    lines.append(f"  why={supporting_why}")

        selected_explanations: list[str] = []
        if isinstance(prepared_primary_skill, dict):
            primary_label = (
                prepared_primary_skill.get("directory_name")
                or prepared_primary_skill.get("name")
                or "unknown"
            )
            selected_explanations.append(
                f"- {primary_label}: "
                f"{self._explanation_text(prepared_primary_skill.get('selection_explanation'))}"
            )
        for item in prepared_supporting_skills:
            supporting_name = str(item.get("directory_name") or item.get("name") or "unknown")
            selected_explanations.append(
                f"- {supporting_name}: {self._explanation_text(item.get('packing_explanation'))}"
            )
        if selected_explanations:
            lines.extend(["", "Why these skills were selected together:", *selected_explanations])

        if isinstance(orchestration_plan, dict):
            active_stage = orchestration_plan.get("active_stage")
            stages = orchestration_plan.get("stages")
            lines.extend(["", "Orchestration plan preview:"])
            if isinstance(active_stage, str) and active_stage.strip():
                lines.append(f"- active_stage={active_stage}")
            if isinstance(stages, list) and stages:
                first_stage = stages[0]
                if isinstance(first_stage, dict):
                    lines.append(
                        "- mode="
                        f"{first_stage.get('mode')} | "
                        f"failure_policy={first_stage.get('failure_policy')} | "
                        f"max_parallel_workers={first_stage.get('max_parallel_workers')}"
                    )
                    stage_steps = first_stage.get("steps")
                    if isinstance(stage_steps, list):
                        candidate_workers = [
                            str(step.get("directory_name") or step.get("name") or "unknown")
                            for step in stage_steps
                            if isinstance(step, dict) and step.get("role") == "supporting"
                        ]
                        reference_steps = [
                            str(step.get("directory_name") or step.get("name") or "unknown")
                            for step in stage_steps
                            if isinstance(step, dict) and step.get("role") == "reference"
                        ]
                        reducer_present = any(
                            isinstance(step, dict) and step.get("role") == "reducer"
                            for step in stage_steps
                        )
                        verifier_present = any(
                            isinstance(step, dict) and step.get("role") == "verifier"
                            for step in stage_steps
                        )
                        if candidate_workers:
                            lines.append(f"- candidate_workers={', '.join(candidate_workers)}")
                        if reference_steps:
                            lines.append(f"- reference_only={', '.join(reference_steps)}")
                        if reducer_present:
                            lines.append("- reducer=enabled")
                        if verifier_present:
                            lines.append("- verifier=enabled")
            replan_triggers = orchestration_plan.get("replan_triggers")
            if isinstance(replan_triggers, list) and replan_triggers:
                lines.append(
                    "- replan_triggers="
                    + ", ".join(str(trigger) for trigger in replan_triggers if str(trigger).strip())
                )

        if isinstance(orchestration_execution, dict) and orchestration_execution:
            lines.extend(["", "Orchestration execution summary:"])
            lines.append(
                "- status="
                + str(orchestration_execution.get("status"))
                + " | duration_ms="
                + str(orchestration_execution.get("duration_ms"))
            )
            worker_results = orchestration_execution.get("worker_results")
            if isinstance(worker_results, list):
                executed_workers = [
                    str(result.get("name") or result.get("skill_id") or "unknown")
                    for result in worker_results
                    if isinstance(result, dict)
                    and result.get("status") == "succeeded"
                    and result.get("role") == "supporting"
                ]
                approval_pending = [
                    str(result.get("name") or result.get("skill_id") or "unknown")
                    for result in worker_results
                    if isinstance(result, dict) and result.get("approval_needed") is True
                ]
                if executed_workers:
                    lines.append(f"- executed_workers={', '.join(executed_workers)}")
                if approval_pending:
                    lines.append(f"- approval_pending={', '.join(approval_pending)}")
            reduction_result = orchestration_execution.get("reduction_result")
            if isinstance(reduction_result, dict):
                lines.append(
                    "- reduction_status=" + str(reduction_result.get("status") or "unknown")
                )
            verification_result = orchestration_execution.get("verification_result")
            if isinstance(verification_result, dict):
                lines.append(
                    "- verification_passed="
                    + str(verification_result.get("passed"))
                    + " | requested_next_stage="
                    + str(verification_result.get("requested_next_stage"))
                )

        if isinstance(stage_transition, dict) and stage_transition:
            lines.extend(["", "Stage transition:"])
            lines.append(
                "- from_stage="
                + str(stage_transition.get("from_stage"))
                + " | to_stage="
                + str(stage_transition.get("to_stage"))
                + " | replan_required="
                + str(stage_transition.get("replan_required"))
            )

        if isinstance(replanned_orchestration_plan, dict) and replanned_orchestration_plan:
            lines.extend(["", "Replanned orchestration preview:"])
            lines.append(
                "- next_active_stage="
                + str(
                    replanned_orchestration_plan.get("active_stage")
                    or replanned_orchestration_plan.get("workflow_stage")
                )
            )

        if suppressed_skills:
            lines.extend(["", "Suppressed skills:"])
            for item in suppressed_skills:
                suppressed_name = str(item.get("directory_name") or item.get("name") or "unknown")
                suppressed_id = item.get("id")
                raw_reason = suppression_reasons.get(suppressed_name)
                if raw_reason is None and isinstance(suppressed_id, str):
                    raw_reason = suppression_reasons.get(suppressed_id)
                if isinstance(raw_reason, list):
                    reason_text = "; ".join(str(reason) for reason in raw_reason)
                elif isinstance(raw_reason, str) and raw_reason.strip():
                    reason_text = raw_reason
                else:
                    reason_text = str(item.get("rejected_reason") or "suppressed_by_intent")
                lines.append(f"- {suppressed_name} | reason={reason_text}")

        guidance_lines = ["", "Guidance:"]
        is_ctf = bool(intent_profile.get("is_ctf")) if isinstance(intent_profile, dict) else False
        is_http_target = (
            bool(intent_profile.get("is_http_target"))
            if isinstance(intent_profile, dict)
            else False
        )
        if is_ctf or is_http_target:
            guidance_lines.extend(
                [
                    "- Use solve-challenge for challenge triage/dispatch when the task is a "
                    "remote or vague CTF service.",
                    "- Use ctf-web as the specialized execution reference for HTTP targets.",
                ]
            )
        if suppressed_skills:
            guidance_lines.append(
                "- Do not promote Java audit skills without Java project evidence."
            )
        if len(guidance_lines) > 2:
            lines.extend(guidance_lines)
        return "\n".join(lines)

    @staticmethod
    def _active_stage_policy_payload(orchestration_plan: dict[str, object]) -> dict[str, object]:
        stages = orchestration_plan.get("stages")
        if not isinstance(stages, list) or not stages:
            return {}
        active_stage = orchestration_plan.get("active_stage")
        selected_stage = next(
            (
                stage
                for stage in stages
                if isinstance(stage, dict) and stage.get("stage_name") == active_stage
            ),
            None,
        )
        if not isinstance(selected_stage, dict):
            selected_stage = next((stage for stage in stages if isinstance(stage, dict)), None)
        if not isinstance(selected_stage, dict):
            return {}
        return {
            "stage_name": selected_stage.get("stage_name"),
            "mode": selected_stage.get("mode"),
            "failure_policy": selected_stage.get("failure_policy"),
            "max_parallel_workers": selected_stage.get("max_parallel_workers"),
            "worker_timeout_ms": selected_stage.get("worker_timeout_ms"),
            "orchestration_timeout_ms": selected_stage.get("orchestration_timeout_ms"),
            "retry_limit": selected_stage.get("retry_limit"),
            "notes": (
                list(selected_stage.get("notes", []))
                if isinstance(selected_stage.get("notes"), list)
                else []
            ),
        }

    @staticmethod
    def _worker_promotion_candidates(orchestration_plan: dict[str, object]) -> list[str]:
        stages = orchestration_plan.get("stages")
        if not isinstance(stages, list):
            return []
        active_stage = orchestration_plan.get("active_stage")
        selected_stage = next(
            (
                stage
                for stage in stages
                if isinstance(stage, dict) and stage.get("stage_name") == active_stage
            ),
            None,
        )
        if not isinstance(selected_stage, dict):
            return []
        stage_steps = selected_stage.get("steps")
        if not isinstance(stage_steps, list):
            return []
        return [
            str(step.get("directory_name") or step.get("name") or "unknown")
            for step in stage_steps
            if isinstance(step, dict) and step.get("execution_intent") == "candidate_worker"
        ]

    def _orchestration_preflight_summary(
        self,
        orchestration_plan: dict[str, object],
    ) -> dict[str, object]:
        planned_count = 0
        auto_runnable_count = 0
        approval_gated_count = 0
        planned_names: list[str] = []
        stages = orchestration_plan.get("stages")
        if not isinstance(stages, list):
            return {
                "planned_count": 0,
                "auto_runnable_count": 0,
                "approval_gated_count": 0,
                "planned_names": [],
            }
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            stage_steps = stage.get("steps")
            if not isinstance(stage_steps, list):
                continue
            for step in stage_steps:
                if not isinstance(step, dict):
                    continue
                trust_level = cast(str | None, step.get("trust_level"))
                for payload in cast(list[dict[str, object]], step.get("preflight_checks", [])):
                    check = SkillPreflightCheck(
                        name=str(payload.get("name") or "preflight"),
                        kind=str(payload.get("kind") or payload.get("name") or "generic"),
                        required=bool(payload.get("required", True)),
                        read_only=bool(payload.get("read_only", True)),
                        description=cast(str | None, payload.get("description")),
                        metadata=cast(dict[str, object], payload.get("metadata", {})),
                    )
                    planned_count += 1
                    planned_names.append(check.name)
                    if can_auto_run_preflight(check, trust_level=trust_level):
                        auto_runnable_count += 1
                    else:
                        approval_gated_count += 1
        return {
            "planned_count": planned_count,
            "auto_runnable_count": auto_runnable_count,
            "approval_gated_count": approval_gated_count,
            "planned_names": planned_names,
        }

    @classmethod
    def _orchestration_plan_preview_lines(cls, orchestration_plan: dict[str, object]) -> list[str]:
        lines = ["- active_stage=" + str(orchestration_plan.get("active_stage"))]
        stage_policy = cls._active_stage_policy_payload(orchestration_plan)
        if stage_policy:
            lines.append(
                "- mode="
                + str(stage_policy.get("mode"))
                + " | failure_policy="
                + str(stage_policy.get("failure_policy"))
                + " | max_parallel_workers="
                + str(stage_policy.get("max_parallel_workers"))
            )
            lines.append(
                "- worker_timeout_ms="
                + str(stage_policy.get("worker_timeout_ms"))
                + " | orchestration_timeout_ms="
                + str(stage_policy.get("orchestration_timeout_ms"))
                + " | retry_limit="
                + str(stage_policy.get("retry_limit"))
            )
        worker_candidates = cls._worker_promotion_candidates(orchestration_plan)
        if worker_candidates:
            lines.append("- candidate_workers=" + ", ".join(worker_candidates))
        stages = orchestration_plan.get("stages")
        if isinstance(stages, list) and stages:
            first_stage = next((stage for stage in stages if isinstance(stage, dict)), None)
            if isinstance(first_stage, dict):
                steps = first_stage.get("steps")
                if isinstance(steps, list):
                    reducer_nodes = [
                        str(step.get("skill_id") or step.get("step_id"))
                        for step in steps
                        if isinstance(step, dict) and step.get("role") == "reducer"
                    ]
                    verifier_nodes = [
                        str(step.get("skill_id") or step.get("step_id"))
                        for step in steps
                        if isinstance(step, dict) and step.get("role") == "verifier"
                    ]
                    if reducer_nodes:
                        lines.append("- reducer_nodes=" + ", ".join(reducer_nodes))
                    if verifier_nodes:
                        lines.append("- verifier_nodes=" + ", ".join(verifier_nodes))
        replan_triggers = orchestration_plan.get("replan_triggers")
        if isinstance(replan_triggers, list) and replan_triggers:
            lines.append(
                "- replan_triggers="
                + ", ".join(str(trigger) for trigger in replan_triggers if str(trigger).strip())
            )
        return lines

    def _explanation_text(self, value: object) -> str:
        if isinstance(value, dict):
            why_selected = value.get("why_selected")
            if isinstance(why_selected, str) and why_selected.strip():
                return why_selected
            why_high_relevance = value.get("why_high_relevance")
            if isinstance(why_high_relevance, str) and why_high_relevance.strip():
                return why_high_relevance
        return "selected for overall relevance and complement"

    def build_ranked_skill_context_prompt_fragment(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
        include_reference_only: bool = True,
    ) -> str:
        resolution_result = self.resolve_skill_candidates(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=include_reference_only,
        )
        skill_resolution = import_module("app.compat.skills.resolution")
        return cast(str, skill_resolution.build_skill_candidate_prompt_fragment(resolution_result))

    def find_skill_by_name_or_directory_name(self, name_or_slug: str) -> SkillRecordRead | None:
        record = self._find_skill_record_by_identifier(name_or_slug, loaded_only=True)
        if record is None:
            return None
        return to_skill_record_read(record)

    def read_skill_content(self, skill_id: str) -> str:
        record = self._get_visible_skill_record(skill_id)
        if record is None:
            raise SkillLookupError("Skill not found.")
        return self._read_skill_entry_file(record)

    def get_skill_content(self, skill_id: str) -> SkillContentRead | None:
        record = self._get_visible_skill_record(skill_id)
        if record is None:
            return None
        return self._build_skill_content(record)

    def read_skill_content_by_name_or_directory_name(self, name_or_slug: str) -> SkillContentRead:
        record = self._find_skill_record_by_identifier(name_or_slug, loaded_only=True)
        if record is None:
            raise SkillLookupError(f"Skill '{name_or_slug}' not found among loaded skills.")
        return self._build_skill_content(record)

    def execute_skill_by_name_or_directory_name(
        self,
        name_or_slug: str,
        *,
        arguments: dict[str, object] | None = None,
        workspace_path: str | None = None,
        touched_paths: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        compiled_skill = self.find_compiled_skill_by_name_or_directory_name(
            name_or_slug,
            arguments=arguments,
            workspace_path=workspace_path,
            touched_paths=touched_paths,
            session_id=session_id,
        )
        if compiled_skill is None:
            raise SkillLookupError(f"Skill '{name_or_slug}' not found among loaded skills.")
        if not compiled_skill.invocable:
            raise SkillLookupError(
                f"Skill '{name_or_slug}' is reference-only and must stay on "
                "MCP/capability surfaces."
            )

        record = self._get_visible_skill_record(compiled_skill.skill_id)
        skill_content = (
            self._build_skill_content(record)
            if record is not None
            else self._build_transient_skill_content(compiled_skill)
        )
        skill_payload = skill_content.model_dump(mode="json")
        prepared_invocation = (
            None
            if compiled_skill.prepared_invocation is None
            else compiled_skill.prepared_invocation.to_payload()
        )
        return {
            "execution": {
                "status": "prepared",
                "mode": "server_skill_executor_facade",
                "tool": "execute_skill",
                "skill_name_or_id": name_or_slug,
                "skill_id": skill_content.id,
                "skill_directory_name": skill_content.directory_name,
                "prepared_prompt": compiled_skill.prepared_prompt,
                "available_tools": [
                    "execute_kali_command",
                    "list_available_skills",
                    "execute_skill",
                    "read_skill_content",
                ],
                "resolved_identity": {
                    "source_kind": compiled_skill.identity.source_kind.value,
                    "source_root": compiled_skill.identity.source_root,
                    "relative_path": compiled_skill.identity.relative_path,
                    "fingerprint": compiled_skill.identity.fingerprint,
                },
                "conditional_activation": {
                    "conditional": compiled_skill.is_conditional,
                    "paths": list(compiled_skill.activation_paths),
                },
                "shell_enabled": compiled_skill.shell_enabled,
                "prepared_invocation": prepared_invocation,
            },
            "skill": skill_payload,
        }

    def build_skill_context_payload(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
    ) -> dict[str, object]:
        prepared_result = self.prepare_selected_skills(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
            include_reference_only=True,
        )
        resolution_payload = prepared_result.get("resolution")
        primary_skill = prepared_result.get("primary_skill")
        supporting_skills = prepared_result.get("supporting_skills", [])
        reference_skills = prepared_result.get("reference_skills", [])
        rejected_skills = prepared_result.get("rejected_skills", [])
        selected_skills = cast(list[dict[str, object]], prepared_result.get("selected_skills", []))
        selected_skill_ids = cast(
            list[str],
            prepared_result.get("selected_skill_ids", []),
        )
        return {
            "skills": selected_skills,
            "primary_skill": primary_skill,
            "supporting_skills": supporting_skills,
            "reference_skills": reference_skills,
            "rejected_skills": rejected_skills,
            "pruned_supporting_skills": prepared_result.get("pruned_supporting_skills", []),
            "pruned_reference_skills": prepared_result.get("pruned_reference_skills", []),
            "selected_skills": selected_skills,
            "selected_skill_ids": selected_skill_ids,
            "selected_skill": prepared_result.get("selected_skill"),
            "selected_skill_id": prepared_result.get("selected_skill_id"),
            "selected_skill_rank": prepared_result.get("selected_skill_rank"),
            "skill_budget": prepared_result.get("skill_budget", {}),
            "skill_set_plan": prepared_result.get("skill_set_plan", {}),
            "skill_orchestration_plan": prepared_result.get("skill_orchestration_plan", {}),
            "skill_orchestration_execution": prepared_result.get(
                "skill_orchestration_execution", {}
            ),
            "skill_stage_transition": prepared_result.get("skill_stage_transition", {}),
            "replanned_skill_context": prepared_result.get("replanned_skill_context", {}),
            "skill_runtime_usage": prepared_result.get("skill_runtime_usage", []),
            "intent_profile": prepared_result.get("intent_profile"),
            "prepared_selected_skills": prepared_result.get("prepared_selected_skills", []),
            "prepared_supporting_skills": prepared_result.get("prepared_supporting_skills", []),
            "prepared_primary_skill": prepared_result.get("prepared_primary_skill"),
            "primary_prepared": prepared_result.get("primary_prepared"),
            "supporting_prepared": prepared_result.get("supporting_prepared", []),
            "prepared_context_prompt": prepared_result.get("prepared_context_prompt"),
            "suppressed_skills": prepared_result.get("suppressed_skills", []),
            "suppression_reasons": prepared_result.get("suppression_reasons", {}),
            "resolution": resolution_payload,
        }

    def build_skill_context_prompt_fragment(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
    ) -> str:
        payload = self.build_skill_context_payload(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
        )
        selected_skills_payload = payload.get("selected_skills", [])
        selected_skills = (
            cast(list[dict[str, object]], selected_skills_payload)
            if isinstance(selected_skills_payload, list)
            else []
        )
        reference_skills_payload = payload.get("reference_skills", [])
        reference_skills = (
            cast(list[dict[str, object]], reference_skills_payload)
            if isinstance(reference_skills_payload, list)
            else []
        )
        if not selected_skills and not reference_skills:
            return "No loaded skills are currently available."
        lines: list[str] = []
        skill_budget = payload.get("skill_budget", {})
        skill_set_plan = payload.get("skill_set_plan", {})
        if isinstance(skill_budget, dict):
            lines.extend(
                [
                    "Skill set plan for this stage:",
                    (
                        "- budget: "
                        f"primary={skill_budget.get('max_primary', 1)} "
                        f"supporting={skill_budget.get('max_supporting', 0)} "
                        f"reference={skill_budget.get('max_reference', 0)}"
                    ),
                ]
            )
        if isinstance(skill_set_plan, dict):
            notes = skill_set_plan.get("notes", [])
            if isinstance(notes, list):
                for note in notes:
                    if isinstance(note, str) and note.strip():
                        lines.append(f"- {note}")
        prepared_context_prompt = payload.get("prepared_context_prompt")
        if isinstance(prepared_context_prompt, str) and prepared_context_prompt.strip():
            lines.extend(["", prepared_context_prompt.strip(), ""])
        prompting_module = import_module("app.agent.prompting")
        plan_available_skills: list[SkillAgentSummaryRead] = []
        for item in [*selected_skills, *reference_skills]:
            if not isinstance(item, dict):
                continue
            plan_available_skills.append(SkillAgentSummaryRead.model_validate(item))
        lines.append(
            cast(
                str,
                prompting_module.render_skill_catalog_context(plan_available_skills),
            )
        )
        pruned_supporting = payload.get("pruned_supporting_skills", [])
        pruned_reference = payload.get("pruned_reference_skills", [])
        pruned_lines: list[str] = []
        if isinstance(pruned_supporting, list) and pruned_supporting:
            pruned_lines.extend(
                str(item.get("directory_name") or item.get("name") or "unknown")
                for item in pruned_supporting
                if isinstance(item, dict)
            )
        if isinstance(pruned_reference, list) and pruned_reference:
            pruned_lines.extend(
                str(item.get("directory_name") or item.get("name") or "unknown")
                for item in pruned_reference
                if isinstance(item, dict)
            )
        if pruned_lines:
            lines.extend(
                [
                    "",
                    "Related skills pruned for context budget:",
                    f"- {', '.join(pruned_lines)}",
                ]
            )
        suppressed_skills = payload.get("suppressed_skills", [])
        if isinstance(suppressed_skills, list) and suppressed_skills:
            suppressed_names = [
                str(item.get("directory_name") or item.get("name") or "unknown")
                for item in suppressed_skills
                if isinstance(item, dict)
            ]
            lines.extend(["", "Suppressed skills:", f"- {', '.join(suppressed_names)}"])
        prepared_selected = payload.get("prepared_selected_skills", [])
        if isinstance(prepared_selected, list) and prepared_selected:
            prepared_lines = []
            for item in prepared_selected:
                if not isinstance(item, dict):
                    continue
                prepared_lines.append(
                    f"{item.get('directory_name') or item.get('name')}: "
                    f"context={item.get('prepared_for_context')} "
                    f"execution={item.get('prepared_for_execution')}"
                )
            if prepared_lines:
                lines.extend(["", "Prepared skill set:", *[f"- {line}" for line in prepared_lines]])
        lines.append("")
        lines.append(
            "Never call a skill slug or skill name directly as a tool alias unless the runtime "
            "explicitly exposes it. The fixed callable tool names are execute_kali_command, "
            "list_available_skills, execute_skill, and read_skill_content. Use execute_skill "
            "when you want the server-side skill executor facade to resolve and prepare a "
            "specific skill context, including invocation metadata and pending approval hints, "
            "and use "
            "read_skill_content "
            "when you only need the raw SKILL.md body."
        )
        lines.append(
            "When the intent profile indicates a remote challenge, keep the dispatcher and the "
            "specialized supporting skill together instead of replacing one with the other."
        )
        return "\n".join(lines)

    def build_active_skill_snapshot(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return self.list_ranked_skill_candidates(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
        )

    def build_best_skill_snapshot(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        user_goal: str | None = None,
        current_prompt: str | None = None,
        scenario_type: str | None = None,
        agent_role: str | None = None,
        workflow_stage: str | None = None,
        available_tools: list[str] | None = None,
        invocation_arguments: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        best_skill_result = self.resolve_best_skill(
            touched_paths=touched_paths,
            workspace_path=workspace_path,
            session_id=session_id,
            top_k=top_k,
            user_goal=user_goal,
            current_prompt=current_prompt,
            scenario_type=scenario_type,
            agent_role=agent_role,
            workflow_stage=workflow_stage,
            available_tools=available_tools,
            invocation_arguments=invocation_arguments,
        )
        primary_skill = best_skill_result.get("primary_skill")
        return primary_skill if isinstance(primary_skill, dict) else None

    def rescan_skills(self) -> list[SkillRecordRead]:
        self._cache.clear_entry_content_cache()
        self._cache.clear_scan_roots_cache()
        records = [self._to_skill_record(parsed) for parsed in self._scan_and_parse()]
        self._repository.replace_all(records)
        return self.list_skills()

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> SkillRecordRead | None:
        record = self._get_visible_skill_record(skill_id)
        if record is None:
            return None
        updated = self._repository.set_enabled(record, enabled)
        return to_skill_record_read(updated)

    def _scan_and_parse(self) -> list[skill_models.ParsedSkillRecordData]:
        discovered_files = scan_skill_files(self._resolve_scan_roots())
        parsed_records = [parse_skill_file(discovered_file) for discovered_file in discovered_files]
        parsed_records.extend(self._scan_mcp_capability_records())
        return parsed_records

    def _resolve_scan_roots(
        self,
        *,
        discovery_paths: list[str] | None = None,
    ) -> list[skill_models.SkillScanRoot]:
        normalized_discovery_paths = [
            path for path in discovery_paths or [] if path and path.strip()
        ]
        return self._cache.get_or_resolve_scan_roots(
            include_compatibility_roots=self._settings.skill_compatibility_scan_enabled,
            extra_dirs=list(self._settings.skill_extra_dirs),
            discovery_paths=normalized_discovery_paths,
            resolver=lambda: self._resolve_skill_scan_roots_with_compatibility(
                normalized_discovery_paths,
            ),
        )

    def _resolve_skill_scan_roots_with_compatibility(
        self,
        discovery_paths: list[str],
    ) -> list[skill_models.SkillScanRoot]:
        if not discovery_paths or not self._resolve_skill_scan_roots_supports_discovery_paths():
            return resolve_skill_scan_roots(self._settings)
        return resolve_skill_scan_roots(
            self._settings,
            discovery_paths=discovery_paths,
        )

    @staticmethod
    def _resolve_skill_scan_roots_supports_discovery_paths() -> bool:
        parameters = signature(resolve_skill_scan_roots).parameters.values()
        return any(
            parameter.kind is Parameter.VAR_KEYWORD or parameter.name == "discovery_paths"
            for parameter in parameters
        )

    def list_active_compiled_skills(
        self,
        *,
        touched_paths: list[str] | None = None,
        workspace_path: str | None = None,
        session_id: str | None = None,
    ) -> list[skill_models.CompiledSkill]:
        normalized_touched_paths = self._normalize_touched_paths(
            touched_paths or [],
            workspace_path=workspace_path,
        )
        registry = self._build_compiled_skill_registry(
            workspace_path=workspace_path,
            touched_paths=normalized_touched_paths,
            invocation_request=skill_models.SkillInvocationRequest(
                workspace_path=workspace_path,
                touched_paths=normalized_touched_paths,
                session_id=session_id,
            ),
        )
        if normalized_touched_paths:
            return registry.activate_for_touched_paths(normalized_touched_paths)
        return registry.list_unconditional_skills()

    def find_compiled_skill_by_name_or_directory_name(
        self,
        name_or_slug: str,
        *,
        arguments: dict[str, object] | None = None,
        workspace_path: str | None = None,
        touched_paths: list[str] | None = None,
        session_id: str | None = None,
    ) -> skill_models.CompiledSkill | None:
        normalized_touched_paths = self._normalize_touched_paths(
            touched_paths or [],
            workspace_path=workspace_path,
        )
        registry = self._build_compiled_skill_registry(
            workspace_path=workspace_path,
            touched_paths=normalized_touched_paths,
            invocation_request=skill_models.SkillInvocationRequest(
                arguments=dict(arguments or {}),
                workspace_path=workspace_path,
                touched_paths=normalized_touched_paths,
                session_id=session_id,
            ),
        )
        return registry.get_by_token(name_or_slug)

    def _find_skill_record_by_identifier(
        self,
        identifier: str,
        *,
        loaded_only: bool,
    ) -> SkillRecord | None:
        normalized_identifier = identifier.strip()
        if not normalized_identifier:
            return None

        records = self._list_visible_skill_records()
        if loaded_only:
            records = [
                record
                for record in records
                if record.status == SkillRecordStatus.LOADED and record.enabled
            ]

        for record in records:
            if record.id == normalized_identifier:
                return record

        normalized_casefold = normalized_identifier.casefold()
        for field_name in ("directory_name", "name"):
            for record in records:
                value = getattr(record, field_name, None)
                if isinstance(value, str) and value.casefold() == normalized_casefold:
                    return record
        return None

    def _list_visible_skill_records(self) -> list[SkillRecord]:
        supported_root_keys = self._supported_root_keys()
        if not supported_root_keys:
            return []

        return [
            record
            for record in self._repository.list_skills()
            if self._is_record_root_supported(record, supported_root_keys)
            and record.status != SkillRecordStatus.IGNORED
        ]

    def _get_visible_skill_record(self, skill_id: str) -> SkillRecord | None:
        for record in self._list_visible_skill_records():
            if record.id == skill_id:
                return record
        return None

    def _supported_root_keys(self) -> set[tuple[object, object, str]]:
        roots = self._resolve_scan_roots()
        roots.extend(compatibility_skill_scan_placeholders())
        return {
            (
                scan_root.source,
                scan_root.scope,
                self._normalize_path(scan_root.root_dir),
            )
            for scan_root in roots
        }

    def _is_record_root_supported(
        self,
        record: SkillRecord,
        supported_root_keys: set[tuple[object, object, str]],
    ) -> bool:
        normalized_root = self._normalize_path(record.root_dir)
        for source, scope, supported_root in supported_root_keys:
            if record.source != source or record.scope != scope:
                continue
            if normalized_root == supported_root:
                return True
            if supported_root.startswith("mcp://") and normalized_root.startswith(
                f"{supported_root}/"
            ):
                return True
        return False

    @staticmethod
    def _normalize_path(path_value: str) -> str:
        if "://" in path_value:
            return path_value.strip().casefold()
        return Path(path_value).resolve(strict=False).as_posix().casefold()

    def _build_skill_record_read(self, record: SkillRecord) -> SkillRecordRead:
        base_record = to_skill_record_read(record)
        payload = base_record.model_dump(mode="python", by_alias=True)
        payload["metadata"] = dict(record.metadata_json)
        payload.update(self._skill_record_extras(record))
        return SkillRecordRead.model_validate(payload)

    def _build_skill_content(self, record: SkillRecord) -> SkillContentRead:
        compat_metadata = self._compat_metadata(record)
        compiled_skill = None
        prepared_invocation: dict[str, object] | None = None
        if record.status == SkillRecordStatus.LOADED and record.enabled:
            compiled_skill = self._compile_skill_record(record)
            prepared_invocation = self._summarize_prepared_invocation(
                compiled_skill.prepared_invocation
            )
        return SkillContentRead(
            id=record.id,
            name=record.name,
            directory_name=record.directory_name,
            entry_file=record.entry_file,
            parameter_schema=dict(record.parameter_schema_json),
            source=record.source,
            scope=record.scope,
            source_kind=self._infer_source_kind(record).value,
            loaded_from=self._string_metadata_value(compat_metadata, "loaded_from")
            or record.entry_file,
            invocable=self._bool_metadata_value(compat_metadata, "invocable", default=True),
            conditional=bool(self._activation_paths(record)),
            active=record.status == SkillRecordStatus.LOADED and record.enabled,
            dynamic=self._bool_metadata_value(compat_metadata, "dynamic", default=False),
            when_to_use=self._string_skill_field(record, "when_to_use"),
            allowed_tools=self._string_list_skill_field(record, "allowed_tools"),
            context=self._string_skill_field(record, "context_hint"),
            agent=self._string_skill_field(record, "agent"),
            effort=self._string_skill_field(record, "effort"),
            version=self._string_metadata_value(compat_metadata, "version"),
            model_hint=self._string_metadata_value(compat_metadata, "model_hint"),
            verification_mode=self._string_metadata_value(compat_metadata, "verification_mode"),
            shell_profile=self._string_metadata_value(compat_metadata, "shell_profile"),
            trust_level=self._string_metadata_value(compat_metadata, "trust_level"),
            preflight_checks=self._preflight_checks(record),
            orchestration_role=self._string_metadata_value(
                compat_metadata,
                "orchestration_role",
            ),
            orchestration_hints=self._structured_metadata_value(
                compat_metadata,
                "orchestration_hints",
            ),
            fanout_group=self._string_metadata_value(compat_metadata, "fanout_group"),
            preferred_stage=self._string_metadata_value(compat_metadata, "preferred_stage"),
            context_strategy=self._string_metadata_value(compat_metadata, "context_strategy"),
            execution_policy=self._structured_metadata_value(compat_metadata, "execution_policy"),
            result_schema=self._structured_metadata_value(compat_metadata, "result_schema"),
            aliases=self._string_list_skill_field(record, "aliases"),
            paths=self._activation_paths(record),
            shell_enabled=self._bool_metadata_value(
                compat_metadata,
                "shell_enabled",
                default=self._infer_source_kind(record) is not skill_models.SkillSourceKind.MCP,
            ),
            prepared_invocation=prepared_invocation,
            resolved_identity=self._resolved_identity_payload_for_record(record),
            discovery_provenance=(
                self._discovery_provenance_for_record(record)
                if compiled_skill is None
                else dict(compiled_skill.discovery_provenance)
            ),
            content=self._read_skill_entry_file(record),
        )

    def _read_skill_entry_file(self, record_or_entry_file: SkillRecord | str) -> str:
        if isinstance(record_or_entry_file, SkillRecord):
            record = record_or_entry_file
            if self._infer_source_kind(record) is skill_models.SkillSourceKind.MCP:
                mcp_bridge = import_module("app.compat.skills.mcp_bridge")
                return cast(str, mcp_bridge.read_mcp_skill_markdown(record))
            entry_file = (
                self._string_metadata_value(self._compat_metadata(record), "loaded_from")
                or record.entry_file
            )
        else:
            record = None
            entry_file = record_or_entry_file
        try:
            return self._cache.read_entry_content(entry_file, read_skill_markdown)
        except OSError as exc:
            entry_path = Path(entry_file)
            raise SkillContentReadError(
                f"Failed to read skill content from '{entry_path.as_posix()}'."
            ) from exc

    def _compile_skill_record(
        self,
        record: SkillRecord,
        *,
        invocation_request: skill_models.SkillInvocationRequest | None = None,
    ) -> skill_models.CompiledSkill:
        source_kind = self._infer_source_kind(record)
        cache_key = build_compiled_skill_cache_key(
            record=record,
            source_kind=source_kind.value,
            relative_path=self._relative_path_for_record(record, source_kind),
            invocation_request=invocation_request,
        )
        compiler_module = import_module("app.compat.skills.compiler")
        registry_module = import_module("app.compat.skills.registry")
        compiled_skill = self._cache.get_or_compile_skill(
            cache_key,
            lambda: cast(
                skill_models.CompiledSkill,
                compiler_module.compile_skill_record(
                    record,
                    self._read_skill_entry_file(record),
                    invocation_request=invocation_request,
                ),
            ),
        )
        registry = cast(_CompiledSkillRegistryProtocol, registry_module.CompiledSkillRegistry())
        registry.register(compiled_skill)
        return compiled_skill

    def _build_compiled_skill_registry(
        self,
        *,
        workspace_path: str | None = None,
        touched_paths: list[str] | None = None,
        invocation_request: skill_models.SkillInvocationRequest | None = None,
    ) -> _CompiledSkillRegistryProtocol:
        registry_module = import_module("app.compat.skills.registry")
        registry = cast(_CompiledSkillRegistryProtocol, registry_module.CompiledSkillRegistry())
        for record in self._list_visible_skill_records():
            if record.status != SkillRecordStatus.LOADED or not record.enabled:
                continue
            registry.register(
                self._compile_skill_record(record, invocation_request=invocation_request)
            )

        discovery_paths = self._discovery_paths(
            workspace_path=workspace_path, touched_paths=touched_paths
        )
        if not discovery_paths:
            return registry

        supported_roots = {
            self._normalize_path(record.root_dir) for record in self._list_visible_skill_records()
        }
        dynamic_roots = [
            root
            for root in self._resolve_scan_roots(discovery_paths=discovery_paths)
            if root.source == CompatibilitySource.CLAUDE
        ]
        for discovered_file in scan_skill_files(dynamic_roots):
            if self._normalize_path(discovered_file.root_dir) in supported_roots:
                continue
            parsed_record = parse_skill_file(discovered_file)
            if parsed_record.status != SkillRecordStatus.LOADED or not parsed_record.enabled:
                continue
            transient_record = self._to_skill_record(parsed_record)
            registry.register(
                self._compile_skill_record(transient_record, invocation_request=invocation_request)
            )
        return registry

    @staticmethod
    def _to_skill_record(parsed: skill_models.ParsedSkillRecordData) -> SkillRecord:
        raw_frontmatter = {
            key: value for key, value in parsed.raw_frontmatter.items() if key != "_compat"
        }
        compat_payload = {
            "source_kind": (
                parsed.source_identity.source_kind.value
                if parsed.source_identity is not None
                else skill_models.SkillSourceKind.FILESYSTEM.value
            ),
            "activation_paths": list(parsed.activation_paths),
            "dynamic": parsed.source_identity is not None
            and parsed.source_identity.source_kind is skill_models.SkillSourceKind.MCP,
            "invocable": (
                False
                if parsed.source_identity is not None
                and parsed.source_identity.source_kind is skill_models.SkillSourceKind.MCP
                else True
            ),
            "shell_enabled": not (
                parsed.source_identity is not None
                and parsed.source_identity.source_kind is skill_models.SkillSourceKind.MCP
            ),
            "loaded_from": parsed.metadata.get("loaded_from", parsed.entry_file),
            "when_to_use": parsed.when_to_use,
            "context_hint": parsed.context_hint,
            "agent": parsed.agent,
            "effort": parsed.effort,
            "version": parsed.version,
            "model_hint": parsed.model_hint,
            "verification_mode": (
                None if parsed.trust_metadata is None else parsed.trust_metadata.verification_mode
            ),
            "shell_profile": (
                None if parsed.trust_metadata is None else parsed.trust_metadata.shell_profile
            ),
            "trust_level": (
                None if parsed.trust_metadata is None else parsed.trust_metadata.trust_level
            ),
            "preflight_checks": [check.to_payload() for check in parsed.preflight_checks],
            "orchestration_role": parsed.orchestration_role,
            "orchestration_hints": (
                None
                if parsed.orchestration_hints is None
                else parsed.orchestration_hints.to_payload()
            ),
            "fanout_group": parsed.fanout_group,
            "preferred_stage": parsed.preferred_stage,
            "context_strategy": parsed.context_strategy,
            "execution_policy": (
                None if parsed.execution_policy is None else parsed.execution_policy.to_payload()
            ),
            "result_schema": (
                None if parsed.result_schema is None else parsed.result_schema.to_payload()
            ),
            "semantic_family": parsed.semantic_family,
            "semantic_domain": parsed.semantic_domain,
            "semantic_task_mode": parsed.semantic_task_mode,
            "semantic_tags": list(parsed.semantic_tags),
            "root_label": parsed.root_label,
            "discovery_provenance": dict(
                parsed.discovery_provenance
                or (
                    {}
                    if parsed.source_identity is None
                    else parsed.source_identity.discovery_provenance
                )
            ),
        }
        raw_frontmatter["_compat"] = compat_payload
        return SkillRecord(
            id=parsed.id,
            source=parsed.source,
            scope=parsed.scope,
            root_dir=parsed.root_dir,
            directory_name=parsed.directory_name,
            entry_file=parsed.entry_file,
            name=parsed.name,
            description=parsed.description,
            compatibility_json=parsed.compatibility,
            metadata_json=parsed.metadata,
            parameter_schema_json=parsed.parameter_schema,
            raw_frontmatter_json=raw_frontmatter,
            status=parsed.status,
            enabled=parsed.enabled,
            error_message=parsed.error_message,
            content_hash=parsed.content_hash,
            last_scanned_at=parsed.last_scanned_at,
        )

    def _build_transient_skill_content(
        self,
        compiled_skill: skill_models.CompiledSkill,
    ) -> SkillContentRead:
        return SkillContentRead(
            id=compiled_skill.skill_id,
            name=compiled_skill.name,
            directory_name=compiled_skill.directory_name,
            entry_file=compiled_skill.entry_file,
            parameter_schema=dict(compiled_skill.parameter_schema),
            source=compiled_skill.identity.source,
            scope=compiled_skill.identity.scope,
            source_kind=compiled_skill.identity.source_kind.value,
            loaded_from=compiled_skill.loaded_from or compiled_skill.entry_file,
            invocable=compiled_skill.invocable,
            conditional=compiled_skill.is_conditional,
            active=True,
            dynamic=compiled_skill.dynamic,
            when_to_use=compiled_skill.when_to_use,
            allowed_tools=list(compiled_skill.allowed_tools),
            context=compiled_skill.context_hint,
            agent=compiled_skill.agent,
            effort=compiled_skill.effort,
            version=compiled_skill.version,
            model_hint=compiled_skill.model_hint,
            verification_mode=(
                None
                if compiled_skill.trust_metadata is None
                else compiled_skill.trust_metadata.verification_mode
            ),
            shell_profile=(
                None
                if compiled_skill.trust_metadata is None
                else compiled_skill.trust_metadata.shell_profile
            ),
            trust_level=(
                None
                if compiled_skill.trust_metadata is None
                else compiled_skill.trust_metadata.trust_level
            ),
            preflight_checks=[check.to_payload() for check in compiled_skill.preflight_checks],
            orchestration_role=compiled_skill.orchestration_role,
            orchestration_hints=(
                None
                if compiled_skill.orchestration_hints is None
                else compiled_skill.orchestration_hints.to_payload()
            ),
            fanout_group=compiled_skill.fanout_group,
            preferred_stage=compiled_skill.preferred_stage,
            context_strategy=compiled_skill.context_strategy,
            execution_policy=(
                None
                if compiled_skill.execution_policy is None
                else compiled_skill.execution_policy.to_payload()
            ),
            result_schema=(
                None
                if compiled_skill.result_schema is None
                else compiled_skill.result_schema.to_payload()
            ),
            aliases=list(compiled_skill.aliases),
            paths=list(compiled_skill.activation_paths),
            shell_enabled=compiled_skill.shell_enabled,
            prepared_invocation=self._summarize_prepared_invocation(
                compiled_skill.prepared_invocation
            ),
            resolved_identity=self._resolved_identity_payload(compiled_skill),
            discovery_provenance=dict(compiled_skill.discovery_provenance),
            content=(
                import_module("app.compat.skills.mcp_bridge").read_mcp_skill_markdown(
                    compiled_skill
                )
                if compiled_skill.identity.source_kind is skill_models.SkillSourceKind.MCP
                else read_skill_markdown(compiled_skill.entry_file)
            ),
        )

    @staticmethod
    def _resolved_identity_payload(
        compiled_skill: skill_models.CompiledSkill,
    ) -> dict[str, object]:
        return {
            "source": compiled_skill.identity.source.value,
            "scope": compiled_skill.identity.scope.value,
            "source_kind": compiled_skill.identity.source_kind.value,
            "source_root": compiled_skill.identity.source_root,
            "relative_path": compiled_skill.identity.relative_path,
            "fingerprint": compiled_skill.identity.fingerprint,
        }

    @staticmethod
    def _summarize_prepared_invocation(
        prepared_invocation: skill_models.PreparedSkillInvocation | None,
    ) -> dict[str, object] | None:
        if prepared_invocation is None:
            return None
        return {
            "request": {
                "arguments": dict(prepared_invocation.request.arguments),
                "workspace_path": prepared_invocation.request.workspace_path,
                "touched_paths": list(prepared_invocation.request.touched_paths),
                "session_id": prepared_invocation.request.session_id,
            },
            "context": {
                "skill_directory": prepared_invocation.context.skill_directory,
                "shell_enabled": prepared_invocation.context.shell_enabled,
                "session_id": prepared_invocation.context.session_id,
                "substitution_values": dict(prepared_invocation.context.substitution_values),
            },
            "shell_expansion_count": len(prepared_invocation.shell_expansions),
            "pending_action_count": len(prepared_invocation.pending_actions),
            "shell_expansions": [
                item.to_payload() for item in prepared_invocation.shell_expansions
            ],
            "pending_actions": [item.to_payload() for item in prepared_invocation.pending_actions],
        }

    def _compiled_skill_payload(
        self,
        compiled_skill: skill_models.CompiledSkill,
        *,
        active_due_to_touched_paths: bool,
        selected: bool,
        role: str | None,
        prepared_for_context: bool,
        prepared_for_execution: bool,
    ) -> dict[str, object]:
        return {
            "id": compiled_skill.skill_id,
            "name": compiled_skill.name,
            "directory_name": compiled_skill.directory_name,
            "description": compiled_skill.description,
            "source": compiled_skill.identity.source.value,
            "scope": compiled_skill.identity.scope.value,
            "source_kind": compiled_skill.identity.source_kind.value,
            "loaded_from": compiled_skill.loaded_from or compiled_skill.entry_file,
            "entry_file": compiled_skill.entry_file,
            "compatibility": list(compiled_skill.compatibility),
            "parameter_schema": dict(compiled_skill.parameter_schema),
            "invocable": compiled_skill.invocable,
            "user_invocable": compiled_skill.user_invocable,
            "conditional": compiled_skill.is_conditional,
            "active": True,
            "dynamic": compiled_skill.dynamic,
            "paths": list(compiled_skill.activation_paths),
            "aliases": list(compiled_skill.aliases),
            "when_to_use": compiled_skill.when_to_use,
            "allowed_tools": list(compiled_skill.allowed_tools),
            "context": compiled_skill.context_hint,
            "agent": compiled_skill.agent,
            "effort": compiled_skill.effort,
            "version": compiled_skill.version,
            "model_hint": compiled_skill.model_hint,
            "verification_mode": (
                None
                if compiled_skill.trust_metadata is None
                else compiled_skill.trust_metadata.verification_mode
            ),
            "shell_profile": (
                None
                if compiled_skill.trust_metadata is None
                else compiled_skill.trust_metadata.shell_profile
            ),
            "trust_level": (
                None
                if compiled_skill.trust_metadata is None
                else compiled_skill.trust_metadata.trust_level
            ),
            "preflight_checks": [check.to_payload() for check in compiled_skill.preflight_checks],
            "orchestration_role": compiled_skill.orchestration_role,
            "orchestration_hints": (
                None
                if compiled_skill.orchestration_hints is None
                else compiled_skill.orchestration_hints.to_payload()
            ),
            "fanout_group": compiled_skill.fanout_group,
            "preferred_stage": compiled_skill.preferred_stage,
            "context_strategy": compiled_skill.context_strategy,
            "execution_policy": (
                None
                if compiled_skill.execution_policy is None
                else compiled_skill.execution_policy.to_payload()
            ),
            "result_schema": (
                None
                if compiled_skill.result_schema is None
                else compiled_skill.result_schema.to_payload()
            ),
            "family": compiled_skill.semantic_family,
            "domain": compiled_skill.semantic_domain,
            "task_mode": compiled_skill.semantic_task_mode,
            "tags": list(compiled_skill.semantic_tags),
            "argument_hint": compiled_skill.argument_hint,
            "shell_enabled": compiled_skill.shell_enabled,
            "execution_mode": compiled_skill.execution_mode.value,
            "prepared_invocation": self._summarize_prepared_invocation(
                compiled_skill.prepared_invocation
            ),
            "prepared_for_context": prepared_for_context,
            "prepared_for_execution": prepared_for_execution,
            "resolved_identity": self._resolved_identity_payload(compiled_skill),
            "discovery_provenance": dict(compiled_skill.discovery_provenance),
            "active_due_to_touched_paths": active_due_to_touched_paths,
            "selected": selected,
            "role": role,
        }

    def _resolved_skill_candidate_payload(
        self,
        candidate: skill_models.ResolvedSkillCandidate,
        *,
        touched_paths: list[str] | None = None,
    ) -> dict[str, object]:
        payload = self._compiled_skill_payload(
            candidate.compiled_skill,
            active_due_to_touched_paths=bool(touched_paths)
            and candidate.compiled_skill.is_conditional,
            selected=candidate.selected,
            role=None if candidate.role is None else candidate.role.value,
            prepared_for_context=False,
            prepared_for_execution=False,
        )
        payload.update(
            {
                "rank": candidate.rank,
                "total_score": candidate.total_score,
                "score_breakdown": candidate.score_breakdown.to_payload(),
                "reasons": list(candidate.reasons),
                "selection_explanation": dict(candidate.selection_explanation),
                "packing_explanation": dict(candidate.packing_explanation),
                "rejected_reason": candidate.rejected_reason,
            }
        )
        return payload

    def _selected_candidate_from_resolution(
        self,
        resolution_result: skill_models.SkillResolutionResult,
    ) -> skill_models.ResolvedSkillCandidate | None:
        return resolution_result.primary_candidate

    def _best_skill_payload_from_resolution(
        self,
        resolution_result: skill_models.SkillResolutionResult,
        *,
        touched_paths: list[str] | None,
        include_reference_only: bool,
    ) -> dict[str, object]:
        skill_set_plan = self.build_skill_set_plan(
            resolution_result,
            workflow_stage=resolution_result.request.workflow_stage,
            agent_role=resolution_result.request.agent_role,
        )
        selected_candidate = self._selected_candidate_from_resolution(resolution_result)
        if selected_candidate is None:
            reference_only_rejected = any(
                candidate.rejected_reason == "reference_only_excluded"
                for candidate in resolution_result.rejected_candidates
            )
            status = (
                "reference_only_only"
                if not include_reference_only
                and (resolution_result.reference_candidates or reference_only_rejected)
                else "no_match"
            )
            selected_skill = None
            selected_skill_id = None
            selected_skill_rank = None
        else:
            status = "selected"
            selected_skill = self._resolved_skill_candidate_payload(
                selected_candidate,
                touched_paths=touched_paths,
            )
            selected_skill_id = selected_candidate.compiled_skill.skill_id
            selected_skill_rank = selected_candidate.rank

        primary_skill = (
            None
            if skill_set_plan.primary_candidate is None
            else self._resolved_skill_candidate_payload(
                skill_set_plan.primary_candidate,
                touched_paths=touched_paths,
            )
        )
        supporting_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in skill_set_plan.supporting_candidates
        ]
        reference_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in skill_set_plan.reference_candidates
        ]
        rejected_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in resolution_result.rejected_candidates
        ]
        pruned_supporting_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in skill_set_plan.pruned_supporting_candidates
        ]
        pruned_reference_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in skill_set_plan.pruned_reference_candidates
        ]
        selected_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in skill_set_plan.selected_candidates
        ]
        selected_skill_ids = list(skill_set_plan.selected_skill_ids)
        skill_runtime_usage = self.build_skill_runtime_usage_records(
            skill_set_plan,
            resolution_result=resolution_result,
        )
        skill_orchestration_plan = self.build_skill_orchestration_plan(
            skill_set_plan,
            resolution_result=resolution_result,
        )
        suppressed_skills = [
            self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
            for candidate in skill_set_plan.suppressed_candidates
        ]
        suppression_reasons = {
            candidate.compiled_skill.skill_id: list(candidate.reasons)
            for candidate in skill_set_plan.suppressed_candidates
        }
        skill_budget = self.determine_skill_budget(
            workflow_stage=resolution_result.request.workflow_stage,
            agent_role=resolution_result.request.agent_role,
        )

        resolution_payload = resolution_result.to_payload(
            payload_builder=lambda candidate: self._compiled_skill_payload(
                candidate.compiled_skill,
                active_due_to_touched_paths=bool(touched_paths)
                and candidate.compiled_skill.is_conditional,
                selected=candidate.selected,
                role=None if candidate.role is None else candidate.role.value,
                prepared_for_context=False,
                prepared_for_execution=False,
            )
        )
        return {
            "status": status,
            "selected_skill": selected_skill,
            "selected_skill_id": selected_skill_id,
            "selected_skill_rank": selected_skill_rank,
            "primary_skill": primary_skill,
            "supporting_skills": supporting_skills,
            "reference_skills": reference_skills,
            "rejected_skills": rejected_skills,
            "pruned_supporting_skills": pruned_supporting_skills,
            "pruned_reference_skills": pruned_reference_skills,
            "selected_skills": selected_skills,
            "selected_skill_ids": selected_skill_ids,
            "skill_budget": skill_budget.to_payload(),
            "skill_set_plan": skill_set_plan.to_payload(
                payload_builder=self._resolved_skill_candidate_payload,
                touched_paths=touched_paths,
            ),
            "skill_orchestration_plan": skill_orchestration_plan,
            "skill_runtime_usage": skill_runtime_usage,
            "intent_profile": (
                None
                if resolution_result.intent_profile is None
                else resolution_result.intent_profile.to_payload()
            ),
            "suppressed_skills": suppressed_skills,
            "suppression_reasons": suppression_reasons,
            "resolution_request": resolution_result.request.to_payload(),
            "resolution_summary": {
                "active_candidate_count": resolution_result.active_candidate_count,
                "shortlisted_count": len(resolution_result.shortlisted_candidates),
                "selected_count": len(skill_set_plan.selected_candidates),
                "primary_skill_id": selected_skill_id,
                "supporting_count": len(skill_set_plan.supporting_candidates),
                "reference_count": len(skill_set_plan.reference_candidates),
                "rejected_count": len(resolution_result.rejected_candidates),
                "selected_skill_id": selected_skill_id,
                "pruning_applied": skill_set_plan.pruning_applied,
            },
            "resolution": resolution_payload,
        }

    def _discovery_paths(
        self,
        *,
        workspace_path: str | None,
        touched_paths: list[str] | None,
    ) -> list[str]:
        paths: list[str] = []
        if workspace_path:
            paths.append(workspace_path)
        paths.extend(touched_paths or [])
        return [path for path in paths if path and path.strip()]

    @staticmethod
    def _normalize_touched_paths(
        touched_paths: list[str], *, workspace_path: str | None
    ) -> list[str]:
        normalized: list[str] = []
        workspace_root = (
            None
            if workspace_path is None
            else Path(workspace_path).expanduser().resolve(strict=False)
        )
        for touched_path in touched_paths:
            stripped_path = touched_path.strip()
            if not stripped_path:
                continue
            resolved_path = Path(stripped_path).expanduser().resolve(strict=False)
            normalized.append(resolved_path.as_posix())
            if workspace_root is not None:
                try:
                    normalized.append(resolved_path.relative_to(workspace_root).as_posix())
                except ValueError:
                    pass
            normalized.append(stripped_path.replace("\\", "/"))

        deduped: list[str] = []
        seen: set[str] = set()
        for item in normalized:
            normalized_item = item.casefold()
            if normalized_item in seen:
                continue
            seen.add(normalized_item)
            deduped.append(item)
        return deduped

    def _scan_mcp_capability_records(self) -> list[skill_models.ParsedSkillRecordData]:
        servers = self._mcp_repository.list_servers()
        capabilities_by_server_id = {
            server.id: self._mcp_repository.list_capabilities(server.id) for server in servers
        }
        mcp_bridge = import_module("app.compat.skills.mcp_bridge")
        return cast(
            list[skill_models.ParsedSkillRecordData],
            mcp_bridge.build_mcp_skill_records(
                servers=servers,
                capabilities_by_server_id=capabilities_by_server_id,
            ),
        )

    def _skill_record_extras(self, record: SkillRecord) -> dict[str, object]:
        compat_metadata = self._compat_metadata(record)
        source_kind = self._infer_source_kind(record)
        payload: dict[str, object] = {
            "source_kind": source_kind.value,
            "loaded_from": self._string_metadata_value(compat_metadata, "loaded_from")
            or record.entry_file,
            "invocable": self._bool_metadata_value(compat_metadata, "invocable", default=True),
            "conditional": bool(self._activation_paths(record)),
            "active": record.status == SkillRecordStatus.LOADED and record.enabled,
            "dynamic": self._bool_metadata_value(compat_metadata, "dynamic", default=False),
            "when_to_use": self._string_skill_field(record, "when_to_use"),
            "allowed_tools": self._string_list_skill_field(record, "allowed_tools"),
            "context": self._string_skill_field(record, "context_hint"),
            "agent": self._string_skill_field(record, "agent"),
            "effort": self._string_skill_field(record, "effort"),
            "version": self._string_metadata_value(compat_metadata, "version"),
            "model_hint": self._string_metadata_value(compat_metadata, "model_hint"),
            "verification_mode": self._string_metadata_value(compat_metadata, "verification_mode"),
            "shell_profile": self._string_metadata_value(compat_metadata, "shell_profile"),
            "trust_level": self._string_metadata_value(compat_metadata, "trust_level"),
            "preflight_checks": self._preflight_checks(record),
            "orchestration_role": self._string_metadata_value(
                compat_metadata,
                "orchestration_role",
            ),
            "orchestration_hints": self._structured_metadata_value(
                compat_metadata,
                "orchestration_hints",
            ),
            "fanout_group": self._string_metadata_value(compat_metadata, "fanout_group"),
            "preferred_stage": self._string_metadata_value(compat_metadata, "preferred_stage"),
            "context_strategy": self._string_metadata_value(compat_metadata, "context_strategy"),
            "execution_policy": self._structured_metadata_value(
                compat_metadata, "execution_policy"
            ),
            "result_schema": self._structured_metadata_value(compat_metadata, "result_schema"),
            "family": self._string_skill_field(record, "semantic_family"),
            "domain": self._string_skill_field(record, "semantic_domain"),
            "task_mode": self._string_skill_field(record, "semantic_task_mode"),
            "tags": self._string_list_skill_field(record, "semantic_tags"),
            "aliases": self._string_list_skill_field(record, "aliases"),
            "paths": self._activation_paths(record),
            "shell_enabled": self._bool_metadata_value(
                compat_metadata,
                "shell_enabled",
                default=source_kind is not skill_models.SkillSourceKind.MCP,
            ),
            "resolved_identity": self._resolved_identity_payload_for_record(record),
            "discovery_provenance": self._discovery_provenance_for_record(record),
            "raw_frontmatter": self._visible_raw_frontmatter(record),
        }
        if record.status == SkillRecordStatus.LOADED and record.enabled:
            compiled = self._compile_skill_record(record)
            payload["prepared_invocation"] = self._summarize_prepared_invocation(
                compiled.prepared_invocation
            )
        else:
            payload["prepared_invocation"] = None
        return payload

    @staticmethod
    def _activation_paths(record: SkillRecord) -> list[str]:
        compat_payload = record.raw_frontmatter_json.get("_compat")
        if isinstance(compat_payload, dict):
            raw_paths = compat_payload.get("activation_paths")
            if isinstance(raw_paths, list):
                return [item for item in raw_paths if isinstance(item, str)]
        return []

    @staticmethod
    def _compat_metadata(record: SkillRecord) -> dict[str, object]:
        compat_payload = record.raw_frontmatter_json.get("_compat")
        return dict(compat_payload) if isinstance(compat_payload, dict) else {}

    @staticmethod
    def _visible_raw_frontmatter(record: SkillRecord) -> dict[str, object]:
        return {
            key: value for key, value in record.raw_frontmatter_json.items() if key != "_compat"
        }

    def _resolved_identity_payload_for_record(self, record: SkillRecord) -> dict[str, object]:
        source_kind = self._infer_source_kind(record)
        relative_path = self._relative_path_for_record(record, source_kind)
        compat_metadata = self._compat_metadata(record)
        return {
            "source": record.source.value,
            "scope": record.scope.value,
            "source_kind": source_kind.value,
            "source_root": record.root_dir,
            "relative_path": relative_path,
            "fingerprint": record.content_hash,
            "loaded_from": self._string_metadata_value(compat_metadata, "loaded_from")
            or record.entry_file,
        }

    def _relative_path_for_record(
        self, record: SkillRecord, source_kind: skill_models.SkillSourceKind
    ) -> str:
        if source_kind is skill_models.SkillSourceKind.MCP:
            normalized_root = record.root_dir.rstrip("/")
            if record.entry_file.startswith(normalized_root):
                return record.entry_file.removeprefix(normalized_root).lstrip("/")
            return record.entry_file
        entry_path = Path(record.entry_file)
        root_path = Path(record.root_dir)
        try:
            return entry_path.resolve().relative_to(root_path.resolve()).as_posix()
        except ValueError:
            return entry_path.name

    def _infer_source_kind(self, record: SkillRecord) -> skill_models.SkillSourceKind:
        compiler_module = import_module("app.compat.skills.compiler")
        return cast(skill_models.SkillSourceKind, compiler_module.infer_skill_source_kind(record))

    @staticmethod
    def _string_list_skill_field(record: SkillRecord, field_name: str) -> list[str]:
        compiler_module = import_module("app.compat.skills.compiler")
        content = (
            import_module("app.compat.skills.mcp_bridge").read_mcp_skill_markdown(record)
            if SkillService._compat_metadata(record).get("source_kind")
            == skill_models.SkillSourceKind.MCP.value
            else read_skill_markdown(
                SkillService._string_metadata_value(
                    SkillService._compat_metadata(record), "loaded_from"
                )
                or record.entry_file
            )
        )
        try:
            parsed_frontmatter = compiler_module.parse_skill_frontmatter(
                content,
                directory_name=record.directory_name,
            )
        except Exception:
            return []
        value = getattr(parsed_frontmatter, field_name, [])
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _string_skill_field(record: SkillRecord, field_name: str) -> str | None:
        compiler_module = import_module("app.compat.skills.compiler")
        content = (
            import_module("app.compat.skills.mcp_bridge").read_mcp_skill_markdown(record)
            if SkillService._compat_metadata(record).get("source_kind")
            == skill_models.SkillSourceKind.MCP.value
            else read_skill_markdown(
                SkillService._string_metadata_value(
                    SkillService._compat_metadata(record), "loaded_from"
                )
                or record.entry_file
            )
        )
        try:
            parsed_frontmatter = compiler_module.parse_skill_frontmatter(
                content,
                directory_name=record.directory_name,
            )
        except Exception:
            return None
        value = getattr(parsed_frontmatter, field_name, None)
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _string_metadata_value(payload: dict[str, object], key: str) -> str | None:
        value = payload.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _bool_metadata_value(payload: dict[str, object], key: str, *, default: bool) -> bool:
        value = payload.get(key)
        return value if isinstance(value, bool) else default

    @staticmethod
    def _structured_metadata_value(
        payload: dict[str, object], key: str
    ) -> dict[str, object] | None:
        value = payload.get(key)
        return dict(value) if isinstance(value, dict) else None

    @staticmethod
    def _discovery_provenance_for_record(record: SkillRecord) -> dict[str, object]:
        provenance = SkillService._compat_metadata(record).get("discovery_provenance")
        if not isinstance(provenance, dict):
            return {}
        sanitized = dict(provenance)
        sanitized.pop("canonical_root", None)
        sanitized.pop("canonical_entry_file", None)
        return sanitized

    @staticmethod
    def _preflight_checks(record: SkillRecord) -> list[dict[str, object]]:
        raw_checks = SkillService._compat_metadata(record).get("preflight_checks")
        if not isinstance(raw_checks, list):
            return []
        return [dict(item) for item in raw_checks if isinstance(item, dict)]


def resolve_skill_scan_roots(
    settings: Settings,
    *,
    discovery_paths: list[str] | None = None,
) -> list[skill_models.SkillScanRoot]:
    roots = default_skill_scan_roots(
        include_compatibility_roots=settings.skill_compatibility_scan_enabled,
        extra_dirs=settings.skill_extra_dirs,
    )
    if discovery_paths:
        roots.extend(discover_claude_skill_scan_roots(discovery_paths))

    deduped: dict[tuple[str, str, str, str], skill_models.SkillScanRoot] = {}
    for root in roots:
        deduped[
            build_root_cache_key(
                source=root.source.value,
                scope=root.scope.value,
                root_dir=root.root_dir,
                source_kind=root.source_kind.value,
            )
        ] = root
    return list(deduped.values())


def get_skill_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SkillService:
    return SkillService(db_session, settings)
