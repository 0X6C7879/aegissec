from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlmodel import Session, SQLModel, create_engine

from app.compat.skills.models import SkillScanRoot
from app.compat.skills.scanner import default_skill_scan_roots, scan_skill_files
from app.compat.skills.service import SkillService
from app.core.settings import Settings
from app.db.models import CompatibilityScope, CompatibilitySource, SkillRecord, SkillRecordStatus


def test_default_skill_scan_roots_only_use_repo_local_skills_directory(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"

    roots = default_skill_scan_roots(repo_root=repo_root)

    assert [(root.source, root.scope, Path(root.root_dir)) for root in roots] == [
        (CompatibilitySource.LOCAL, CompatibilityScope.PROJECT, repo_root / "skills"),
    ]


def test_skills_rescan_lists_repo_local_skills_and_invalid_errors(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "project" / "skills"

    _write_skill(
        local_root / "demo" / "SKILL.md",
        """---
name: demo
description: Claude skill demo
compatibility:
  - claude
metadata:
  owner: team-a
extra_flag: true
---
# Demo

Claude body.
""",
    )
    _write_skill(
        local_root / "scanner" / "SKILL.md",
        """---
description: OpenCode scanner
compatibility: opencode
---
# Scanner

OpenCode body.
""",
    )
    _write_skill(
        local_root / "broken" / "SKILL.md",
        """---
name: broken
description: [unterminated
---
Broken.
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(local_root),
            ),
        ],
    )

    rescan_response = client.post("/api/skills/rescan")

    assert rescan_response.status_code == 200
    records = rescan_response.json()
    assert [(record["source"], record["scope"], record["name"]) for record in records] == [
        ("local", "project", "broken"),
        ("local", "project", "demo"),
        ("local", "project", "scanner"),
    ]

    broken_record = next(record for record in records if record["name"] == "broken")
    assert broken_record["status"] == "invalid"
    assert "Invalid YAML frontmatter" in broken_record["error_message"]

    demo_record = next(record for record in records if record["name"] == "demo")
    assert demo_record["status"] == "loaded"
    assert demo_record["compatibility"] == ["claude"]
    assert demo_record["metadata"] == {"owner": "team-a"}
    assert demo_record["raw_frontmatter"] == {"extra_flag": True}

    list_response = client.get("/api/skills")

    assert list_response.status_code == 200
    assert list_response.json() == records

    detail_response = client.get(f"/api/skills/{demo_record['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["entry_file"].endswith("demo/SKILL.md")


def test_default_roots_ignore_adjacent_compatibility_skill_directories(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    local_root = repo_root / "skills"

    _write_skill(local_root / "local-demo" / "SKILL.md", "# Local Demo")
    _write_skill(repo_root / ".claude" / "skills" / "claude-demo" / "SKILL.md", "# Claude Demo")
    _write_skill(
        repo_root / ".opencode" / "skills" / "opencode-demo" / "SKILL.md", "# OpenCode Demo"
    )
    _write_skill(repo_root / ".agents" / "skills" / "agents-demo" / "SKILL.md", "# Agents Demo")

    discovered = scan_skill_files(default_skill_scan_roots(repo_root=repo_root))

    assert [(item.source, item.scope, item.directory_name) for item in discovered] == [
        (CompatibilitySource.LOCAL, CompatibilityScope.PROJECT, "local-demo"),
    ]


def test_skill_service_lists_loaded_summaries(
    tmp_path: Path,
    test_settings: Settings,
) -> None:
    skills_root = tmp_path / "service-skills"
    adscan_entry = skills_root / "adscan" / "SKILL.md"
    broken_entry = skills_root / "broken" / "SKILL.md"

    _write_skill(
        adscan_entry,
        """---
name: adscan
description: Active Directory 枚举 skill
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration without using ADscan itself.
""",
    )
    _write_skill(broken_entry, "# broken\n")

    with _create_service_session(test_settings, tmp_path / "service.db") as (
        session,
        skill_service,
    ):
        session.add(
            _build_skill_record(
                root_dir=skills_root,
                directory_name="adscan",
                entry_file=adscan_entry,
                name="adscan",
                description="Active Directory 枚举 skill",
                compatibility=["opencode"],
            )
        )
        session.add(
            _build_skill_record(
                root_dir=skills_root,
                directory_name="broken",
                entry_file=broken_entry,
                name="broken",
                description="broken",
                status=SkillRecordStatus.INVALID,
                error_message="Invalid YAML frontmatter",
            )
        )
        session.commit()
        summaries = skill_service.list_loaded_skills_for_agent()

    assert [summary.directory_name for summary in summaries] == ["adscan"]
    assert summaries[0].name == "adscan"
    assert summaries[0].description == "Active Directory 枚举 skill"
    assert summaries[0].compatibility == ["opencode"]
    assert summaries[0].id == "adscan-id"


def test_skill_service_reads_real_skill_markdown(
    tmp_path: Path,
    test_settings: Settings,
) -> None:
    skills_root = tmp_path / "service-skills"
    adscan_entry = skills_root / "adscan" / "SKILL.md"

    _write_skill(
        adscan_entry,
        """---
name: adscan
description: Active Directory 枚举 skill
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration without using ADscan itself.
""",
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (
        session,
        skill_service,
    ):
        session.add(
            _build_skill_record(
                root_dir=skills_root,
                directory_name="adscan",
                entry_file=adscan_entry,
                name="adscan",
                description="Active Directory 枚举 skill",
                compatibility=["opencode"],
            )
        )
        session.commit()
        content = skill_service.read_skill_content("adscan-id")

    assert "name: adscan" in content
    assert "Use when performing Active Directory pentest orchestration" in content


def test_skill_content_endpoint_returns_real_markdown(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    records = _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "adscan": """---
name: adscan
description: Active Directory 枚举 skill
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration without using ADscan itself.
""",
        },
    )
    adscan_id = next(record["id"] for record in records if record["name"] == "adscan")

    content_response = client.get(f"/api/skills/{adscan_id}/content")

    assert content_response.status_code == 200
    payload = content_response.json()
    assert payload["directory_name"] == "adscan"
    assert payload["entry_file"].endswith("adscan/SKILL.md")
    assert "Use when performing Active Directory pentest orchestration" in payload["content"]


def _write_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _seed_skills(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    skill_bodies: dict[str, str],
) -> list[dict[str, object]]:
    local_root = tmp_path / "project" / "skills"
    for directory_name, content in skill_bodies.items():
        _write_skill(local_root / directory_name / "SKILL.md", content)

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(local_root),
            ),
        ],
    )

    rescan_response = client.post("/api/skills/rescan")
    assert rescan_response.status_code == 200
    return cast(list[dict[str, object]], rescan_response.json())


@contextmanager
def _create_service_session(
    test_settings: Settings,
    database_path: Path,
) -> Iterator[tuple[Session, SkillService]]:
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session, SkillService(session, test_settings)
    finally:
        session.close()


def _build_skill_record(
    *,
    root_dir: Path,
    directory_name: str,
    entry_file: Path,
    name: str,
    description: str,
    compatibility: list[str] | None = None,
    status: SkillRecordStatus = SkillRecordStatus.LOADED,
    error_message: str | None = None,
) -> SkillRecord:
    return SkillRecord(
        id=f"{directory_name}-id",
        source=CompatibilitySource.LOCAL,
        scope=CompatibilityScope.PROJECT,
        root_dir=str(root_dir),
        directory_name=directory_name,
        entry_file=str(entry_file),
        name=name,
        description=description,
        compatibility_json=list(compatibility or []),
        metadata_json={},
        raw_frontmatter_json={},
        status=status,
        error_message=error_message,
        content_hash=f"hash-{directory_name}",
    )
