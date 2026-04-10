from importlib import import_module
from types import SimpleNamespace
from typing import cast

from app.compat.skills import models as skill_models
from app.db.models import CompatibilityScope, CompatibilitySource

skill_resolution = import_module("app.compat.skills.resolution")
session_runner = import_module("app.harness.session_runner")
build_skill_candidate_prompt_fragment = skill_resolution.build_skill_candidate_prompt_fragment
score_skill_candidate = skill_resolution.score_skill_candidate
resolve_skill_candidates = skill_resolution.resolve_skill_candidates
resolve_autorouted_skill_candidate = session_runner._resolve_autorouted_skill_candidate


def test_path_matched_skill_ranks_above_unconditional() -> None:
    request = skill_models.SkillResolutionRequest(
        touched_paths=["apps/api/app/main.py"],
        top_k=5,
    )
    unconditional = _compiled_skill("always-on")
    path_matched = _compiled_skill("api-skill", activation_paths=["apps/api/**"])

    result = resolve_skill_candidates([unconditional, path_matched], request)

    assert [
        candidate.compiled_skill.directory_name for candidate in result.shortlisted_candidates
    ] == ["api-skill"]
    assert result.primary_candidate is not None
    assert result.primary_candidate.score_breakdown.path_score > 0


def test_agent_role_match_boosts_ranking() -> None:
    request = skill_models.SkillResolutionRequest(agent_role="browser automation specialist")
    matched = _compiled_skill("browser", agent="browser automation specialist")
    unmatched = _compiled_skill("general", agent="triage")

    result = resolve_skill_candidates([unmatched, matched], request)

    assert result.shortlisted_candidates[0].compiled_skill.directory_name == "browser"
    assert result.shortlisted_candidates[0].score_breakdown.agent_score > 0


def test_when_to_use_overlap_boosts_ranking() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Need API audit and endpoint triage for auth flows"
    )
    matched = _compiled_skill(
        "api-audit",
        when_to_use="Use for API audit, endpoint triage, and auth review.",
    )
    unmatched = _compiled_skill("other", when_to_use="Use for binary reversing only.")

    result = resolve_skill_candidates([unmatched, matched], request)

    assert result.shortlisted_candidates[0].compiled_skill.directory_name == "api-audit"
    assert result.shortlisted_candidates[0].score_breakdown.when_to_use_score > 0
    assert result.shortlisted_candidates[0].score_breakdown.matched_when_to_use_terms


def test_filesystem_skill_outranks_mcp_by_default_when_other_signals_are_equal() -> None:
    request = skill_models.SkillResolutionRequest(current_prompt="Need browser automation")
    filesystem_skill = _compiled_skill(
        "browser-local",
        source_kind=skill_models.SkillSourceKind.FILESYSTEM,
        when_to_use="Use for browser automation",
    )
    mcp_skill = _compiled_skill(
        "browser-mcp",
        source_kind=skill_models.SkillSourceKind.MCP,
        when_to_use="Use for browser automation",
    )

    result = resolve_skill_candidates([mcp_skill, filesystem_skill], request)

    assert result.shortlisted_candidates[0].compiled_skill.directory_name == "browser-local"


def test_invocable_false_excluded_from_executable_shortlist_by_default() -> None:
    request = skill_models.SkillResolutionRequest(current_prompt="Need burp scan")
    invocable = _compiled_skill("filesystem-scan")
    reference_only = _compiled_skill("mcp-scan", invocable=False)

    result = resolve_skill_candidates([reference_only, invocable], request)

    assert [
        candidate.compiled_skill.directory_name for candidate in result.shortlisted_candidates
    ] == ["filesystem-scan"]
    assert result.rejected_candidates[0].rejected_reason == "reference_only_excluded"


def test_reference_only_candidates_can_be_included_separately() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Need burp scan",
        include_reference_only=True,
    )
    reference_only = _compiled_skill("mcp-scan", invocable=False)

    result = resolve_skill_candidates([reference_only], request)

    assert result.shortlisted_candidates == []
    assert [
        candidate.compiled_skill.directory_name for candidate in result.reference_candidates
    ] == ["mcp-scan"]


def test_general_triage_and_path_specific_skill_are_both_kept() -> None:
    request = skill_models.SkillResolutionRequest(
        touched_paths=["apps/api/app/main.py"],
        current_prompt="Need triage planning and API validation for auth flows",
        top_k=5,
    )
    general_triage = _compiled_skill(
        "triage-planner",
        when_to_use="Use for triage planning and general validation.",
    )
    api_specific = _compiled_skill(
        "api-auth",
        activation_paths=["apps/api/**"],
        when_to_use="Use for API auth validation and endpoint review.",
    )

    result = resolve_skill_candidates([general_triage, api_specific], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "api-auth"
    assert [
        candidate.compiled_skill.directory_name for candidate in result.supporting_candidates
    ] == ["triage-planner"]
    assert [
        candidate.compiled_skill.directory_name for candidate in result.all_selected_candidates
    ] == [
        "api-auth",
        "triage-planner",
    ]


def test_close_score_complementary_skill_becomes_supporting() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Need browser automation validation and planning",
        agent_role="browser automation specialist",
        top_k=5,
    )
    browser_primary = _compiled_skill(
        "browser-executor",
        when_to_use="Use for browser automation validation.",
        agent="browser automation specialist",
    )
    planner_support = _compiled_skill(
        "browser-planner",
        when_to_use="Use for browser automation planning and validation.",
        agent="planning analyst",
    )

    result = resolve_skill_candidates([planner_support, browser_primary], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "browser-executor"
    assert [
        candidate.compiled_skill.directory_name for candidate in result.supporting_candidates
    ] == ["browser-planner"]


def test_highly_redundant_skill_is_rejected() -> None:
    request = skill_models.SkillResolutionRequest(
        touched_paths=["apps/api/app/main.py"],
        current_prompt="Need API audit",
        top_k=5,
    )
    primary = _compiled_skill(
        "api-audit-primary",
        activation_paths=["apps/api/**"],
        when_to_use="Use for API audit and endpoint review.",
        agent="api analyst",
        fingerprint="aaa-primary",
    )
    redundant = _compiled_skill(
        "api-audit-redundant",
        activation_paths=["apps/api/**"],
        when_to_use="Use for API audit and endpoint review.",
        agent="api analyst",
        fingerprint="zzz-redundant",
    )

    result = resolve_skill_candidates([redundant, primary], request)

    assert result.primary_candidate is not None
    assert result.supporting_candidates == []
    rejected = {
        candidate.compiled_skill.directory_name: candidate
        for candidate in result.rejected_candidates
    }
    primary_name = result.primary_candidate.compiled_skill.directory_name
    assert primary_name in {"api-audit-primary", "api-audit-redundant"}
    assert set(rejected) == ({"api-audit-primary", "api-audit-redundant"} - {primary_name})
    assert result.rejected_candidates[0].rejected_reason == "redundant_with_primary"


def test_tie_breaker_is_stable_and_deterministic() -> None:
    request = skill_models.SkillResolutionRequest(current_prompt="general help")
    first = _compiled_skill("alpha", fingerprint="aaa")
    second = _compiled_skill("beta", fingerprint="bbb")

    first_result = resolve_skill_candidates([second, first], request)
    second_result = resolve_skill_candidates([first, second], request)

    assert [
        candidate.compiled_skill.directory_name for candidate in first_result.shortlisted_candidates
    ] == [
        candidate.compiled_skill.directory_name
        for candidate in second_result.shortlisted_candidates
    ]


def test_top_k_trimming_limits_shortlist_size() -> None:
    request = skill_models.SkillResolutionRequest(top_k=2)
    result = resolve_skill_candidates(
        [_compiled_skill("one"), _compiled_skill("two"), _compiled_skill("three")],
        request,
    )

    assert len(result.shortlisted_candidates) == 2


def test_score_breakdown_contains_explanatory_fields() -> None:
    request = skill_models.SkillResolutionRequest(
        touched_paths=["apps/api/app/main.py"],
        available_tools=["execute_skill"],
        invocation_arguments={"target": "demo"},
    )
    candidate = _compiled_skill(
        "api-audit",
        activation_paths=["apps/api/**"],
        allowed_tools=["execute_skill", "read_skill_content"],
        parameter_schema={"type": "object", "required": ["target"]},
    )

    result = resolve_skill_candidates([candidate], request)
    breakdown = result.shortlisted_candidates[0].score_breakdown.to_payload()

    assert cast(int, breakdown["path_score"]) > 0
    assert breakdown["matched_activation_paths"] == ["apps/api/**"]
    assert breakdown["matched_allowed_tools"] == ["execute_skill"]
    assert breakdown["missing_allowed_tools"] == ["read_skill_content"]
    assert breakdown["matched_argument_names"] == ["target"]
    assert "family_fit_score" in breakdown
    assert "domain_fit_score" in breakdown
    assert "task_mode_fit_score" in breakdown


def test_score_skill_candidate_returns_breakdown_object_with_reasons_and_penalties() -> None:
    request = skill_models.SkillResolutionRequest(current_prompt="Need browser automation")
    breakdown = score_skill_candidate(
        _compiled_skill(
            "browser-mcp",
            when_to_use="Use for browser automation",
            invocable=False,
            source_kind=skill_models.SkillSourceKind.MCP,
        ),
        request,
    )

    assert isinstance(breakdown, skill_models.SkillCandidateScoreBreakdown)
    assert breakdown.total >= 0
    assert breakdown.reasons
    assert breakdown.penalties


def test_ranked_candidates_receive_stable_rank_numbers() -> None:
    request = skill_models.SkillResolutionRequest(top_k=3)
    result = resolve_skill_candidates(
        [_compiled_skill("one"), _compiled_skill("two"), _compiled_skill("three")],
        request,
    )

    assert [candidate.rank for candidate in result.shortlisted_candidates] == [1, 2, 3]


def test_prompt_fragment_shows_ranked_shortlist_with_selection_guidance() -> None:
    request = skill_models.SkillResolutionRequest(
        touched_paths=["apps/api/app/main.py"],
        current_prompt="Need API audit",
        include_reference_only=True,
    )
    result = resolve_skill_candidates(
        [
            _compiled_skill("always-on"),
            _compiled_skill(
                "api-audit", activation_paths=["apps/api/**"], when_to_use="Use for API audit"
            ),
            _compiled_skill(
                "mcp-ref", invocable=False, source_kind=skill_models.SkillSourceKind.MCP
            ),
        ],
        request,
    )

    fragment = build_skill_candidate_prompt_fragment(result)

    assert "Selected skill set" in fragment
    assert "Supporting skills selected for complement" in fragment
    assert "1. api-audit [score=" in fragment
    assert "- None" in fragment
    assert "Reference-only related skills" in fragment
    assert "selection:" in fragment


def test_remote_ctf_web_url_soft_selects_ctf_pair_and_suppresses_java_route_tracer() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="解答这道 ctf web 题目: http://target.local/challenge",
        top_k=5,
    )
    solve_challenge = _compiled_skill(
        "solve-challenge",
        when_to_use="Use for CTF challenges and flag-oriented solving.",
        semantic_family="ctf",
        semantic_task_mode="dispatcher",
        semantic_tags=["ctf", "dispatcher", "challenge"],
    )
    ctf_web = _compiled_skill(
        "ctf-web",
        when_to_use="Use for CTF web exploitation against HTTP services.",
        semantic_family="ctf",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["ctf-web", "web"],
    )
    java_route_tracer = _compiled_skill(
        "java-route-tracer",
        when_to_use="Trace Java request mappings and controller flows.",
        semantic_family="java-audit",
        semantic_domain="java",
        semantic_task_mode="audit",
        semantic_tags=["java-audit", "route-trace"],
    )

    result = resolve_skill_candidates([java_route_tracer, ctf_web, solve_challenge], request)

    assert result.intent_profile is not None
    assert result.intent_profile.dominant_domain == "ctf_web"
    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name != "java-route-tracer"
    selected_names = [
        candidate.compiled_skill.directory_name for candidate in result.all_selected_candidates
    ]
    assert "solve-challenge" in selected_names
    assert "ctf-web" in selected_names
    assert result.primary_candidate.selection_explanation
    assert any(candidate.packing_explanation for candidate in result.supporting_candidates)
    rejected = {
        candidate.compiled_skill.directory_name: candidate
        for candidate in result.rejected_candidates
    }
    assert rejected["java-route-tracer"].rejected_reason == "suppressed_by_intent"


def test_clear_specialized_web_task_allows_ctf_web_to_outrank_dispatcher() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="专注分析这个 HTTP challenge 的 Web 漏洞点: http://target.local/challenge",
        top_k=5,
    )
    solve_challenge = _compiled_skill(
        "solve-challenge",
        when_to_use="Use for CTF challenges and flag-oriented solving.",
        semantic_family="ctf",
        semantic_task_mode="dispatcher",
        semantic_tags=["ctf", "dispatcher", "challenge"],
    )
    ctf_web = _compiled_skill(
        "ctf-web",
        when_to_use="Use for CTF web exploitation against HTTP services.",
        semantic_family="ctf",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["ctf-web", "web"],
    )

    result = resolve_skill_candidates([solve_challenge, ctf_web], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "ctf-web"
    selected_names = [
        candidate.compiled_skill.directory_name for candidate in result.all_selected_candidates
    ]
    assert "ctf-web" in selected_names


def test_java_audit_family_is_suppressed_without_java_code_evidence() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Inspect remote HTTP challenge at http://demo"
    )
    java_route_tracer = _compiled_skill(
        "java-route-tracer",
        semantic_family="java-audit",
        semantic_domain="java",
        semantic_task_mode="audit",
    )
    generic = _compiled_skill(
        "generic-recon", when_to_use="Use for generic recon and service review"
    )

    result = resolve_skill_candidates([java_route_tracer, generic], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "generic-recon"
    assert any(
        candidate.compiled_skill.directory_name == "java-route-tracer"
        and candidate.rejected_reason == "suppressed_by_intent"
        for candidate in result.rejected_candidates
    )


def test_local_java_audit_with_code_evidence_allows_java_route_tracer() -> None:
    request = skill_models.SkillResolutionRequest(
        touched_paths=["apps/api/src/main/java/com/demo/UserController.java", "pom.xml"],
        current_prompt="Trace Java controller route mappings in this local project",
    )
    java_route_tracer = _compiled_skill(
        "java-route-tracer",
        semantic_family="java-audit",
        semantic_domain="java",
        semantic_task_mode="audit",
    )
    solve_challenge = _compiled_skill(
        "solve-challenge",
        semantic_family="ctf",
        semantic_task_mode="dispatcher",
    )

    result = resolve_skill_candidates([solve_challenge, java_route_tracer], request)

    assert result.intent_profile is not None
    assert result.intent_profile.dominant_domain in {"java_code_audit", "java_route_trace"}
    selected_names = [
        candidate.compiled_skill.directory_name for candidate in result.all_selected_candidates
    ]
    assert "java-route-tracer" in selected_names
    assert not any(
        candidate.compiled_skill.directory_name == "java-route-tracer"
        and candidate.rejected_reason == "suppressed_by_intent"
        for candidate in result.rejected_candidates
    )


def test_semantic_redundancy_rejects_duplicate_specialized_web_skill() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="专注分析这个 HTTP challenge 的 Web 漏洞点: http://target.local/challenge",
        top_k=5,
    )
    ctf_web = _compiled_skill(
        "ctf-web",
        when_to_use="Use for CTF web exploitation against HTTP services.",
        semantic_family="ctf",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["ctf-web", "web"],
    )
    web_xss = _compiled_skill(
        "web-xss-specialist",
        when_to_use="Use for CTF web exploitation against HTTP services.",
        semantic_family="ctf",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["ctf-web", "web"],
        fingerprint="zzz-web-xss",
    )
    solve_challenge = _compiled_skill(
        "solve-challenge",
        when_to_use="Use for CTF challenges and flag-oriented solving.",
        semantic_family="ctf",
        semantic_task_mode="dispatcher",
        semantic_tags=["ctf", "dispatcher", "challenge"],
    )

    result = resolve_skill_candidates([solve_challenge, web_xss, ctf_web], request)

    selected_names = [
        candidate.compiled_skill.directory_name for candidate in result.all_selected_candidates
    ]
    assert len({"ctf-web", "web-xss-specialist"} & set(selected_names)) == 1
    assert any(
        candidate.rejected_reason in {"redundant_with_primary", "redundant_with_supporting"}
        for candidate in result.rejected_candidates
        if candidate.compiled_skill.directory_name in {"ctf-web", "web-xss-specialist"}
    )


def test_pattt_loader_outranks_generic_skill_for_payload_retrieval_tasks() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Need SSRF payloads and Burp Intruder candidates for verification",
        top_k=5,
    )
    pattt_loader = _compiled_skill(
        "pattt-readme-loader",
        when_to_use=(
            "Use when a task needs payload, bypass, exploit, fuzz, or Burp Intruder guidance "
            "from PayloadsAllTheThings."
        ),
        semantic_family="payloadsallthethings",
        semantic_domain="offensive-knowledge",
        semantic_task_mode="retrieval",
        semantic_tags=["pattt", "payloadsallthethings", "readme-first", "verification"],
    )
    generic = _compiled_skill(
        "generic-recon",
        when_to_use="Use for generic web recon and validation.",
        semantic_family="generic-recon",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["web", "recon"],
    )

    result = resolve_skill_candidates([generic, pattt_loader], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "pattt-readme-loader"


def test_pattt_loader_becomes_supporting_when_concrete_execution_skill_matches() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Validate SSRF on http://target.local using PATTT payloads",
        top_k=5,
    )
    pattt_loader = _compiled_skill(
        "pattt-readme-loader",
        when_to_use=(
            "Use when a task needs payload, bypass, exploit, fuzz, or Burp Intruder guidance "
            "from PayloadsAllTheThings."
        ),
        semantic_family="payloadsallthethings",
        semantic_domain="offensive-knowledge",
        semantic_task_mode="retrieval",
        semantic_tags=["pattt", "payloadsallthethings", "readme-first", "verification"],
    )
    ssrf_validator = _compiled_skill(
        "ssrf-validator",
        when_to_use="Use for SSRF validation against HTTP targets.",
        semantic_family="generic-recon",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["web", "ssrf", "validation"],
    )

    result = resolve_skill_candidates([pattt_loader, ssrf_validator], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "ssrf-validator"
    assert any(
        candidate.compiled_skill.directory_name == "pattt-readme-loader"
        for candidate in result.supporting_candidates
    )


def test_pattt_loader_does_not_overtrigger_for_generic_remote_http_recon() -> None:
    request = skill_models.SkillResolutionRequest(
        current_prompt="Inspect remote HTTP service at http://target.local for generic recon",
        top_k=5,
    )
    pattt_loader = _compiled_skill(
        "pattt-readme-loader",
        when_to_use=(
            "Use when a task needs payload, bypass, exploit, fuzz, or Burp Intruder guidance "
            "from PayloadsAllTheThings."
        ),
        semantic_family="payloadsallthethings",
        semantic_domain="offensive-knowledge",
        semantic_task_mode="retrieval",
        semantic_tags=["pattt", "payloadsallthethings", "readme-first", "verification"],
    )
    generic = _compiled_skill(
        "generic-recon",
        when_to_use="Use for generic web recon and validation.",
        semantic_family="generic-recon",
        semantic_domain="web",
        semantic_task_mode="specialized",
        semantic_tags=["web", "recon"],
    )

    result = resolve_skill_candidates([pattt_loader, generic], request)

    assert result.primary_candidate is not None
    assert result.primary_candidate.compiled_skill.directory_name == "generic-recon"
    assert all(
        candidate.compiled_skill.directory_name != "pattt-readme-loader"
        for candidate in result.all_selected_candidates
    )


def test_session_runner_does_not_overtrigger_pattt_from_stale_recent_context() -> None:
    pattt_skill = SimpleNamespace(
        directory_name="pattt-readme-loader",
        name="pattt-readme-loader",
        family="payloadsallthethings",
        domain="offensive-knowledge",
        task_mode="retrieval",
        description=(
            "README-first PayloadsAllTheThings loader for payload retrieval, Burp Intruder "
            "wordlists, fuzz candidates, verification probes, bypass ideas, exploit-gated research."
        ),
    )
    generic_skill = SimpleNamespace(
        directory_name="generic-recon",
        name="generic-recon",
        family="generic-recon",
        domain="web",
        task_mode="specialized",
        description="Generic recon helper for remote HTTP inspection.",
    )

    selected_skill, route_report = resolve_autorouted_skill_candidate(
        available_skills=[pattt_skill, generic_skill],
        latest_message_text="Inspect remote HTTP service at http://target.local for generic recon",
        recent_context_text="Earlier we discussed SSRF payloads and Burp Intruder candidates.",
    )

    assert selected_skill is generic_skill or selected_skill is None
    assert route_report["top_candidate"] != "pattt-readme-loader"


def _compiled_skill(
    directory_name: str,
    *,
    activation_paths: list[str] | None = None,
    when_to_use: str | None = None,
    agent: str | None = None,
    allowed_tools: list[str] | None = None,
    parameter_schema: dict[str, object] | None = None,
    invocable: bool = True,
    source_kind: skill_models.SkillSourceKind = skill_models.SkillSourceKind.FILESYSTEM,
    fingerprint: str | None = None,
    context_hint: str | None = None,
    semantic_family: str | None = None,
    semantic_domain: str | None = None,
    semantic_task_mode: str | None = None,
    semantic_tags: list[str] | None = None,
) -> skill_models.CompiledSkill:
    return skill_models.CompiledSkill(
        identity=skill_models.SkillSourceIdentity(
            source_kind=source_kind,
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            source_root="skills",
            relative_path=f"{directory_name}/SKILL.md",
            fingerprint=fingerprint or f"hash-{directory_name}",
        ),
        skill_id=f"{directory_name}-id",
        name=directory_name,
        directory_name=directory_name,
        entry_file=f"skills/{directory_name}/SKILL.md",
        description=f"{directory_name} description",
        content=f"# {directory_name}\n",
        compatibility=["opencode"],
        parameter_schema=parameter_schema or {},
        aliases=[],
        allowed_tools=list(allowed_tools or []),
        user_invocable=True,
        argument_hint=None,
        activation_paths=list(activation_paths or []),
        invocable=invocable,
        when_to_use=when_to_use,
        context_hint=context_hint,
        agent=agent,
        effort="medium",
        semantic_family=semantic_family,
        semantic_domain=semantic_domain,
        semantic_task_mode=semantic_task_mode,
        semantic_tags=list(semantic_tags or []),
        loaded_from=f"skills/{directory_name}/SKILL.md",
    )
