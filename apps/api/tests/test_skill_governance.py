from __future__ import annotations

import json
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
from app.compat.skills.governance_reporting import build_registry_metrics_report
from app.compat.skills.governance_reduce import reduce_governed_skill, restore_reduction
from app.compat.skills.governance_registry import (
    GovernanceRegistryError,
    load_routing_testset,
    load_skill_registry,
    load_task_eval_cases,
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


def test_reference_metadata_defaults_when_frontmatter_is_absent(tmp_path: Path) -> None:
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
    assert reference.when is None
    assert reference.topics == []
    assert reference.cost_hint.value == "unknown"


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
    assert discovery.issues[0].relative_path == "irregular/scripts/SKILL.md"


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
    registry_entries = load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml")
    routing_cases = load_routing_testset(REPO_ROOT / "registry" / "routing-testset.yaml")
    task_cases = load_task_eval_cases(REPO_ROOT / "registry" / "task-eval-set")
    catalog = build_governed_skill_catalog(REPO_ROOT / "skills", registry_entries=registry_entries)

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


def test_registry_metrics_report_uses_registry_families_and_quality_inputs() -> None:
    registry_entries = load_skill_registry(REPO_ROOT / "registry" / "skill-registry.yaml")
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    selected_paths = {entry.path for entry in registry_entries}
    selected_skills = [skill for skill in discovery.skills if skill.relative_path in selected_paths]

    metrics = build_registry_metrics_report(
        skills=selected_skills,
        registry_entries=registry_entries,
        routing_report={"precision": 0.5, "recall": 0.5},
        task_report={"reduced_pass_rate": 0.75, "regression_count": 1},
    )
    quality_metrics = metrics["quality_metrics"]
    governance_metrics = metrics["governance_metrics"]

    assert isinstance(quality_metrics, dict)
    assert isinstance(governance_metrics, dict)

    assert metrics["skill_total"] == 4
    assert metrics["family_total"] == 3
    assert quality_metrics["routing_precision"] == 0.5
    assert quality_metrics["task_pass_rate"] == 0.75
    assert isinstance(governance_metrics["duplicate_content_ratio"], float)
    assert governance_metrics["duplicate_content_ratio"] >= 0.0


def test_lint_flags_missing_reference_metadata(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    _write_file(
        skills_root / "lint-demo" / "SKILL.md",
        "---\nname: lint-demo\ndescription: lint demo skill with enough description tokens for validation\n---\n\n# Demo\n\nUse references/example.md when deeper guidance is needed.",
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
    assert "reference_missing_when" in issue_codes
    assert "reference_missing_topics" in issue_codes
    assert "reference_missing_cost_hint" in issue_codes


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
