from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.compat.skills.models import SkillScanRoot
from app.compat.skills.scanner import default_skill_scan_roots, scan_skill_files
from app.db.models import CompatibilityScope, CompatibilitySource


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


def _write_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
