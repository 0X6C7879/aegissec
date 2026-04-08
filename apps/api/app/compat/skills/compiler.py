from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.compat.skills import models as skill_models
from app.compat.skills.discovery_cache import build_discovery_provenance, canonicalize_skill_path
from app.compat.skills.intent_routing import infer_skill_semantics
from app.compat.skills.parser import parse_skill_frontmatter
from app.compat.skills.trust import SkillTrustMetadata, resolve_effective_trust_level
from app.db.models import SkillRecord

INLINE_SHELL_RE = re.compile(r"^(?P<indent>\s*)!(?P<command>\S.*)$")
FENCED_SHELL_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})\s*!(?P<info>.*)$")
SUBSTITUTION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def compile_skill_record(
    record: SkillRecord,
    content: str,
    invocation_request: skill_models.SkillInvocationRequest | None = None,
) -> skill_models.CompiledSkill:
    parsed_frontmatter = parse_skill_frontmatter(content, directory_name=record.directory_name)
    compat_metadata = _compat_metadata(record)
    stripped_content = content.strip()
    if len(stripped_content) > 4000:
        stripped_content = stripped_content[:4000].rstrip() + "\n...[truncated]"

    identity = skill_models.SkillSourceIdentity(
        source_kind=infer_skill_source_kind(record),
        source=record.source,
        scope=record.scope,
        source_root=record.root_dir,
        relative_path=_relative_skill_path(record),
        fingerprint=record.content_hash or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        canonical_source_root=canonicalize_skill_path(record.root_dir),
        canonical_entry_file=canonicalize_skill_path(record.entry_file),
        discovery_provenance=_discovery_provenance(record),
    )
    semantic_family, semantic_domain, semantic_task_mode, semantic_tags = infer_skill_semantics(
        directory_name=record.directory_name,
        name=record.name,
        when_to_use=parsed_frontmatter.when_to_use,
        description=record.description,
        explicit_family=parsed_frontmatter.semantic_family,
        explicit_domain=parsed_frontmatter.semantic_domain,
        explicit_task_mode=parsed_frontmatter.semantic_task_mode,
        explicit_tags=parsed_frontmatter.semantic_tags,
    )

    compiled_skill = skill_models.CompiledSkill(
        identity=identity,
        skill_id=record.id,
        name=record.name,
        directory_name=record.directory_name,
        entry_file=record.entry_file,
        description=record.description,
        content=content,
        compatibility=list(record.compatibility_json),
        parameter_schema=dict(record.parameter_schema_json),
        aliases=list(parsed_frontmatter.aliases),
        allowed_tools=list(parsed_frontmatter.allowed_tools),
        user_invocable=parsed_frontmatter.user_invocable,
        argument_hint=parsed_frontmatter.argument_hint,
        activation_paths=list(parsed_frontmatter.activation_paths),
        invocable=_bool_compat_value(compat_metadata, "invocable", default=True),
        dynamic=_bool_compat_value(compat_metadata, "dynamic", default=False),
        when_to_use=parsed_frontmatter.when_to_use,
        context_hint=parsed_frontmatter.context_hint,
        agent=parsed_frontmatter.agent,
        effort=parsed_frontmatter.effort,
        trust_metadata=_trust_metadata(record, parsed_frontmatter),
        preflight_checks=list(parsed_frontmatter.preflight_checks),
        orchestration_role=parsed_frontmatter.orchestration_role,
        orchestration_hints=parsed_frontmatter.orchestration_hints,
        fanout_group=parsed_frontmatter.fanout_group,
        preferred_stage=parsed_frontmatter.preferred_stage,
        context_strategy=parsed_frontmatter.context_strategy,
        execution_policy=parsed_frontmatter.execution_policy,
        result_schema=parsed_frontmatter.result_schema,
        semantic_family=semantic_family,
        semantic_domain=semantic_domain,
        semantic_task_mode=semantic_task_mode,
        semantic_tags=semantic_tags,
        loaded_from=_string_compat_value(compat_metadata, "loaded_from") or record.entry_file,
        shell_enabled=_bool_compat_value(
            compat_metadata,
            "shell_enabled",
            default=identity.source_kind is not skill_models.SkillSourceKind.MCP,
        ),
        discovery_provenance=_discovery_provenance(record),
    )
    prepared_invocation = prepare_skill_invocation(
        compiled_skill,
        invocation_request or skill_models.SkillInvocationRequest(),
        raw_content=stripped_content,
    )
    compiled_skill.prepared_invocation = prepared_invocation
    compiled_skill.prepared_prompt = build_prepared_prompt_fragment(
        compiled_skill,
        prepared_invocation.prompt_text,
        prepared_invocation=prepared_invocation,
    )
    return compiled_skill


def build_prepared_prompt_fragment(
    compiled_skill: skill_models.CompiledSkill,
    rendered_content: str | None = None,
    *,
    prepared_invocation: skill_models.PreparedSkillInvocation | None = None,
) -> str:
    resolved_name = compiled_skill.directory_name or compiled_skill.name or "unknown-skill"
    lines = [
        f"Prepared primary skill: {resolved_name}",
        f"## Prepared skill context: primary={resolved_name}",
        "Execution mode: server-side skill executor facade",
        f"Resolved skill id: {compiled_skill.skill_id}",
        (
            "This prepared context represents the primary skill already loaded for the current "
            "request. Supporting skills may also be active elsewhere in the resolution payload, "
            "and this prepared primary context remains reference-only until the runtime approval "
            "and tool pipeline decide the next step."
        ),
        ("Use the prepared guidance below before deciding on follow-up tools or the final answer."),
    ]
    if compiled_skill.aliases:
        lines.append(f"Aliases: {', '.join(compiled_skill.aliases)}")
    if compiled_skill.allowed_tools:
        lines.append(f"Allowed tools hint: {', '.join(compiled_skill.allowed_tools)}")
    if compiled_skill.argument_hint:
        lines.append(f"Argument hint: {compiled_skill.argument_hint}")
    if compiled_skill.when_to_use:
        lines.append(f"When to use: {compiled_skill.when_to_use}")
    if compiled_skill.context_hint:
        lines.append(f"Context: {compiled_skill.context_hint}")
    if compiled_skill.agent:
        lines.append(f"Agent: {compiled_skill.agent}")
    if compiled_skill.effort:
        lines.append(f"Effort: {compiled_skill.effort}")
    if compiled_skill.trust_metadata is not None:
        if compiled_skill.trust_metadata.verification_mode:
            lines.append(f"Verification mode: {compiled_skill.trust_metadata.verification_mode}")
        if compiled_skill.trust_metadata.shell_profile:
            lines.append(f"Shell profile: {compiled_skill.trust_metadata.shell_profile}")
        if compiled_skill.trust_metadata.trust_level:
            lines.append(f"Trust level: {compiled_skill.trust_metadata.trust_level}")
    if compiled_skill.orchestration_role:
        lines.append(f"Orchestration role: {compiled_skill.orchestration_role}")
    if compiled_skill.preferred_stage:
        lines.append(f"Preferred stage: {compiled_skill.preferred_stage}")
    if compiled_skill.context_strategy:
        lines.append(f"Context strategy: {compiled_skill.context_strategy}")
    lines.append(f"Invocable: {str(compiled_skill.invocable).lower()}")
    lines.append(f"Dynamic: {str(compiled_skill.dynamic).lower()}")
    if compiled_skill.loaded_from:
        lines.append(f"Loaded from: {compiled_skill.loaded_from}")
    if compiled_skill.activation_paths:
        lines.append(f"Conditional paths: {', '.join(compiled_skill.activation_paths)}")
    if prepared_invocation is not None:
        substitution_values = prepared_invocation.context.substitution_values
        if substitution_values:
            rendered_pairs = ", ".join(
                f"{key}={value}" for key, value in sorted(substitution_values.items())
            )
            lines.append(f"Substitutions: {rendered_pairs}")
        if prepared_invocation.shell_expansions:
            status = "enabled" if compiled_skill.shell_enabled else "disabled"
            lines.append(
                "Shell expansions detected: "
                f"{len(prepared_invocation.shell_expansions)} pending approval ({status})."
            )
        if compiled_skill.preflight_checks:
            lines.append(
                "Preflight checks planned: "
                + ", ".join(check.name for check in compiled_skill.preflight_checks)
            )
    if rendered_content:
        lines.extend(["", rendered_content])
    return "\n".join(lines)


def prepare_skill_invocation(
    compiled_skill: skill_models.CompiledSkill,
    invocation_request: skill_models.SkillInvocationRequest,
    *,
    raw_content: str,
) -> skill_models.PreparedSkillInvocation:
    substitution_values = build_skill_substitution_values(compiled_skill, invocation_request)
    rendered_prompt = apply_skill_substitutions(raw_content, substitution_values)
    shell_expansions = parse_shell_expansion_requests(
        rendered_prompt,
        shell_enabled=compiled_skill.shell_enabled,
    )
    pending_actions = [
        skill_models.SkillInvocationPendingAction(
            action_type="shell_expansion",
            status="pending_approval" if expansion.shell_allowed else "disabled",
            payload=expansion.to_payload(),
        )
        for expansion in shell_expansions
    ]
    pending_actions.extend(
        skill_models.SkillInvocationPendingAction(
            action_type="preflight_check",
            status="planned",
            payload=check.to_payload(),
        )
        for check in compiled_skill.preflight_checks
    )
    return skill_models.PreparedSkillInvocation(
        request=invocation_request,
        context=skill_models.SkillInvocationContext(
            skill_directory=_resolve_skill_directory(compiled_skill),
            shell_enabled=compiled_skill.shell_enabled,
            substitution_values=substitution_values,
            session_id=invocation_request.session_id,
        ),
        prompt_text=rendered_prompt,
        shell_expansions=shell_expansions,
        pending_actions=pending_actions,
    )


def build_skill_substitution_values(
    compiled_skill: skill_models.CompiledSkill,
    invocation_request: skill_models.SkillInvocationRequest,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in invocation_request.arguments.items():
        stringified = _stringify_substitution_value(value)
        if stringified is not None:
            values[key] = stringified
    values["CLAUDE_SKILL_DIR"] = _resolve_skill_directory(compiled_skill)
    if invocation_request.session_id is not None:
        values["CLAUDE_SESSION_ID"] = invocation_request.session_id
    return values


def apply_skill_substitutions(content: str, values: dict[str, str]) -> str:
    return SUBSTITUTION_RE.sub(lambda match: values.get(match.group(1), match.group(0)), content)


def parse_shell_expansion_requests(
    content: str, *, shell_enabled: bool
) -> list[skill_models.SkillPromptShellExpansion]:
    expansions: list[skill_models.SkillPromptShellExpansion] = []
    lines = content.splitlines()
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        fenced_match = FENCED_SHELL_RE.match(line)
        if fenced_match is not None:
            fence = fenced_match.group("fence")
            start_line = line_index + 1
            block_lines: list[str] = []
            line_index += 1
            while line_index < len(lines) and lines[line_index].strip() != fence:
                block_lines.append(lines[line_index])
                line_index += 1
            end_line = min(line_index + 1, len(lines))
            block_text = "\n".join(block_lines).strip()
            if block_text:
                expansions.append(
                    _build_shell_expansion(
                        kind=skill_models.SkillPromptShellExpansionKind.FENCED,
                        command=block_text,
                        original_text="\n".join(lines[start_line - 1 : end_line]),
                        line_start=start_line,
                        line_end=end_line,
                        shell_enabled=shell_enabled,
                    )
                )
            line_index += 1
            continue

        inline_match = INLINE_SHELL_RE.match(line)
        if inline_match is not None:
            expansions.append(
                _build_shell_expansion(
                    kind=skill_models.SkillPromptShellExpansionKind.INLINE,
                    command=inline_match.group("command").strip(),
                    original_text=line,
                    line_start=line_index + 1,
                    line_end=line_index + 1,
                    shell_enabled=shell_enabled,
                )
            )
        line_index += 1
    return expansions


def infer_skill_source_kind(record: SkillRecord) -> skill_models.SkillSourceKind:
    compat_source_kind = _string_compat_value(_compat_metadata(record), "source_kind")
    if compat_source_kind is not None:
        try:
            return skill_models.SkillSourceKind(compat_source_kind)
        except ValueError:
            pass
    normalized_root = record.root_dir.casefold()
    if normalized_root.startswith("mcp://"):
        return skill_models.SkillSourceKind.MCP
    if normalized_root.replace("\\", "/").endswith("/bundled-skills"):
        return skill_models.SkillSourceKind.BUNDLED
    if normalized_root.replace("\\", "/").endswith("/.claude/commands"):
        return skill_models.SkillSourceKind.LEGACY_COMMAND_DIRECTORY
    return skill_models.SkillSourceKind.FILESYSTEM


def _build_shell_expansion(
    *,
    kind: skill_models.SkillPromptShellExpansionKind,
    command: str,
    original_text: str,
    line_start: int,
    line_end: int,
    shell_enabled: bool,
) -> skill_models.SkillPromptShellExpansion:
    reason = None if shell_enabled else "shell_disabled_for_source_kind"
    status = "pending_approval" if shell_enabled else "disabled"
    return skill_models.SkillPromptShellExpansion(
        kind=kind,
        command=command,
        original_text=original_text,
        line_start=line_start,
        line_end=line_end,
        shell_allowed=shell_enabled,
        status=status,
        reason=reason,
    )


def _stringify_substitution_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    return None


def _relative_skill_path(record: SkillRecord) -> str:
    entry_path = Path(record.entry_file)
    root_path = Path(record.root_dir)
    try:
        return entry_path.resolve().relative_to(root_path.resolve()).as_posix()
    except ValueError:
        return entry_path.name


def _compat_metadata(record: SkillRecord) -> dict[str, object]:
    compat_payload = record.raw_frontmatter_json.get("_compat")
    return dict(compat_payload) if isinstance(compat_payload, dict) else {}


def _string_compat_value(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _bool_compat_value(payload: dict[str, object], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    return value if isinstance(value, bool) else default


def _resolve_skill_directory(compiled_skill: skill_models.CompiledSkill) -> str:
    if compiled_skill.identity.source_kind == skill_models.SkillSourceKind.MCP:
        return compiled_skill.identity.source_root
    return Path(compiled_skill.entry_file).resolve().parent.as_posix()


def _trust_metadata(
    record: SkillRecord,
    parsed_frontmatter: skill_models.ParsedSkillFrontmatter,
) -> SkillTrustMetadata | None:
    if parsed_frontmatter.verification_mode is not None:
        verification_mode: str | None = parsed_frontmatter.verification_mode
    else:
        verification_mode = _string_compat_value(_compat_metadata(record), "verification_mode")
    if parsed_frontmatter.shell_profile is not None:
        shell_profile: str | None = parsed_frontmatter.shell_profile
    else:
        shell_profile = _string_compat_value(_compat_metadata(record), "shell_profile")
    if parsed_frontmatter.trust_level is not None:
        trust_level: str | None = parsed_frontmatter.trust_level
    else:
        trust_level = _string_compat_value(_compat_metadata(record), "trust_level")
    effective_trust_level = resolve_effective_trust_level(
        source=record.source.value,
        source_kind=infer_skill_source_kind(record).value,
    )
    trust_metadata = SkillTrustMetadata(
        verification_mode=verification_mode,
        shell_profile=shell_profile,
        trust_level=effective_trust_level if trust_level is not None else effective_trust_level,
    )
    return None if trust_metadata.is_empty else trust_metadata


def _discovery_provenance(record: SkillRecord) -> dict[str, object]:
    compat_metadata = _compat_metadata(record)
    provenance = compat_metadata.get("discovery_provenance")
    if isinstance(provenance, dict):
        return dict(provenance)
    return build_discovery_provenance(
        source_root=record.root_dir,
        entry_file=record.entry_file,
        relative_path=_relative_skill_path(record),
        source_kind=infer_skill_source_kind(record).value,
        root_label=_string_compat_value(compat_metadata, "root_label"),
        metadata=compat_metadata,
    )
