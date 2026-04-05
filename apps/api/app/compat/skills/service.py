from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.compat.skills import models as skill_models
from app.compat.skills.parser import parse_skill_file, read_skill_markdown
from app.compat.skills.scanner import (
    compatibility_skill_scan_placeholders,
    default_skill_scan_roots,
    discover_claude_skill_scan_roots,
    scan_skill_files,
)
from app.core.settings import Settings, get_settings
from app.db.models import (
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
        )
        summaries: list[SkillAgentSummaryRead] = []
        active_due_to_touched_paths = bool(touched_paths)
        for candidate in resolution_result.shortlisted_candidates:
            compiled_skill = candidate.compiled_skill
            prepared_invocation = self._summarize_prepared_invocation(
                compiled_skill.prepared_invocation
            )
            summaries.append(
                SkillAgentSummaryRead(
                    id=compiled_skill.skill_id,
                    name=compiled_skill.name,
                    directory_name=compiled_skill.directory_name,
                    description=compiled_skill.description,
                    compatibility=list(compiled_skill.compatibility),
                    entry_file=compiled_skill.entry_file,
                    source=compiled_skill.identity.source,
                    scope=compiled_skill.identity.scope,
                    source_kind=compiled_skill.identity.source_kind.value,
                    loaded_from=compiled_skill.loaded_from or compiled_skill.entry_file,
                    invocable=compiled_skill.invocable,
                    user_invocable=compiled_skill.user_invocable,
                    conditional=compiled_skill.is_conditional,
                    active=True,
                    dynamic=compiled_skill.dynamic,
                    paths=list(compiled_skill.activation_paths),
                    aliases=list(compiled_skill.aliases),
                    when_to_use=compiled_skill.when_to_use,
                    allowed_tools=list(compiled_skill.allowed_tools),
                    context=compiled_skill.context_hint,
                    agent=compiled_skill.agent,
                    effort=compiled_skill.effort,
                    argument_hint=compiled_skill.argument_hint,
                    shell_enabled=compiled_skill.shell_enabled,
                    execution_mode=compiled_skill.execution_mode.value,
                    resolved_identity=self._resolved_identity_payload(compiled_skill),
                    prepared_invocation=prepared_invocation,
                    active_due_to_touched_paths=(
                        active_due_to_touched_paths and compiled_skill.is_conditional
                    ),
                    rank=candidate.rank,
                    total_score=candidate.total_score,
                    score_breakdown=candidate.score_breakdown.to_payload(),
                    reasons=list(candidate.reasons),
                    selected=candidate.selected,
                    rejected_reason=candidate.rejected_reason,
                )
            )
        return summaries

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
        return [
            self._resolved_skill_candidate_payload(candidate)
            for candidate in resolution_result.shortlisted_candidates
        ]

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
            include_reference_only=True,
        )
        return {
            "skills": [
                self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
                for candidate in resolution_result.shortlisted_candidates
            ],
            "reference_skills": [
                self._resolved_skill_candidate_payload(candidate, touched_paths=touched_paths)
                for candidate in resolution_result.reference_candidates
            ],
            "resolution": resolution_result.to_payload(
                payload_builder=lambda candidate: self._compiled_skill_payload(
                    candidate.compiled_skill,
                    active_due_to_touched_paths=bool(touched_paths)
                    and candidate.compiled_skill.is_conditional,
                    selected=candidate.selected,
                )
            ),
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
        skills = payload.get("skills", [])
        reference_skills = payload.get("reference_skills", [])
        if (not isinstance(skills, list) or not skills) and (
            not isinstance(reference_skills, list) or not reference_skills
        ):
            return "No loaded skills are currently available."
        lines = [
            self.build_ranked_skill_context_prompt_fragment(
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
        ]
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

    def rescan_skills(self) -> list[SkillRecordRead]:
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
        discovered_files = scan_skill_files(resolve_skill_scan_roots(self._settings))
        parsed_records = [parse_skill_file(discovered_file) for discovered_file in discovered_files]
        parsed_records.extend(self._scan_mcp_capability_records())
        return parsed_records

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
        roots = resolve_skill_scan_roots(self._settings)
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
        prepared_invocation: dict[str, object] | None = None
        if record.status == SkillRecordStatus.LOADED and record.enabled:
            prepared_invocation = self._summarize_prepared_invocation(
                self._compile_skill_record(record).prepared_invocation
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
            aliases=self._string_list_skill_field(record, "aliases"),
            paths=self._activation_paths(record),
            shell_enabled=self._bool_metadata_value(
                compat_metadata,
                "shell_enabled",
                default=self._infer_source_kind(record) is not skill_models.SkillSourceKind.MCP,
            ),
            prepared_invocation=prepared_invocation,
            resolved_identity=self._resolved_identity_payload_for_record(record),
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
        entry_path = Path(entry_file)
        try:
            return read_skill_markdown(str(entry_path))
        except OSError as exc:
            raise SkillContentReadError(
                f"Failed to read skill content from '{entry_path.as_posix()}'."
            ) from exc

    def _compile_skill_record(self, record: SkillRecord) -> skill_models.CompiledSkill:
        content = self._read_skill_entry_file(record)
        compiler_module = import_module("app.compat.skills.compiler")
        registry_module = import_module("app.compat.skills.registry")
        compiled_skill = cast(
            skill_models.CompiledSkill,
            compiler_module.compile_skill_record(record, content),
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
        compiler_module = import_module("app.compat.skills.compiler")
        registry_module = import_module("app.compat.skills.registry")
        registry = cast(_CompiledSkillRegistryProtocol, registry_module.CompiledSkillRegistry())
        for record in self._list_visible_skill_records():
            if record.status != SkillRecordStatus.LOADED or not record.enabled:
                continue
            registry.register(
                cast(
                    skill_models.CompiledSkill,
                    compiler_module.compile_skill_record(
                        record,
                        self._read_skill_entry_file(record),
                        invocation_request=invocation_request,
                    ),
                )
            )

        discovery_paths = self._discovery_paths(
            workspace_path=workspace_path, touched_paths=touched_paths
        )
        if not discovery_paths:
            return registry

        supported_roots = {
            self._normalize_path(record.root_dir) for record in self._list_visible_skill_records()
        }
        for discovered_file in scan_skill_files(discover_claude_skill_scan_roots(discovery_paths)):
            if self._normalize_path(discovered_file.root_dir) in supported_roots:
                continue
            parsed_record = parse_skill_file(discovered_file)
            if parsed_record.status != SkillRecordStatus.LOADED or not parsed_record.enabled:
                continue
            transient_record = self._to_skill_record(parsed_record)
            registry.register(
                cast(
                    skill_models.CompiledSkill,
                    compiler_module.compile_skill_record(
                        transient_record,
                        self._read_skill_entry_file(transient_record),
                        invocation_request=invocation_request,
                    ),
                )
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
            aliases=list(compiled_skill.aliases),
            paths=list(compiled_skill.activation_paths),
            shell_enabled=compiled_skill.shell_enabled,
            prepared_invocation=self._summarize_prepared_invocation(
                compiled_skill.prepared_invocation
            ),
            resolved_identity=self._resolved_identity_payload(compiled_skill),
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
            "argument_hint": compiled_skill.argument_hint,
            "shell_enabled": compiled_skill.shell_enabled,
            "execution_mode": compiled_skill.execution_mode.value,
            "prepared_invocation": self._summarize_prepared_invocation(
                compiled_skill.prepared_invocation
            ),
            "resolved_identity": self._resolved_identity_payload(compiled_skill),
            "active_due_to_touched_paths": active_due_to_touched_paths,
            "selected": selected,
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
        )
        payload.update(
            {
                "rank": candidate.rank,
                "total_score": candidate.total_score,
                "score_breakdown": candidate.score_breakdown.to_payload(),
                "reasons": list(candidate.reasons),
                "rejected_reason": candidate.rejected_reason,
            }
        )
        return payload

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
            "aliases": self._string_list_skill_field(record, "aliases"),
            "paths": self._activation_paths(record),
            "shell_enabled": self._bool_metadata_value(
                compat_metadata,
                "shell_enabled",
                default=source_kind is not skill_models.SkillSourceKind.MCP,
            ),
            "resolved_identity": self._resolved_identity_payload_for_record(record),
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

    deduped: dict[tuple[object, object, str], skill_models.SkillScanRoot] = {}
    for root in roots:
        deduped[
            (
                root.source,
                root.scope,
                (
                    root.root_dir.strip().casefold()
                    if "://" in root.root_dir
                    else Path(root.root_dir).resolve(strict=False).as_posix().casefold()
                ),
            )
        ] = root
    return list(deduped.values())


def get_skill_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SkillService:
    return SkillService(db_session, settings)
