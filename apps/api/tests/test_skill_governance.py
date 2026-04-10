from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app.compat.skills.governance_discovery import (
    classify_skill_markdown_path,
    discover_governed_skills,
    stable_governance_skill_id,
)
from app.compat.skills.governance_eval import (
    build_governed_skill_catalog,
    evaluate_routing,
    evaluate_tasks,
)
from app.compat.skills.governance_lint import lint_governed_skills
from app.compat.skills.governance_models import SkillGovernanceStatus
from app.compat.skills.governance_reduce import reduce_governed_skill, restore_reduction
from app.compat.skills.governance_registry import (
    GovernanceRegistryError,
    apply_status_changes,
    derive_status_proposals,
    load_routing_testset,
    load_skill_registry,
    load_task_eval_cases,
    refresh_registry_entries,
    synchronize_registry_entries,
    write_skill_registry,
)
from app.compat.skills.governance_reporting import (
    build_registry_metrics_report,
    build_watch_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_governance_registry_loader_reads_file_based_registry() -> None:
    registry_entries = load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml")

    assert {entry.skill_id for entry in registry_entries} >= {
        "exploit-file-download",
        "static-analysis/semgrep",
        "static-analysis/codeql",
        "wooyun-legacy",
    }
    assert len(registry_entries) >= 100


def test_reference_metadata_is_inferred_when_frontmatter_is_absent(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_file(
        skills_root / "demo" / "SKILL.md",
        "---\nname: demo\ndescription: demo skill\n---\n\n# Demo\n\nCore body.",
    )
    _write_file(
        skills_root / "demo" / "references" / "guide.md", "Reference body without metadata."
    )

    discovery = discover_governed_skills(skills_root)

    assert len(discovery.skills) == 1
    reference = discovery.skills[0].references[0]
    assert reference.when is not None
    assert "guide" in reference.topics
    assert reference.cost_hint.value in {"low", "medium", "high"}


def test_mixed_layout_discovery_reports_irregular_deep_skill_paths(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_file(skills_root / "flat-skill" / "SKILL.md", "# Flat")
    _write_file(skills_root / "family" / "member" / "SKILL.md", "# Direct family")
    _write_file(skills_root / "bundle" / "skills" / "child" / "SKILL.md", "# Nested family")
    _write_file(skills_root / "irregular" / "scripts" / "SKILL.md", "# Ignore me")

    discovery = discover_governed_skills(skills_root)

    assert {skill.relative_path for skill in discovery.skills} == {
        "bundle/skills/child/SKILL.md",
        "family/member/SKILL.md",
        "flat-skill/SKILL.md",
    }
    assert discovery.issues == []


def test_classify_skill_markdown_path_and_stable_id_support_current_and_family_layouts() -> None:
    flat = classify_skill_markdown_path("exploit-file-download/SKILL.md")
    family_direct = classify_skill_markdown_path("family/member/SKILL.md")
    family_nested = classify_skill_markdown_path("static-analysis/skills/semgrep/SKILL.md")

    assert flat is not None and flat.family is None
    assert family_direct is not None and family_direct.family == "family"
    assert family_nested is not None and family_nested.family == "static-analysis"
    assert stable_governance_skill_id("exploit-file-download/SKILL.md") == "exploit-file-download"
    assert (
        stable_governance_skill_id("static-analysis/skills/semgrep/SKILL.md")
        == "static-analysis/semgrep"
    )


def test_governance_loaders_raise_for_invalid_yaml(tmp_path: Path) -> None:
    invalid_file = tmp_path / "invalid.yaml"
    invalid_file.write_text("skills: [unterminated", encoding="utf-8")

    try:
        load_skill_registry(invalid_file)
    except GovernanceRegistryError as exc:
        assert "Invalid YAML" in str(exc)
    else:
        raise AssertionError("Expected GovernanceRegistryError for invalid YAML.")


def test_routing_and_task_eval_produce_deterministic_structured_reports() -> None:
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    registry_entries = synchronize_registry_entries(
        entries=load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml"),
        skills=discovery.skills,
    )
    routing_cases = load_routing_testset(REPO_ROOT / "registry" / "routing-testset.yaml")
    task_cases = load_task_eval_cases(REPO_ROOT / "registry" / "task-eval-set")
    catalog = build_governed_skill_catalog(
        REPO_ROOT / "skills",
        registry_entries=registry_entries,
        allowed_statuses={SkillGovernanceStatus.ACTIVE, SkillGovernanceStatus.WATCH},
    )

    routing_report = evaluate_routing(catalog, routing_cases)
    task_report = evaluate_tasks(catalog, task_cases)

    assert isinstance(routing_report["total_cases"], int)
    assert routing_report["total_cases"] >= 4
    assert isinstance(routing_report["confusion_matrix"], dict)
    assert isinstance(task_report["total_cases"], int)
    assert task_report["total_cases"] >= 4
    assert isinstance(task_report["results"], list)
    json.dumps(routing_report, ensure_ascii=False, sort_keys=True)
    json.dumps(task_report, ensure_ascii=False, sort_keys=True)


def test_registry_synchronization_adds_discovered_skills_with_defaults(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_file(
        skills_root / "seed" / "SKILL.md",
        (
            "---\nname: seed\ndescription: seed skill with enough description tokens "
            "for validation\n---\n\n# Seed\n\nCore body."
        ),
    )
    _write_file(
        skills_root / "extra" / "SKILL.md",
        (
            "---\nname: extra\ndescription: extra skill with enough description tokens "
            "for validation\n---\n\n# Extra\n\nCore body."
        ),
    )

    discovery = discover_governed_skills(skills_root)
    seed_skill = next(skill for skill in discovery.skills if skill.governance_id == "seed")
    synchronized = synchronize_registry_entries(
        entries=[
            replace(
                load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml")[0],
                skill_id="seed",
                path="seed/SKILL.md",
                family=None,
                owner="sec-platform",
                version="1.2.3",
                status=SkillGovernanceStatus.ACTIVE,
            )
        ],
        skills=discovery.skills,
    )

    synchronized_by_id = {entry.skill_id: entry for entry in synchronized}
    assert synchronized_by_id["seed"].status is SkillGovernanceStatus.ACTIVE
    assert synchronized_by_id["seed"].version == "1.2.3"
    assert synchronized_by_id["seed"].description_tokens > 0
    assert synchronized_by_id["extra"].status is SkillGovernanceStatus.INCUBATING
    assert synchronized_by_id["extra"].owner == "sec-platform"
    assert synchronized_by_id["extra"].path == "extra/SKILL.md"
    assert synchronized_by_id["extra"].neighbors == []
    assert synchronized_by_id["seed"].path == seed_skill.relative_path


def test_watch_candidates_skip_incubating_entries_without_eval() -> None:
    registry_entries = load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml")
    sample = replace(
        registry_entries[0],
        skill_id="incubating-demo",
        path="incubating-demo/SKILL.md",
        status=SkillGovernanceStatus.INCUBATING,
        routing_pass_rate=0.0,
        task_pass_rate=0.0,
        route_collision_score=1.0,
        invocation_30d=0,
    )

    watch_candidates = build_watch_candidates([sample])

    assert watch_candidates == []


def test_restore_reduction_rehydrates_missing_terms_from_source_text(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_file(
        skills_root / "restore-demo" / "SKILL.md",
        (
            "---\nname: restore-demo\ndescription: restore demo\n---\n\n# Restore Demo"
            "\n\n核心规则。\n\n这里包含关键术语 restore-anchor。"
        ),
    )

    discovery = discover_governed_skills(skills_root)
    reduction = reduce_governed_skill(discovery.skills[0], max_body_tokens=4)

    restored = restore_reduction(
        reduction,
        source_text=Path(discovery.skills[0].entry_file).read_text(encoding="utf-8"),
        missing_terms=["restore-anchor"],
    )

    assert "restore-anchor" in restored.reduced_body
    assert restored.restored_snippets


def test_task_eval_seed_fixtures_cover_registered_seed_skills() -> None:
    task_cases = load_task_eval_cases(REPO_ROOT / "registry" / "task-eval-set")

    assert {
        "exploit-file-download",
        "static-analysis/codeql",
        "static-analysis/semgrep",
        "wooyun-legacy",
    } <= set(task_cases)
    assert len(task_cases["exploit-file-download"]) >= 5
    assert len(task_cases["static-analysis/codeql"]) >= 5
    assert len(task_cases["static-analysis/semgrep"]) >= 5
    assert len(task_cases["wooyun-legacy"]) >= 5
    for skill_id in (
        "exploit-file-download",
        "static-analysis/codeql",
        "static-analysis/semgrep",
        "wooyun-legacy",
    ):
        mode_counts = {
            "core-only": sum(1 for case in task_cases[skill_id] if case.mode == "core-only"),
            "needs-reference": sum(
                1 for case in task_cases[skill_id] if case.mode == "needs-reference"
            ),
        }
        assert mode_counts["core-only"] >= 3
        assert mode_counts["needs-reference"] >= 2


def test_registry_metrics_report_uses_registry_families_and_quality_inputs() -> None:
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    registry_entries = synchronize_registry_entries(
        entries=load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml"),
        skills=discovery.skills,
    )

    metrics = build_registry_metrics_report(
        skills=discovery.skills,
        registry_entries=registry_entries,
        routing_report={"precision": 0.5, "recall": 0.5},
        task_report={"reduced_pass_rate": 0.75, "regression_count": 1},
    )
    quality_metrics = metrics["quality_metrics"]
    governance_metrics = metrics["governance_metrics"]
    family_total = metrics["family_total"]

    assert isinstance(quality_metrics, dict)
    assert isinstance(governance_metrics, dict)
    assert isinstance(family_total, int)

    assert metrics["skill_total"] == len(discovery.skills)
    assert family_total >= 3
    assert quality_metrics["routing_precision"] == 0.5
    assert quality_metrics["task_pass_rate"] == 0.75
    assert isinstance(governance_metrics["duplicate_content_ratio"], float)
    assert governance_metrics["duplicate_content_ratio"] >= 0.0


def test_lint_flags_missing_reference_metadata(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_file(
        skills_root / "lint-demo" / "SKILL.md",
        (
            "---\nname: lint-demo\ndescription: lint demo skill with enough description "
            "tokens for validation\n---\n\n# Demo\n\nUse references/example.md when "
            "deeper guidance is needed."
        ),
    )
    _write_file(
        skills_root / "lint-demo" / "references" / "example.md",
        "Reference content without frontmatter.",
    )

    discovery = discover_governed_skills(skills_root)
    issues = lint_governed_skills(
        skills=discovery.skills,
        registry_entries=[],
        discovery_issues=discovery.issues,
        strict=False,
    )

    issue_codes = {issue.code for issue in issues}
    assert "reference_missing_when" not in issue_codes
    assert "reference_missing_topics" not in issue_codes
    assert "reference_missing_cost_hint" not in issue_codes


def test_refresh_registry_entries_and_write_round_trip(tmp_path: Path) -> None:
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    registry_entries = synchronize_registry_entries(
        entries=load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml"),
        skills=discovery.skills,
    )
    selected_skills = discovery.skills

    refreshed_entries = refresh_registry_entries(
        entries=registry_entries,
        skills=selected_skills,
        routing_report={
            "reduced": {
                "results": [
                    {"expected_skill_id": "exploit-file-download", "passed": True},
                    {"expected_skill_id": "wooyun-legacy", "passed": False},
                ]
            }
        },
        task_report={
            "results": [
                {"skill_id": "exploit-file-download", "reduced": {"passed": True}},
                {"skill_id": "wooyun-legacy", "reduced": {"passed": False}},
            ]
        },
        last_verified_model="gpt-5.4",
        last_verified_at="2026-04-10T12:00:00Z",
    )

    registry_copy = tmp_path / "skill-registry.yaml"
    write_skill_registry(registry_copy, refreshed_entries)
    reloaded_entries = load_skill_registry(registry_copy)
    reloaded_by_id = {entry.skill_id: entry for entry in reloaded_entries}

    assert reloaded_by_id["exploit-file-download"].last_verified_model == "gpt-5.4"
    assert reloaded_by_id["exploit-file-download"].last_verified_at == "2026-04-10T12:00:00Z"
    assert reloaded_by_id["wooyun-legacy"].routing_pass_rate == 0.0
    assert reloaded_by_id["wooyun-legacy"].task_pass_rate == 0.0


def test_status_proposals_and_application_follow_candidate_flow() -> None:
    registry_entries = synchronize_registry_entries(
        entries=load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml"),
        skills=discover_governed_skills(REPO_ROOT / "skills").skills,
    )
    watch_candidates: list[dict[str, object]] = [
        {"skill_id": "exploit-file-download", "reasons": ["high_route_collision"]}
    ]
    deprecated_candidates: list[dict[str, object]] = [
        {"skill_id": "static-analysis/codeql", "reasons": ["watch_and_high_obsolescence"]}
    ]
    primed_entries = []
    for entry in registry_entries:
        if entry.skill_id == "static-analysis/codeql":
            primed_entries.append(replace(entry, status=SkillGovernanceStatus.WATCH))
        else:
            primed_entries.append(entry)

    proposals = derive_status_proposals(
        entries=primed_entries,
        watch_candidates=watch_candidates,
        deprecated_candidates=deprecated_candidates,
    )
    updated_entries = apply_status_changes(entries=primed_entries, changes=proposals)
    updated_by_id = {entry.skill_id: entry for entry in updated_entries}

    assert any(change["skill_id"] == "exploit-file-download" for change in proposals)
    assert updated_by_id["exploit-file-download"].status.value == "watch"
    assert updated_by_id["static-analysis/codeql"].status.value == "deprecated"


def test_metrics_reference_load_uses_task_report_selected_references() -> None:
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    registry_entries = synchronize_registry_entries(
        entries=load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml"),
        skills=discovery.skills,
    )

    metrics = build_registry_metrics_report(
        skills=discovery.skills,
        registry_entries=registry_entries,
        task_report={
            "results": [
                {"reduced": {"selected_references": ["a"]}},
                {"reduced": {"selected_references": []}},
                {"reduced": {"selected_references": ["a", "b"]}},
            ]
        },
    )

    cost_metrics = metrics["cost_metrics"]
    assert isinstance(cost_metrics, dict)
    assert cost_metrics["average_reference_load_count"] == 1.0


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
