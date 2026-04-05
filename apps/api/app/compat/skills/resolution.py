from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from app.compat.skills import models as skill_models

_WORD_RE = re.compile(r"[a-z0-9_\-/\.]+")
_ARG_HINT_RE = re.compile(r"--([a-zA-Z0-9_-]+)|<([a-zA-Z0-9_-]+)>")
_DEFAULT_TOP_K = 5
_FIXED_RUNTIME_TOOLS = {
    "execute_kali_command",
    "list_available_skills",
    "execute_skill",
    "read_skill_content",
    "call_mcp_tool",
}
_SOURCE_KIND_SCORES: dict[skill_models.SkillSourceKind, int] = {
    skill_models.SkillSourceKind.FILESYSTEM: 5,
    skill_models.SkillSourceKind.BUNDLED: 4,
    skill_models.SkillSourceKind.LEGACY_COMMAND_DIRECTORY: 3,
    skill_models.SkillSourceKind.MCP: 1,
}


def score_skill_candidate(
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
) -> skill_models.SkillCandidateScoreBreakdown:
    breakdown = skill_models.SkillCandidateScoreBreakdown()

    breakdown.path_score = _score_path_match(
        compiled_skill=compiled_skill,
        touched_paths=request.touched_paths,
        matched_activation_paths=breakdown.matched_activation_paths,
        reasons=breakdown.reasons,
    )
    breakdown.agent_score = _score_agent_match(
        compiled_skill=compiled_skill,
        request=request,
        matched_terms=breakdown.matched_agent_terms,
        reasons=breakdown.reasons,
    )
    breakdown.when_to_use_score = _score_when_to_use_match(
        compiled_skill=compiled_skill,
        request=request,
        matched_terms=breakdown.matched_when_to_use_terms,
        reasons=breakdown.reasons,
    )
    breakdown.compatibility_score = _score_compatibility_match(
        compiled_skill=compiled_skill,
        request=request,
        matched_terms=breakdown.matched_compatibility_terms,
        reasons=breakdown.reasons,
    )
    breakdown.allowed_tools_score = _score_allowed_tools(
        compiled_skill=compiled_skill,
        request=request,
        matched_tools=breakdown.matched_allowed_tools,
        missing_tools=breakdown.missing_allowed_tools,
        reasons=breakdown.reasons,
    )
    breakdown.argument_readiness_score = _score_argument_readiness(
        compiled_skill=compiled_skill,
        request=request,
        matched_names=breakdown.matched_argument_names,
        missing_names=breakdown.missing_argument_names,
        reasons=breakdown.reasons,
    )
    breakdown.effort_score = _score_effort(
        compiled_skill=compiled_skill,
        request=request,
        reasons=breakdown.reasons,
    )
    breakdown.source_kind_score = _SOURCE_KIND_SCORES.get(compiled_skill.identity.source_kind, 0)
    breakdown.reasons.append(
        f"source_kind={compiled_skill.identity.source_kind.value} (+{breakdown.source_kind_score})"
    )

    if compiled_skill.user_invocable is False:
        breakdown.penalties.append(
            "user_invocable=false (kept for agent auto-selection, deprioritized in ties)"
        )
    if not compiled_skill.invocable:
        breakdown.penalties.append(
            "reference-only skill (not executable via execute_skill shortlist by default)"
        )
    return breakdown


def rank_skill_candidates(
    compiled_skills: list[skill_models.CompiledSkill],
    request: skill_models.SkillResolutionRequest,
) -> list[skill_models.ResolvedSkillCandidate]:
    candidates = [_build_resolved_skill_candidate(skill, request) for skill in compiled_skills]
    ranked_candidates = sorted(candidates, key=_ranking_sort_key)
    for index, candidate in enumerate(ranked_candidates, start=1):
        candidate.rank = index
    return ranked_candidates


def resolve_skill_candidates(
    compiled_skills: list[skill_models.CompiledSkill],
    request: skill_models.SkillResolutionRequest,
) -> skill_models.SkillResolutionResult:
    ranked_candidates = rank_skill_candidates(compiled_skills, request)

    executable_candidates: list[skill_models.ResolvedSkillCandidate] = []
    reference_candidates: list[skill_models.ResolvedSkillCandidate] = []
    rejected_candidates: list[skill_models.ResolvedSkillCandidate] = []

    for candidate in ranked_candidates:
        if candidate.compiled_skill.invocable:
            executable_candidates.append(candidate)
            continue
        if request.include_reference_only:
            reference_candidates.append(candidate)
            continue
        candidate.rejected_reason = "reference_only_excluded"
        rejected_candidates.append(candidate)

    top_k = max(1, request.top_k or _DEFAULT_TOP_K)
    shortlisted_candidates = executable_candidates[:top_k]
    if shortlisted_candidates:
        shortlisted_candidates[0].selected = True

    return skill_models.SkillResolutionResult(
        request=request,
        considered_candidates=ranked_candidates,
        shortlisted_candidates=shortlisted_candidates,
        reference_candidates=reference_candidates[:top_k],
        rejected_candidates=rejected_candidates,
    )


def build_skill_candidate_prompt_fragment(
    resolution_result: skill_models.SkillResolutionResult,
) -> str:
    if not resolution_result.shortlisted_candidates and not resolution_result.reference_candidates:
        return "No ranked skill candidates are currently available."

    lines = [
        (
            "Top ranked skills for current context: pick the highest-ranked skill unless a "
            "lower-ranked skill is more specific to the exact subtask."
        )
    ]
    for index, candidate in enumerate(resolution_result.shortlisted_candidates, start=1):
        skill = candidate.compiled_skill
        score = candidate.total_score
        selected_label = " selected" if candidate.selected else ""
        display_rank = index
        global_rank_suffix = ""
        if candidate.rank and candidate.rank != index:
            global_rank_suffix = f" global-rank={candidate.rank}"
        score_label = f"score={score}{selected_label}{global_rank_suffix}"
        lines.append(
            f"{display_rank}. {skill.directory_name} [{score_label}] "
            f"agent={skill.agent or 'n/a'} effort={skill.effort or 'n/a'} "
            f"invocable={str(skill.invocable).lower()}"
        )
        if skill.when_to_use:
            lines.append(f"   when_to_use: {skill.when_to_use.strip()}")
        if skill.activation_paths:
            lines.append(f"   paths: {list(skill.activation_paths)}")
        lines.append(f"   why: {'; '.join(candidate.reasons[:4])}")

    if resolution_result.reference_candidates:
        lines.append("")
        lines.append(
            "Reference-only ranked candidates (visible for context, not executable by default):"
        )
        for candidate in resolution_result.reference_candidates:
            skill = candidate.compiled_skill
            lines.append(
                f"- {skill.directory_name} [score={candidate.total_score}] why: "
                f"{'; '.join(candidate.reasons[:3])}"
            )

    return "\n".join(lines)


def _build_resolved_skill_candidate(
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
) -> skill_models.ResolvedSkillCandidate:
    breakdown = score_skill_candidate(compiled_skill, request)
    return skill_models.ResolvedSkillCandidate(
        compiled_skill=compiled_skill,
        score_breakdown=breakdown,
        reasons=[*breakdown.reasons, *breakdown.penalties],
    )


def _ranking_sort_key(candidate: skill_models.ResolvedSkillCandidate) -> tuple[object, ...]:
    skill = candidate.compiled_skill
    breakdown = candidate.score_breakdown
    return (
        -candidate.total_score,
        -breakdown.path_score,
        -breakdown.agent_score,
        -breakdown.when_to_use_score,
        0 if skill.invocable else 1,
        0 if skill.user_invocable is True else 1,
        -int(bool(skill.aliases)),
        -int(skill.user_invocable is True),
        -len(skill.content),
        skill.identity.source_kind.value,
        skill.identity.source.value,
        skill.identity.scope.value,
        skill.identity.source_root.casefold(),
        skill.identity.relative_path.casefold(),
        skill.identity.fingerprint,
        skill.directory_name.casefold(),
        skill.name.casefold(),
    )


def _score_path_match(
    *,
    compiled_skill: skill_models.CompiledSkill,
    touched_paths: list[str],
    matched_activation_paths: list[str],
    reasons: list[str],
) -> int:
    if not compiled_skill.activation_paths:
        reasons.append("unconditional skill (no path activation constraint)")
        return 0
    if not touched_paths:
        reasons.append("conditional skill without touched-path context")
        return 0

    best_score = 0
    best_reason: str | None = None
    normalized_touched_paths = [_normalize_path(path) for path in touched_paths if path.strip()]
    for pattern in compiled_skill.activation_paths:
        normalized_pattern = _normalize_path(pattern)
        for touched_path in normalized_touched_paths:
            match_score, reason = _path_match_score(normalized_pattern, touched_path)
            if match_score > best_score:
                best_score = match_score
                best_reason = reason
                matched_activation_paths[:] = [pattern]
            elif match_score and pattern not in matched_activation_paths:
                matched_activation_paths.append(pattern)
    if best_reason is not None:
        reasons.append(best_reason)
    return best_score


def _path_match_score(pattern: str, touched_path: str) -> tuple[int, str | None]:
    if not pattern:
        return 0, None
    if not any(character in pattern for character in "*?["):
        if touched_path == pattern:
            return 40, f"exact path match '{pattern}' (+40)"
        if touched_path.startswith(f"{pattern.rstrip('/')}/"):
            return 24, f"parent/prefix path match '{pattern}' (+24)"
        if touched_path.endswith(f"/{pattern}"):
            return 24, f"filename/prefix path match '{pattern}' (+24)"
        return 0, None
    if fnmatch.fnmatch(touched_path, pattern):
        return 32, f"glob path match '{pattern}' (+32)"
    return 0, None


def _score_agent_match(
    *,
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    matched_terms: list[str],
    reasons: list[str],
) -> int:
    if not compiled_skill.agent:
        return 0
    query = " ".join(part for part in (request.agent_role, request.workflow_stage) if part)
    score, terms = _score_text_overlap(compiled_skill.agent, query, max_score=20)
    if score:
        matched_terms.extend(terms)
        reasons.append(f"agent/workflow match {terms} (+{score})")
    return score


def _score_when_to_use_match(
    *,
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    matched_terms: list[str],
    reasons: list[str],
) -> int:
    if not compiled_skill.when_to_use:
        return 0
    query = _request_context_text(request)
    score, terms = _score_text_overlap(compiled_skill.when_to_use, query, max_score=20)
    if score:
        matched_terms.extend(terms)
        reasons.append(f"when_to_use overlap {terms} (+{score})")
    return score


def _score_compatibility_match(
    *,
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    matched_terms: list[str],
    reasons: list[str],
) -> int:
    if not compiled_skill.compatibility:
        return 0
    query_tokens = _tokenize(_request_context_text(request))
    compatibility_tokens = {
        token for item in compiled_skill.compatibility for token in _tokenize(item)
    }
    overlap = sorted(compatibility_tokens & query_tokens)
    if not overlap:
        return 0
    matched_terms.extend(overlap)
    score = min(10, len(overlap) * 4)
    reasons.append(f"compatibility overlap {overlap} (+{score})")
    return score


def _score_allowed_tools(
    *,
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    matched_tools: list[str],
    missing_tools: list[str],
    reasons: list[str],
) -> int:
    allowed_tools = {
        _normalize_tool_name(tool) for tool in compiled_skill.allowed_tools if tool.strip()
    }
    if not allowed_tools:
        reasons.append("no allowed_tools restriction (neutral +5)")
        return 5

    available_tools = {
        _normalize_tool_name(tool)
        for tool in (request.available_tools or list(_FIXED_RUNTIME_TOOLS))
        if isinstance(tool, str) and tool.strip()
    }
    overlap = sorted(allowed_tools & available_tools)
    missing = sorted(allowed_tools - available_tools)
    matched_tools.extend(overlap)
    missing_tools.extend(missing)
    if not overlap:
        reasons.append(f"allowed_tools missing {missing} (+0)")
        return 0
    score = max(1, round(10 * (len(overlap) / len(allowed_tools))))
    reasons.append(f"allowed_tools overlap {overlap} (+{score})")
    return score


def _score_argument_readiness(
    *,
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    matched_names: list[str],
    missing_names: list[str],
    reasons: list[str],
) -> int:
    argument_keys = {
        _normalize_tool_name(key)
        for key in request.invocation_arguments.keys()
        if isinstance(key, str) and key.strip()
    }
    required_names = _required_argument_names(compiled_skill)
    hinted_names = _hinted_argument_names(compiled_skill.argument_hint)

    matched_required = sorted(required_names & argument_keys)
    missing_required = sorted(required_names - argument_keys)
    matched_names.extend(matched_required)
    missing_names.extend(missing_required)

    if required_names:
        ratio = len(matched_required) / len(required_names)
        score = round((ratio * 20) - 10)
        score = max(-10, min(10, score))
        if matched_required:
            reasons.append(f"required arguments present {matched_required} ({score:+d})")
        if missing_required:
            reasons.append(f"missing required arguments {missing_required} ({score:+d})")
        return score

    if hinted_names:
        matched_hints = sorted(hinted_names & argument_keys)
        if matched_hints:
            matched_names.extend(name for name in matched_hints if name not in matched_names)
            reasons.append(f"argument hints already satisfied {matched_hints} (+4)")
            return 4
        reasons.append(f"argument hints suggest more inputs {sorted(hinted_names)} (-2)")
        return -2

    reasons.append("no required arguments detected (+2)")
    return 2


def _score_effort(
    *,
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    reasons: list[str],
) -> int:
    effort = (compiled_skill.effort or "").strip().casefold()
    if not effort:
        return 0
    request_text = _request_context_text(request)
    fast_mode = any(token in request_text for token in ("fast", "quick", "triage", "summary"))
    deep_mode = any(
        token in request_text
        for token in ("deep", "analysis", "analyze", "audit", "research", "investigate")
    )
    if fast_mode:
        score_map = {"low": 5, "medium": 1, "high": -5}
        score = score_map.get(effort, 0)
        if score:
            reasons.append(f"effort={effort} aligned with fast/triage stage ({score:+d})")
        return score
    if deep_mode:
        score_map = {"low": -2, "medium": 3, "high": 5}
        score = score_map.get(effort, 0)
        if score:
            reasons.append(f"effort={effort} aligned with deep-analysis stage ({score:+d})")
        return score
    score_map = {"low": 1, "medium": 2, "high": 0}
    score = score_map.get(effort, 0)
    if score:
        reasons.append(f"effort={effort} baseline fit ({score:+d})")
    return score


def _required_argument_names(compiled_skill: skill_models.CompiledSkill) -> set[str]:
    required = compiled_skill.parameter_schema.get("required")
    if not isinstance(required, list):
        return set()
    return {
        _normalize_tool_name(item) for item in required if isinstance(item, str) and item.strip()
    }


def _hinted_argument_names(argument_hint: str | None) -> set[str]:
    if not argument_hint:
        return set()
    names: set[str] = set()
    for match in _ARG_HINT_RE.finditer(argument_hint):
        value = match.group(1) or match.group(2)
        if value:
            names.add(_normalize_tool_name(value))
    return names


def _score_text_overlap(
    reference_text: str, query_text: str, *, max_score: int
) -> tuple[int, list[str]]:
    reference_tokens = _tokenize(reference_text)
    query_tokens = _tokenize(query_text)
    overlap = sorted(reference_tokens & query_tokens)
    if not overlap:
        return 0, []
    score = min(max_score, max(1, len(overlap) * (4 if max_score >= 20 else 3)))
    return score, overlap[:5]


def _request_context_text(request: skill_models.SkillResolutionRequest) -> str:
    path_tokens = " ".join(PurePosixPath(path).as_posix() for path in request.touched_paths)
    return " ".join(
        part
        for part in (
            request.user_goal or "",
            request.current_prompt or "",
            request.scenario_type or "",
            request.agent_role or "",
            request.workflow_stage or "",
            path_tokens,
        )
        if part
    ).casefold()


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    normalized = text.casefold().replace("\\", "/")
    return {
        token
        for token in _WORD_RE.findall(normalized)
        if token and len(token) > 2 and token not in {"the", "and", "for", "with", "from"}
    }


def _normalize_path(path_value: str) -> str:
    return path_value.replace("\\", "/").strip().lstrip("./").casefold()


def _normalize_tool_name(name: str) -> str:
    return name.strip().replace("-", "_").casefold()
