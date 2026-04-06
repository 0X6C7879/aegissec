from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.compat.skills import models as skill_models

_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_HOST_PORT_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")
_JAVA_PATH_RE = re.compile(r"\.(?:java|class|jar)\b", re.IGNORECASE)

_JAVA_AUDIT_FAMILY = "java-audit"
_CTF_FAMILY = "ctf"


def infer_skill_semantics(
    *,
    directory_name: str,
    name: str,
    when_to_use: str | None,
    description: str,
    explicit_family: str | None,
    explicit_domain: str | None,
    explicit_task_mode: str | None,
    explicit_tags: list[str] | None,
) -> tuple[str | None, str | None, str | None, list[str]]:
    family = _normalize_optional(explicit_family)
    domain = _normalize_optional(explicit_domain)
    task_mode = _normalize_optional(explicit_task_mode)
    tags = {_normalize_token(tag) for tag in explicit_tags or [] if _normalize_token(tag)}

    tokens = _tokenize(" ".join(filter(None, [directory_name, name, when_to_use, description])))
    slug = f"{directory_name} {name}".casefold()

    if family is None and ("solve-challenge" in slug or "ctf" in tokens or "challenge" in tokens):
        family = _CTF_FAMILY
    if domain is None and ("ctf-web" in slug or "web" in tokens):
        domain = "web"
    if task_mode is None and "solve-challenge" in slug:
        task_mode = "dispatcher"

    java_markers = {"java", "spring", "servlet", "requestmapping", "route", "mapper", "tracer"}
    if family is None and (
        {"java-route-tracer", "java-route-mapper"} & set(slug.split())
        or ("java" in slug and (tokens & java_markers))
    ):
        family = _JAVA_AUDIT_FAMILY
    if domain is None and (family == _JAVA_AUDIT_FAMILY or "java" in tokens):
        domain = "java"
    if task_mode is None and family == _JAVA_AUDIT_FAMILY:
        task_mode = "audit"

    if "ctf-web" in slug:
        family = family or _CTF_FAMILY
        domain = domain or "web"
        task_mode = task_mode or "specialized"
        tags.update({"ctf", "ctf-web", "web"})
    if "solve-challenge" in slug:
        family = family or _CTF_FAMILY
        task_mode = task_mode or "dispatcher"
        tags.update({"ctf", "dispatcher", "challenge"})
    if family == _JAVA_AUDIT_FAMILY:
        tags.update({"java-audit", "java"})

    if family:
        tags.add(family)
    if domain:
        tags.add(domain)
    if task_mode:
        tags.add(task_mode)
    return family, domain, task_mode, sorted(tags)


def infer_task_intent(
    request: skill_models.SkillResolutionRequest,
) -> skill_models.SkillIntentProfile:
    request_text = _request_text(request)
    tokens = _tokenize(request_text)
    touched_text = " ".join(request.touched_paths)
    has_http_target = bool(_URL_RE.search(request_text)) or any(
        token in tokens
        for token in {"http", "https", "url", "api", "jwt", "xss", "sqli", "ssrf", "browser"}
    )
    has_remote_signal = (
        has_http_target
        or bool(_HOST_PORT_RE.search(request_text))
        or any(token in tokens for token in {"host", "port", "remote", "nc", "netcat", "instance"})
    )
    java_evidence = _has_java_code_evidence(request_text, touched_text)
    ctf_signal = any(
        token in tokens for token in {"ctf", "challenge", "flag", "题目", "web题", "web题目"}
    ) or (has_remote_signal and any(token in tokens for token in {"solve", "exploit", "hint"}))
    web_ctf_signal = ctf_signal and has_http_target
    explicit_java_route_trace = any(
        token in tokens
        for token in {
            "java-route-tracer",
            "route-trace",
            "route trace",
            "route",
            "trace",
            "requestmapping",
            "controller",
            "servlet",
        }
    )

    notes: list[str] = []
    preferred_tags: list[str] = []
    suppressed_tags: list[str] = []
    dominant_domain = "generic_recon"
    is_local_codebase_task = (
        bool(request.touched_paths)
        or java_evidence
        or any(token in tokens for token in {"repo", "codebase", "local", "source", "project"})
    )
    specialized_web_focus = has_http_target and any(
        token in tokens
        for token in {
            "focus",
            "specialized",
            "specific",
            "analysis",
            "analyze",
            "audit",
            "exploit",
            "exploitation",
            "分析",
            "专注",
            "漏洞",
            "漏洞点",
        }
    )
    prefers_dispatcher = ctf_signal and not specialized_web_focus

    if ctf_signal:
        dominant_domain = "ctf_challenge"
        preferred_tags.extend(["ctf", "challenge"])
        if prefers_dispatcher:
            preferred_tags.append("dispatcher")
        notes.append("challenge signals detected before scoring")
    if web_ctf_signal:
        dominant_domain = "ctf_web"
        preferred_tags.extend(["ctf-web", "web"])
        if specialized_web_focus:
            preferred_tags.extend(["specialized", "web-focus"])
        notes.append("remote HTTP challenge matched ctf-web specialization")
    elif has_http_target and not is_local_codebase_task:
        dominant_domain = "remote_http_service"
        preferred_tags.extend(["web", "http", "remote"])
        notes.append("remote HTTP target detected without local codebase evidence")
    if java_evidence:
        dominant_domain = "java_code_audit"
        preferred_tags.extend([_JAVA_AUDIT_FAMILY, "java"])
        notes.append("local Java code evidence detected")
    if explicit_java_route_trace and java_evidence:
        dominant_domain = "java_route_trace"
        preferred_tags.extend(["route-trace", "requestmapping"])
        notes.append("explicit Java route tracing intent detected")
    if is_local_codebase_task and dominant_domain == "generic_recon":
        dominant_domain = "local_project_audit"
        preferred_tags.extend(["local", "project", "audit"])
        notes.append("local project analysis context detected")
    if not java_evidence:
        suppressed_tags.extend([_JAVA_AUDIT_FAMILY, "java-route-tracer", "java-route-mapper"])
        notes.append("java audit family suppressed without Java code evidence")

    return skill_models.SkillIntentProfile(
        dominant_domain=dominant_domain,
        is_ctf=ctf_signal,
        is_remote_service=has_remote_signal,
        is_http_target=has_http_target,
        is_local_codebase_task=is_local_codebase_task,
        prefers_dispatcher=prefers_dispatcher,
        preferred_skill_tags=_dedupe_list(preferred_tags),
        suppressed_skill_tags=_dedupe_list(suppressed_tags),
        notes=notes,
    )


def build_skill_intent_adjustment(
    compiled_skill: skill_models.CompiledSkill,
    request: skill_models.SkillResolutionRequest,
    intent_profile: skill_models.SkillIntentProfile,
) -> skill_models.SkillIntentAdjustment:
    adjustment = skill_models.SkillIntentAdjustment()
    tags = _skill_tag_set(compiled_skill)
    request_text = _request_text(request)
    java_evidence = _has_java_code_evidence(request_text, " ".join(request.touched_paths))

    if _JAVA_AUDIT_FAMILY in tags and not java_evidence:
        adjustment.suppressed = True
        adjustment.reasons.append("suppressed: java audit family requires local Java evidence")
        return adjustment

    if intent_profile.is_ctf and _CTF_FAMILY in tags:
        adjustment.prior_score += 10
        adjustment.reasons.append("intent prior: ctf family matched challenge context")
    if intent_profile.is_ctf and intent_profile.prefers_dispatcher and "dispatcher" in tags:
        adjustment.prior_score += 6
        adjustment.reasons.append("intent prior: vague challenge benefits from dispatcher triage")
    if intent_profile.is_ctf and intent_profile.is_http_target and "ctf-web" in tags:
        adjustment.prior_score += 8
        adjustment.reasons.append(
            "intent prior: web challenge specialization matched HTTP evidence"
        )
    if (
        intent_profile.is_http_target
        and not intent_profile.is_local_codebase_task
        and "web" in tags
    ):
        adjustment.prior_score += 5
        adjustment.reasons.append("intent prior: remote HTTP target matched web domain")
    if intent_profile.is_local_codebase_task and _JAVA_AUDIT_FAMILY in tags:
        adjustment.prior_score += 12
        adjustment.reasons.append("intent prior: local code audit matched java audit family")
    if intent_profile.dominant_domain == "java_route_trace" and "route-trace" in tags:
        adjustment.prior_score += 6
        adjustment.reasons.append("intent prior: route trace evidence matched Java tracing")
    if (
        compiled_skill.directory_name.casefold() == "java-route-tracer"
        and intent_profile.dominant_domain != "java_route_trace"
    ):
        adjustment.prior_score -= 4
        adjustment.reasons.append(
            "intent prior: route tracer downranked without explicit route-tracing intent"
        )
    if any(tag in tags for tag in intent_profile.suppressed_skill_tags):
        adjustment.prior_score -= 6
        adjustment.reasons.append("intent prior: matched suppressed semantic tag")
    return adjustment


def _has_java_code_evidence(request_text: str, touched_text: str) -> bool:
    combined = f"{request_text} {touched_text}"
    return bool(_JAVA_PATH_RE.search(combined)) or any(
        marker in combined.casefold()
        for marker in [
            "pom.xml",
            "build.gradle",
            "requestmapping",
            "controller",
            "servlet",
            "spring",
            "src/main/java",
        ]
    )


def _request_text(request: skill_models.SkillResolutionRequest) -> str:
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


def _skill_tag_set(compiled_skill: skill_models.CompiledSkill) -> set[str]:
    tags = {_normalize_token(tag) for tag in compiled_skill.semantic_tags if _normalize_token(tag)}
    for value in (
        compiled_skill.semantic_family,
        compiled_skill.semantic_domain,
        compiled_skill.semantic_task_mode,
        compiled_skill.directory_name,
        compiled_skill.name,
    ):
        token = _normalize_token(value)
        if token:
            tags.add(token)
    return tags


def _tokenize(text: str) -> set[str]:
    normalized = text.casefold().replace("\\", "/")
    rough = re.findall(r"[\w\-\./:\u4e00-\u9fff]+", normalized)
    return {token for token in rough if token and len(token) > 1}


def _normalize_optional(value: str | None) -> str | None:
    normalized = _normalize_token(value)
    return normalized or None


def _normalize_token(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().casefold().replace("_", "-")


def _dedupe_list(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_token(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped
