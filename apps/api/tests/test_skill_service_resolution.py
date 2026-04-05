from pathlib import Path
from typing import cast

from pytest import MonkeyPatch

from app.compat.skills.models import SkillScanRoot
from app.core.settings import Settings
from app.db.models import CompatibilityScope, CompatibilitySource, SkillRecord, SkillRecordStatus
from tests.test_skills import _create_service_session, _write_skill


def test_resolve_best_skill_returns_top_ranked_executable_candidate(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    project_root = tmp_path / "workspace"
    skills_root = project_root / "skills"
    touched_file = project_root / "apps" / "api" / "app" / "main.py"
    touched_file.parent.mkdir(parents=True, exist_ok=True)
    touched_file.write_text("print('ok')\n", encoding="utf-8")

    _write_skill(
        skills_root / "always-on" / "SKILL.md",
        """---
name: always-on
description: General fallback skill
---
# always-on
""",
    )
    _write_skill(
        skills_root / "api-skill" / "SKILL.md",
        """---
name: api-skill
description: API specific skill
paths:
  - apps/api/**
parameter_schema:
  type: object
  required: [target]
  properties:
    target:
      type: string
---
# api-skill
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            )
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (_, skill_service):
        skill_service.rescan_skills()
        resolution = skill_service.resolve_skill_candidates(
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            session_id="session-top1",
            invocation_arguments={"target": "demo"},
        )
        best = skill_service.resolve_best_skill(
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            session_id="session-top1",
            invocation_arguments={"target": "demo"},
        )

    selected_skill = cast(dict[str, object], best["selected_skill"])
    primary_skill = cast(dict[str, object], best["primary_skill"])
    selected_skill_ids = cast(list[str], best["selected_skill_ids"])
    assert best["status"] == "selected"
    assert resolution.selected_candidate is not None
    assert selected_skill["id"] == resolution.selected_candidate.compiled_skill.skill_id
    assert primary_skill["id"] == selected_skill["id"]
    assert selected_skill["rank"] == 1
    assert selected_skill["selected"] is True
    assert resolution.primary_candidate is not None
    assert selected_skill["total_score"] == resolution.primary_candidate.total_score
    assert cast(list[object], selected_skill["reasons"])
    assert best["selected_skill_id"] == resolution.selected_candidate.compiled_skill.skill_id
    assert best["selected_skill_rank"] == 1
    assert selected_skill["id"] in selected_skill_ids


def test_resolve_best_skill_returns_reference_only_status_when_only_reference_candidates_exist(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    entry_file = skills_root / "reference-only" / "SKILL.md"
    _write_skill(
        entry_file,
        """---
name: reference-only
description: Reference only skill
---
    # reference-only
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            )
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (
        session,
        skill_service,
    ):
        session.add(
            SkillRecord(
                id="reference-only-id",
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
                directory_name="reference-only",
                entry_file=str(entry_file),
                name="reference-only",
                description="Reference only skill",
                compatibility_json=["opencode"],
                metadata_json={},
                raw_frontmatter_json={"_compat": {"invocable": False}},
                status=SkillRecordStatus.LOADED,
                error_message=None,
                content_hash="hash-reference-only",
            )
        )
        session.commit()

        best = skill_service.resolve_best_skill(current_prompt="Need reference only skill")

    assert best["status"] == "reference_only_only"
    assert best["selected_skill"] is None
    assert best["selected_skill_id"] is None
    resolution_summary = cast(dict[str, object], best["resolution_summary"])
    assert resolution_summary["rejected_count"] == 1
    resolution = cast(dict[str, object], best["resolution"])
    rejected = cast(list[dict[str, object]], resolution["rejected_candidates"])
    assert rejected[0]["rejected_reason"] == "reference_only_excluded"


def test_prepare_best_skill_returns_prepared_execution_payload(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    entry_file = skills_root / "demo" / "SKILL.md"
    _write_skill(
        entry_file,
        """---
name: demo
description: Prepared execution skill
parameter_schema:
  type: object
  required: [target]
  properties:
    target:
      type: string
---
# demo
Target ${target}
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            )
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (_, skill_service):
        skill_service.rescan_skills()
        prepared = skill_service.prepare_best_skill(
            current_prompt="Need demo skill",
            invocation_arguments={"target": "srv-01"},
            session_id="session-prepared",
        )

    execution = cast(dict[str, object], prepared["execution"])
    prepared_invocation = cast(dict[str, object], execution["prepared_invocation"])
    assert prepared["status"] == "selected"
    assert execution["status"] == "prepared"
    assert execution["mode"] == "server_skill_executor_facade"
    assert isinstance(execution["prepared_prompt"], str) and execution["prepared_prompt"]
    assert cast(dict[str, object], prepared_invocation["request"])["arguments"] == {
        "target": "srv-01"
    }


def test_prepare_best_skill_includes_primary_supporting_and_summary(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    project_root = tmp_path / "workspace"
    skills_root = project_root / "skills"
    touched_file = project_root / "apps" / "api" / "app" / "main.py"
    touched_file.parent.mkdir(parents=True, exist_ok=True)
    touched_file.write_text("print('ok')\n", encoding="utf-8")

    _write_skill(
        skills_root / "triage-planner" / "SKILL.md",
        """---
name: triage-planner
description: General triage planner
when_to_use: Use for triage planning and general validation.
---
# triage-planner
""",
    )
    _write_skill(
        skills_root / "api-skill" / "SKILL.md",
        """---
name: api-skill
description: API specific skill
paths:
  - apps/api/**
when_to_use: Use for API validation and endpoint review.
parameter_schema:
  type: object
  required: [target]
  properties:
    target:
      type: string
---
# api-skill
Target ${target}
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            )
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service-prepared.db") as (
        _,
        skill_service,
    ):
        skill_service.rescan_skills()
        prepared = skill_service.prepare_best_skill(
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            current_prompt="Need triage planning and API validation",
            invocation_arguments={"target": "srv-01"},
            session_id="session-prepared-multi",
        )

    assert cast(dict[str, object], prepared["primary_skill"])["directory_name"] == "api-skill"
    supporting_names = [
        skill["directory_name"]
        for skill in cast(list[dict[str, object]], prepared["supporting_skills"])
    ]
    selected_skill_ids = cast(list[str], prepared["selected_skill_ids"])
    assert "triage-planner" in supporting_names
    assert cast(dict[str, object], prepared["primary_skill"])["id"] in selected_skill_ids
    resolution_summary = cast(dict[str, object], prepared["resolution_summary"])
    supporting_count = cast(int, resolution_summary["supporting_count"])
    assert supporting_count >= 1


def test_build_skill_context_payload_exposes_supporting_selected_and_resolution_identity(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    project_root = tmp_path / "workspace"
    skills_root = project_root / "skills"
    touched_file = project_root / "apps" / "api" / "app" / "main.py"
    touched_file.parent.mkdir(parents=True, exist_ok=True)
    touched_file.write_text("print('ok')\n", encoding="utf-8")
    _write_skill(
        skills_root / "triage-planner" / "SKILL.md",
        """---
name: triage-planner
description: Context triage skill
when_to_use: Use for triage planning and general validation.
---
# triage-planner
""",
    )
    _write_skill(
        skills_root / "demo" / "SKILL.md",
        """---
name: demo
description: Context API skill
paths:
  - apps/api/**
when_to_use: Use for API validation and endpoint review.
---
# demo
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            )
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (_, skill_service):
        skill_service.rescan_skills()
        payload = skill_service.build_skill_context_payload(
            session_id="session-context",
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            current_prompt="Need triage planning and API validation",
        )
        prompt_fragment = skill_service.build_skill_context_prompt_fragment(
            session_id="session-context",
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            current_prompt="Need triage planning and API validation",
        )
        snapshot = skill_service.build_active_skill_snapshot(
            session_id="session-context",
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            current_prompt="Need triage planning and API validation",
        )
        loaded = skill_service.list_loaded_skills_for_agent(
            session_id="session-context",
            touched_paths=[str(touched_file)],
            workspace_path=str(project_root),
            current_prompt="Need triage planning and API validation",
        )

    selected_skill = cast(dict[str, object], payload["selected_skill"])
    resolution = cast(dict[str, object], payload["resolution"])
    assert selected_skill["id"] == resolution["selected_skill_id"]
    assert payload["selected_skill_id"] == resolution["selected_skill_id"]
    assert payload["selected_skill_rank"] == selected_skill["rank"]
    assert cast(dict[str, object], payload["primary_skill"])["directory_name"] == "demo"
    supporting_skills = cast(list[dict[str, object]], payload["supporting_skills"])
    supporting_names = [item["directory_name"] for item in supporting_skills]
    selected_skills = cast(list[dict[str, object]], payload["selected_skills"])
    selected_names = [item["directory_name"] for item in selected_skills]
    assert "triage-planner" in supporting_names
    assert selected_names[0] == "demo"
    assert "triage-planner" in selected_names
    selected_skill_ids = cast(list[str], payload["selected_skill_ids"])
    selected_skill_id = cast(str, payload["selected_skill_id"])
    assert selected_skill_id in selected_skill_ids
    rejected_skills = cast(list[dict[str, object]], payload["rejected_skills"])
    assert isinstance(rejected_skills, list)
    assert all(item["role"] == "rejected" for item in rejected_skills)
    assert all(item["rejected_reason"] for item in rejected_skills)
    assert "Primary skill for current context" in prompt_fragment
    assert "Supporting skills also loaded" in prompt_fragment
    assert "demo" in prompt_fragment
    assert snapshot[0]["selected"] is True
    assert snapshot[0]["role"] == "primary"
    loaded_names = [item.directory_name for item in loaded]
    loaded_roles = [item.role for item in loaded]
    assert loaded_names[0] == "demo"
    assert "triage-planner" in loaded_names
    assert loaded_roles[0] == "primary"
    assert "supporting" in loaded_roles
