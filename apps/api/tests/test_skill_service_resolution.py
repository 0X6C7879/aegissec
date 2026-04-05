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
    assert best["status"] == "selected"
    assert resolution.selected_candidate is not None
    assert selected_skill["id"] == resolution.selected_candidate.compiled_skill.skill_id
    assert selected_skill["rank"] == 1
    assert selected_skill["selected"] is True
    assert selected_skill["total_score"] == resolution.shortlisted_candidates[0].total_score
    assert cast(list[object], selected_skill["reasons"])
    assert best["selected_skill_id"] == resolution.selected_candidate.compiled_skill.skill_id
    assert best["selected_skill_rank"] == 1


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


def test_build_skill_context_payload_exposes_selected_skill_and_resolution_identity(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root / "demo" / "SKILL.md",
        """---
name: demo
description: Context skill
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
        payload = skill_service.build_skill_context_payload(session_id="session-context")
        prompt_fragment = skill_service.build_skill_context_prompt_fragment(
            session_id="session-context"
        )
        snapshot = skill_service.build_active_skill_snapshot(session_id="session-context")

    selected_skill = cast(dict[str, object], payload["selected_skill"])
    resolution = cast(dict[str, object], payload["resolution"])
    assert selected_skill["id"] == resolution["selected_skill_id"]
    assert payload["selected_skill_id"] == resolution["selected_skill_id"]
    assert payload["selected_skill_rank"] == selected_skill["rank"]
    assert "Top ranked skills for current context" in prompt_fragment
    assert "demo" in prompt_fragment
    assert snapshot[0]["selected"] is True
