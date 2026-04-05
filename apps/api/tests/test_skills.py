from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlmodel import Session, SQLModel, create_engine

from app.compat.skills.models import DiscoveredSkillFile, SkillScanRoot
from app.compat.skills.parser import parse_skill_file
from app.compat.skills.scanner import (
    compatibility_skill_scan_placeholders,
    default_skill_scan_roots,
    discover_claude_skill_scan_roots,
    scan_skill_files,
)
from app.compat.skills.service import SkillService
from app.core.settings import Settings
from app.db.models import (
    CompatibilityScope,
    CompatibilitySource,
    MCPCapability,
    MCPCapabilityKind,
    MCPServer,
    MCPServerStatus,
    MCPTransport,
    SkillRecord,
    SkillRecordStatus,
)
from app.main import app
from tests.utils import api_data


def test_default_skill_scan_roots_only_use_repo_local_skills_directory(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"

    roots = default_skill_scan_roots(repo_root=repo_root, home_dir=home_dir)

    assert [(root.source, root.scope, Path(root.root_dir)) for root in roots] == [
        (CompatibilitySource.LOCAL, CompatibilityScope.PROJECT, repo_root / "skills"),
        (CompatibilitySource.LOCAL, CompatibilityScope.PROJECT, repo_root / "bundled-skills"),
        (CompatibilitySource.LOCAL, CompatibilityScope.PROJECT, Path("mcp:/skills")),
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
parameter_schema:
  type: object
  properties:
    target:
      type: string
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
    records = api_data(rescan_response)
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
    assert demo_record["parameter_schema"] == {
        "type": "object",
        "properties": {"target": {"type": "string"}},
    }
    assert demo_record["raw_frontmatter"] == {"extra_flag": True}

    list_response = client.get("/api/skills")

    assert list_response.status_code == 200
    assert api_data(list_response) == records

    detail_response = client.get(f"/api/skills/{demo_record['id']}")
    assert detail_response.status_code == 200
    assert api_data(detail_response)["entry_file"].endswith("demo/SKILL.md")


def test_default_roots_ignore_compatibility_skill_directories(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"
    local_root = repo_root / "skills"

    _write_skill(local_root / "local-demo" / "SKILL.md", "# Local Demo")
    _write_skill(repo_root / ".claude" / "skills" / "claude-demo" / "SKILL.md", "# Claude Demo")
    _write_skill(
        repo_root / ".opencode" / "skills" / "opencode-demo" / "SKILL.md", "# OpenCode Demo"
    )
    _write_skill(repo_root / ".agents" / "skills" / "agents-demo" / "SKILL.md", "# Agents Demo")

    discovered = scan_skill_files(default_skill_scan_roots(repo_root=repo_root, home_dir=home_dir))

    assert {(item.source, item.scope, item.directory_name) for item in discovered} == {
        (CompatibilitySource.LOCAL, CompatibilityScope.PROJECT, "local-demo"),
    }


def test_default_skill_scan_roots_can_expand_to_compatibility_and_extra_directories(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"
    extra_root = tmp_path / "shared-skills"

    roots = default_skill_scan_roots(
        repo_root=repo_root,
        home_dir=home_dir,
        include_compatibility_roots=True,
        extra_dirs=[str(extra_root)],
    )

    assert [(root.source, root.scope, Path(root.root_dir), root.root_label) for root in roots] == [
        (
            CompatibilitySource.LOCAL,
            CompatibilityScope.PROJECT,
            repo_root / "skills",
            "repo-skills",
        ),
        (
            CompatibilitySource.LOCAL,
            CompatibilityScope.PROJECT,
            repo_root / "bundled-skills",
            "bundled-skills",
        ),
        (
            CompatibilitySource.LOCAL,
            CompatibilityScope.PROJECT,
            Path("mcp:/skills"),
            "mcp-skills",
        ),
        (
            CompatibilitySource.CLAUDE,
            CompatibilityScope.PROJECT,
            repo_root / ".claude" / "skills",
            "project-claude-skills",
        ),
        (
            CompatibilitySource.CLAUDE,
            CompatibilityScope.USER,
            home_dir / ".claude" / "skills",
            "user-claude-skills",
        ),
        (
            CompatibilitySource.CLAUDE,
            CompatibilityScope.PROJECT,
            repo_root / ".claude" / "commands",
            "project-claude-commands",
        ),
        (
            CompatibilitySource.CLAUDE,
            CompatibilityScope.USER,
            home_dir / ".claude" / "commands",
            "user-claude-commands",
        ),
        (
            CompatibilitySource.LOCAL,
            CompatibilityScope.PROJECT,
            extra_root,
            "configured-extra-skill-dir",
        ),
    ]


def test_compatibility_skill_scan_placeholders_are_disabled_scaffolding(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    home_dir = tmp_path / "home"

    placeholders = compatibility_skill_scan_placeholders(repo_root=repo_root, home_dir=home_dir)

    assert placeholders
    assert all(root.enabled is False for root in placeholders)
    assert any(root.placeholder is True for root in placeholders)
    assert any(root.root_label == "project-claude-commands" for root in placeholders)
    assert any(root.root_label == "mcp-skills" for root in placeholders)


def test_scan_skill_files_only_includes_top_level_skill_directories(tmp_path: Path) -> None:
    local_root = tmp_path / "project" / "skills"
    _write_skill(local_root / "top-level" / "SKILL.md", "# Top level")
    _write_skill(local_root / "nested" / "scripts" / "SKILL.md", "# Nested")

    discovered = scan_skill_files(
        [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(local_root),
            )
        ]
    )

    assert [item.directory_name for item in discovered] == ["top-level"]


def test_scan_skill_files_preserves_relative_path_and_skips_disabled_placeholder_roots(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "project" / "skills"
    _write_skill(local_root / "demo" / "SKILL.md", "# Demo")

    discovered = scan_skill_files(
        [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(local_root),
                root_label="repo-skills",
            ),
            SkillScanRoot(
                source=CompatibilitySource.CLAUDE,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(tmp_path / ".claude" / "commands"),
                root_label="project-claude-commands",
                enabled=False,
                placeholder=True,
            ),
        ]
    )

    assert len(discovered) == 1
    assert discovered[0].relative_path == "demo/SKILL.md"
    assert discovered[0].root_label == "repo-skills"


def test_scan_skill_files_supports_bundled_and_legacy_command_roots(tmp_path: Path) -> None:
    bundled_root = tmp_path / "bundled-skills"
    legacy_root = tmp_path / ".claude" / "commands"
    _write_skill(bundled_root / "triage" / "SKILL.md", "# Bundled")
    _write_skill(legacy_root / "summarize.md", "# Summarize")

    discovered = scan_skill_files(
        [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(bundled_root),
                source_kind=import_module("app.compat.skills.models").SkillSourceKind.BUNDLED,
                root_label="bundled-skills",
            ),
            SkillScanRoot(
                source=CompatibilitySource.CLAUDE,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(legacy_root),
                source_kind=import_module(
                    "app.compat.skills.models"
                ).SkillSourceKind.LEGACY_COMMAND_DIRECTORY,
                root_label="project-claude-commands",
            ),
        ]
    )

    assert [(item.directory_name, item.source_kind.value) for item in discovered] == [
        ("summarize", "legacy_command_directory"),
        ("triage", "bundled"),
    ]


def test_parse_skill_file_supports_claude_alias_fields_and_preserves_unknown_frontmatter(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills"
    entry_file = skill_root / "demo" / "SKILL.md"
    _write_skill(
        entry_file,
        """---
name: demo
description: Claude-compatible skill
aliases: [demo-skill, demo-shortcut]
user-invocable: true
allowed-tools:
  - execute_skill
  - read_skill_content
argument-hint: --target <value>
compatibility:
  - claude
extra_flag: true
---
# Demo
""",
    )

    parsed = parse_skill_file(
        DiscoveredSkillFile(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir=str(skill_root),
            directory_name="demo",
            entry_file=str(entry_file),
            relative_path="demo/SKILL.md",
        )
    )

    assert parsed.status == SkillRecordStatus.LOADED
    assert parsed.aliases == ["demo-skill", "demo-shortcut"]
    assert parsed.user_invocable is True
    assert parsed.allowed_tools == ["execute_skill", "read_skill_content"]
    assert parsed.argument_hint == "--target <value>"
    assert parsed.raw_frontmatter == {"extra_flag": True}
    assert parsed.source_identity is not None
    assert parsed.source_identity.relative_path == "demo/SKILL.md"


def test_skill_service_lists_loaded_summaries(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
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
aliases: [ad-enum]
user-invocable: true
allowed-tools: [execute_skill, read_skill_content]
argument-hint: --target <host>
---
# adscan

Use when performing Active Directory pentest orchestration without using ADscan itself.
""",
    )
    _write_skill(broken_entry, "# broken\n")

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            ),
        ],
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
        summaries = skill_service.list_loaded_skills_for_agent(session_id="session-skill-summary")

    assert [summary.directory_name for summary in summaries] == ["adscan"]
    assert summaries[0].name == "adscan"
    assert summaries[0].description == "Active Directory 枚举 skill"
    assert summaries[0].compatibility == ["opencode"]
    assert summaries[0].id == "adscan-id"
    assert summaries[0].source == CompatibilitySource.LOCAL
    assert summaries[0].scope == CompatibilityScope.PROJECT
    assert summaries[0].source_kind == "filesystem"
    assert summaries[0].invocable is True
    assert summaries[0].user_invocable is True
    assert summaries[0].aliases == ["ad-enum"]
    assert summaries[0].allowed_tools == ["execute_skill", "read_skill_content"]
    assert summaries[0].argument_hint == "--target <host>"
    assert summaries[0].shell_enabled is True
    assert summaries[0].execution_mode == "reference_only"
    resolved_identity = summaries[0].resolved_identity
    assert resolved_identity["relative_path"] == "adscan/SKILL.md"
    assert summaries[0].prepared_invocation is not None
    prepared_invocation = summaries[0].prepared_invocation
    prepared_request = cast(dict[str, object], prepared_invocation["request"])
    assert prepared_request["session_id"] == "session-skill-summary"


def test_skill_service_reads_real_skill_markdown(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
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

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            ),
        ],
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


def test_skill_service_executes_skill_with_prepared_execution_contract(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
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

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            ),
        ],
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
        result = skill_service.execute_skill_by_name_or_directory_name("adscan")

    assert result["execution"]["status"] == "prepared"
    assert result["execution"]["mode"] == "server_skill_executor_facade"
    assert result["execution"]["available_tools"] == [
        "execute_kali_command",
        "list_available_skills",
        "execute_skill",
        "read_skill_content",
    ]
    assert result["execution"]["resolved_identity"]["relative_path"] == "adscan/SKILL.md"
    assert result["execution"]["prepared_prompt"].startswith("Auto-selected skill: adscan")
    assert "## Prepared skill context: adscan" in result["execution"]["prepared_prompt"]
    assert result["skill"]["directory_name"] == "adscan"
    assert (
        "Use when performing Active Directory pentest orchestration" in result["skill"]["content"]
    )


def test_compiler_registers_alias_ready_reference_only_skill(tmp_path: Path) -> None:
    skills_root = tmp_path / "service-skills"
    entry_file = skills_root / "demo" / "SKILL.md"
    _write_skill(
        entry_file,
        """---
name: demo
description: Demo skill
aliases:
  - demo-skill
user_invocable: true
allowed_tools:
  - execute_skill
argument_hint: --target
---
# Demo

Body.
""",
    )

    compiler_module = import_module("app.compat.skills.compiler")
    compiled = compiler_module.compile_skill_record(
        _build_skill_record(
            root_dir=skills_root,
            directory_name="demo",
            entry_file=entry_file,
            name="demo",
            description="Demo skill",
        ),
        entry_file.read_text(encoding="utf-8"),
    )

    assert compiled.execution_mode.value == "reference_only"
    assert compiled.aliases == ["demo-skill"]
    assert compiled.user_invocable is True
    assert compiled.allowed_tools == ["execute_skill"]
    assert "Argument hint: --target" in compiled.prepared_prompt


def test_compiler_prepares_substitutions_and_shell_expansion_requests(tmp_path: Path) -> None:
    skills_root = tmp_path / "service-skills"
    entry_file = skills_root / "demo" / "SKILL.md"
    _write_skill(
        entry_file,
        """---
name: demo
description: Demo skill
---
# Demo

Session: ${CLAUDE_SESSION_ID}
Skill dir: ${CLAUDE_SKILL_DIR}
Target: ${target}
!pwd

```!
ls -la
whoami
```
""",
    )

    compiler_module = import_module("app.compat.skills.compiler")
    models_module = import_module("app.compat.skills.models")
    compiled = compiler_module.compile_skill_record(
        _build_skill_record(
            root_dir=skills_root,
            directory_name="demo",
            entry_file=entry_file,
            name="demo",
            description="Demo skill",
        ),
        entry_file.read_text(encoding="utf-8"),
        models_module.SkillInvocationRequest(arguments={"target": "api"}, session_id="session-123"),
    )

    assert compiled.prepared_invocation is not None
    prepared_invocation = compiled.prepared_invocation
    assert "Session: session-123" in prepared_invocation.prompt_text
    assert (
        f"Skill dir: {(entry_file.parent.resolve().as_posix())}" in prepared_invocation.prompt_text
    )
    assert "Target: api" in prepared_invocation.prompt_text
    assert [item.kind.value for item in prepared_invocation.shell_expansions] == [
        "inline",
        "fenced",
    ]
    assert prepared_invocation.shell_expansions[0].command == "pwd"
    assert prepared_invocation.shell_expansions[1].command == "ls -la\nwhoami"
    assert all(item.status == "pending_approval" for item in prepared_invocation.shell_expansions)
    assert all(
        action.status == "pending_approval" for action in prepared_invocation.pending_actions
    )


def test_compiler_disables_shell_expansion_for_mcp_origin_skill(tmp_path: Path) -> None:
    entry_file = tmp_path / "mcp-demo" / "SKILL.md"
    _write_skill(
        entry_file,
        """---
name: mcp-demo
description: MCP-origin demo skill
---
# MCP Demo

!pwd
""",
    )

    compiler_module = import_module("app.compat.skills.compiler")
    compiled = compiler_module.compile_skill_record(
        SkillRecord(
            id="mcp-demo-id",
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.PROJECT,
            root_dir="mcp://skills",
            directory_name="mcp-demo",
            entry_file=str(entry_file),
            name="mcp-demo",
            description="MCP-origin demo skill",
            compatibility_json=[],
            metadata_json={},
            parameter_schema_json={},
            raw_frontmatter_json={},
            status=SkillRecordStatus.LOADED,
            enabled=True,
            content_hash="hash-mcp-demo",
        ),
        entry_file.read_text(encoding="utf-8"),
    )

    assert compiled.shell_enabled is False
    assert compiled.prepared_invocation is not None
    assert len(compiled.prepared_invocation.shell_expansions) == 1
    expansion = compiled.prepared_invocation.shell_expansions[0]
    assert expansion.shell_allowed is False
    assert expansion.reason == "shell_disabled_for_source_kind"
    assert expansion.status == "disabled"


def test_skill_service_only_auto_activates_conditional_paths_on_match(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    skills_root = project_root / "skills"
    _write_skill(
        skills_root / "always-on" / "SKILL.md",
        """---
name: always-on
description: Always available
---
# Always available
""",
    )
    _write_skill(
        skills_root / "api-skill" / "SKILL.md",
        """---
name: api-skill
description: API only skill
paths:
  - apps/api/**
---
# API skill
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(skills_root),
            ),
            *([] if not discovery_paths else discover_claude_skill_scan_roots(discovery_paths)),
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (_, skill_service):
        skill_service.rescan_skills()

        assert [
            summary.directory_name for summary in skill_service.list_loaded_skills_for_agent()
        ] == ["always-on"]

        active = skill_service.list_active_compiled_skills(
            touched_paths=[str(project_root / "apps" / "api" / "app" / "main.py")],
            workspace_path=str(project_root),
        )
        payload = skill_service.build_skill_context_payload(
            touched_paths=[str(project_root / "apps" / "api" / "app" / "main.py")],
            workspace_path=str(project_root),
            session_id="session-conditional",
        )

    active_directory_names = {skill.directory_name for skill in active}
    assert {"always-on", "api-skill"}.issubset(active_directory_names)
    payload_skills = cast(list[dict[str, object]], payload["skills"])
    api_skill = next(item for item in payload_skills if item["directory_name"] == "api-skill")
    assert api_skill["conditional"] is True
    assert api_skill["active_due_to_touched_paths"] is True
    prepared_invocation = cast(dict[str, object], api_skill["prepared_invocation"])
    prepared_request = cast(dict[str, object], prepared_invocation["request"])
    assert prepared_request["session_id"] == "session-conditional"


def test_skill_service_can_discover_nested_claude_skills_dynamically(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    project_root = tmp_path / "workspace"
    feature_root = project_root / "src" / "feature"
    workspace_file = feature_root / "target.py"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("print('ok')\n", encoding="utf-8")

    dynamic_skill_entry = project_root / ".claude" / "skills" / "nested-demo" / "SKILL.md"
    _write_skill(
        dynamic_skill_entry,
        """---
name: nested-demo
description: Dynamically discovered Claude skill
---
# nested-demo
""",
    )

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings, discovery_paths=None: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(tmp_path / "empty-skills"),
            ),
            *([] if not discovery_paths else discover_claude_skill_scan_roots(discovery_paths)),
        ],
    )

    with _create_service_session(test_settings, tmp_path / "service.db") as (_, skill_service):
        compiled = skill_service.find_compiled_skill_by_name_or_directory_name(
            "nested-demo",
            workspace_path=str(workspace_file),
        )

    assert compiled is not None
    assert compiled.directory_name == "nested-demo"
    assert compiled.identity.source.value == "claude"
    assert compiled.entry_file.endswith("nested-demo/SKILL.md")


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
    payload = api_data(content_response)
    assert payload["directory_name"] == "adscan"
    assert payload["entry_file"].endswith("adscan/SKILL.md")
    assert payload["parameter_schema"] == {}
    assert "Use when performing Active Directory pentest orchestration" in payload["content"]


def test_skill_context_endpoint_returns_structured_and_prompt_fragments(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "demo": """---
name: demo
description: Demo skill
aliases: [demo-short]
user-invocable: true
allowed-tools: [execute_skill]
argument-hint: --command <value>
parameter_schema:
  type: object
  properties:
    command:
      type: string
---
# Demo
""",
        },
    )

    response = client.get("/api/skills/skill-context")
    assert response.status_code == 200
    payload = api_data(response)
    assert payload["payload"]["skills"][0]["directory_name"] == "demo"
    assert payload["payload"]["skills"][0]["parameter_schema"]["type"] == "object"
    assert payload["payload"]["skills"][0]["invocable"] is True
    assert payload["payload"]["skills"][0]["user_invocable"] is True
    assert payload["payload"]["skills"][0]["aliases"] == ["demo-short"]
    assert payload["payload"]["skills"][0]["allowed_tools"] == ["execute_skill"]
    assert payload["payload"]["skills"][0]["argument_hint"] == "--command <value>"
    assert payload["payload"]["skills"][0]["source_kind"] == "filesystem"
    assert payload["payload"]["skills"][0]["loaded_from"].endswith("demo/SKILL.md")
    assert payload["payload"]["skills"][0]["active"] is True
    assert payload["payload"]["skills"][0]["dynamic"] is False
    assert payload["payload"]["skills"][0]["prepared_invocation"]["context"]["skill_directory"]
    assert "Loaded skills context" in payload["prompt_fragment"]
    assert "execute_skill" in payload["prompt_fragment"]
    assert "compiled metadata only" in payload["prompt_fragment"]
    assert "prepared=shell_expansions=0,pending_actions=0" in payload["prompt_fragment"]


def test_skill_toggle_persists_across_rescan_and_scan_aliases(
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
""",
        },
    )
    adscan_id = next(record["id"] for record in records if record["name"] == "adscan")

    toggle_response = client.post(f"/api/skills/{adscan_id}/toggle", json={"enabled": False})
    assert toggle_response.status_code == 200
    assert api_data(toggle_response)["enabled"] is False

    scan_response = client.post("/api/skills/scan")
    assert scan_response.status_code == 200
    rescanned = api_data(scan_response)
    adscan_record = next(record for record in rescanned if record["id"] == adscan_id)
    assert adscan_record["enabled"] is False

    loaded_summaries = _list_agent_loaded_skill_directory_names(client)
    assert "adscan" not in loaded_summaries

    refresh_response = client.post("/api/skills/refresh")
    assert refresh_response.status_code == 200
    refreshed = api_data(refresh_response)
    refreshed_adscan = next(record for record in refreshed if record["id"] == adscan_id)
    assert refreshed_adscan["enabled"] is False


def test_skills_endpoints_hide_records_outside_supported_scan_roots(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    visible_records = _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "demo": """---
name: demo
description: Demo skill
---
# demo
""",
        },
    )
    visible_skill_id = next(record["id"] for record in visible_records if record["name"] == "demo")

    external_root = tmp_path / "external" / ".claude" / "skills"
    external_entry = external_root / "adapt" / "SKILL.md"
    _write_skill(external_entry, "# adapt")

    database_engine = app.state.database_engine
    with Session(database_engine) as session:
        session.add(
            _build_skill_record(
                root_dir=external_root,
                directory_name="adapt",
                entry_file=external_entry,
                name="adapt",
                description="External stale skill",
                source=CompatibilitySource.CLAUDE,
                status=SkillRecordStatus.IGNORED,
                error_message="Skill entry was not found in latest scan.",
            )
        )
        session.commit()

    list_response = client.get("/api/skills")
    assert list_response.status_code == 200
    listed_records = cast(list[dict[str, object]], api_data(list_response))
    assert {record["id"] for record in listed_records} == {visible_skill_id}
    assert all(record["source"] == "local" for record in listed_records)

    hidden_detail_response = client.get("/api/skills/adapt-id")
    assert hidden_detail_response.status_code == 404


def test_skills_rescan_includes_bundled_and_mcp_bridge_records(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "project" / "skills"
    bundled_root = tmp_path / "project" / "bundled-skills"
    _write_skill(local_root / "demo" / "SKILL.md", "# demo")
    _write_skill(
        bundled_root / "bundle-demo" / "SKILL.md",
        """---
name: bundle-demo
description: Bundled demo
when-to-use: When bundled guidance is needed.
agent: bundle-agent
effort: medium
---
# bundle-demo
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
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(bundled_root),
                source_kind=import_module("app.compat.skills.models").SkillSourceKind.BUNDLED,
                root_label="bundled-skills",
            ),
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir="mcp://skills",
                source_kind=import_module("app.compat.skills.models").SkillSourceKind.MCP,
                root_label="mcp-skills",
            ),
        ],
    )

    database_engine = app.state.database_engine
    with Session(database_engine) as session:
        session.add(
            MCPServer(
                id="server-1",
                name="Burp Suite",
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                transport=MCPTransport.STDIO,
                enabled=True,
                timeout_ms=30_000,
                status=MCPServerStatus.CONNECTED,
                config_path="manual://burp-suite",
            )
        )
        session.add(
            MCPCapability(
                id="cap-1",
                server_id="server-1",
                kind=MCPCapabilityKind.TOOL,
                name="repeater.send",
                title="Send with Repeater",
                description="Inspect and send a request through Repeater.",
                metadata_json={},
                input_schema_json={"type": "object", "properties": {"request": {"type": "string"}}},
                raw_payload_json={},
            )
        )
        session.commit()

    rescan_response = client.post("/api/skills/rescan")
    assert rescan_response.status_code == 200
    records = cast(list[dict[str, object]], api_data(rescan_response))

    bundled_record = next(record for record in records if record["directory_name"] == "bundle-demo")
    assert bundled_record["source_kind"] == "bundled"
    assert bundled_record["when_to_use"] == "When bundled guidance is needed."
    assert bundled_record["agent"] == "bundle-agent"
    assert bundled_record["effort"] == "medium"
    assert str(bundled_record["loaded_from"]).endswith("bundle-demo/SKILL.md")

    mcp_record = next(
        record for record in records if str(record["directory_name"]).startswith("mcp-burp-suite")
    )
    assert mcp_record["source_kind"] == "mcp"
    assert mcp_record["dynamic"] is True
    assert mcp_record["invocable"] is False
    assert mcp_record["shell_enabled"] is False
    assert mcp_record["allowed_tools"] == ["call_mcp_tool", "read_skill_content"]
    resolved_identity = cast(dict[str, object], mcp_record["resolved_identity"])
    assert resolved_identity["source_root"] == "mcp://skills/server-1"

    detail_response = client.get(f"/api/skills/{mcp_record['id']}")
    assert detail_response.status_code == 200
    assert api_data(detail_response)["source_kind"] == "mcp"

    content_response = client.get(f"/api/skills/{mcp_record['id']}/content")
    assert content_response.status_code == 200
    content_payload = api_data(content_response)
    assert content_payload["source_kind"] == "mcp"
    assert content_payload["invocable"] is False
    assert "conservative MCP compatibility bridge" in content_payload["content"]


def test_skills_rescan_hides_deleted_records_in_supported_scan_root(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "project" / "skills"
    demo_entry = local_root / "demo" / "SKILL.md"
    _write_skill(
        demo_entry,
        """---
name: demo
description: Demo skill
---
# demo
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

    first_rescan = client.post("/api/skills/rescan")
    assert first_rescan.status_code == 200
    original_records = cast(list[dict[str, object]], api_data(first_rescan))
    demo_id = next(record["id"] for record in original_records if record["name"] == "demo")

    demo_entry.unlink()

    second_rescan = client.post("/api/skills/rescan")
    assert second_rescan.status_code == 200
    assert api_data(second_rescan) == []

    list_response = client.get("/api/skills")
    assert list_response.status_code == 200
    assert api_data(list_response) == []

    detail_response = client.get(f"/api/skills/{demo_id}")
    assert detail_response.status_code == 404


def _list_agent_loaded_skill_directory_names(client: TestClient) -> list[str]:
    del client
    database_engine = app.state.database_engine
    with Session(database_engine) as session:
        service = SkillService(session, cast(Settings, app.state.settings))
        return [summary.directory_name for summary in service.list_loaded_skills_for_agent()]


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
    return cast(list[dict[str, object]], api_data(rescan_response))


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
    source: CompatibilitySource = CompatibilitySource.LOCAL,
    scope: CompatibilityScope = CompatibilityScope.PROJECT,
    status: SkillRecordStatus = SkillRecordStatus.LOADED,
    error_message: str | None = None,
) -> SkillRecord:
    return SkillRecord(
        id=f"{directory_name}-id",
        source=source,
        scope=scope,
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
