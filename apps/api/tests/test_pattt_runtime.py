from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from app.services.pattt_catalog import build_pattt_catalog
from app.services.pattt_context import resolve_pattt_context


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _build_pattt_fixture(base_path: Path, monkeypatch: MonkeyPatch) -> Path:
    pattt_root = base_path / "knowledge" / "pattt"
    monkeypatch.setenv("AEGISSEC_PATTT_ROOT", str(pattt_root))
    repo_dir = pattt_root / "repo"
    _write_text(
        repo_dir / "Server Side Request Forgery" / "README.md",
        "# SSRF\n## Verification\n- http://169.254.169.254/latest/meta-data/",
    )
    _write_text(
        repo_dir / "Server Side Request Forgery" / "SSRF-Cloud-Instances.md",
        "# SSRF Cloud Instances\n## AWS\n- http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    )
    _write_text(
        repo_dir / "SQL Injection" / "README.md",
        "# SQL Injection\n## Verification\n- ' OR '1'='1",
    )
    _write_text(
        repo_dir / "SQL Injection" / "MySQL Injection.md",
        "# MySQL Injection\n## Verification\n- UNION SELECT @@version",
    )
    _write_text(
        repo_dir / "XSS Injection" / "README.md",
        "# XSS Injection\n## Verification\n- <script>alert(1)</script>",
    )
    _write_text(
        repo_dir / "XSS Injection" / "4 - CSP Bypass.md",
        "# CSP Bypass\n## Bypass\n- strict-dynamic bypass",
    )
    _write_text(
        repo_dir / "Prompt Injection" / "README.md",
        "# Prompt Injection\n## Verification\n- ignore previous instructions",
    )
    _write_text(
        repo_dir / "CVE Exploits" / "README.md",
        "# CVE Exploits\n## Overview\n- curated exploit notes",
    )
    _write_text(
        repo_dir / "CVE Exploits" / "Log4Shell.md",
        (
            "# Log4Shell\n## Verification\n- ${jndi:ldap://example.com/a}"
            "\n## Exploit\n- reverse shell chain"
        ),
    )
    _write_text(
        repo_dir / "Methodology and Resources" / "AWS Pentest.md",
        "# AWS Pentest\n## Methodology\n- enumerate IMDS",
    )
    _write_text(repo_dir / ".source-commit", "fixture-sha\n")
    build_pattt_catalog(
        repo_dir=repo_dir,
        catalog_dir=pattt_root / "catalog",
        repo_root=base_path,
        source_commit="fixture-sha",
    )
    return pattt_root


def test_ssrf_cloud_metadata_reads_canonical_and_child_doc(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(
        request={
            "vuln_family": "ssrf",
            "objective": "verify aws metadata reachability",
            "target_kind": "web",
            "injection_point": "url",
            "stack": ["aws", "http"],
            "constraints": {
                "phase": "verification",
                "explicit_bypass": False,
                "explicit_exploit": False,
            },
            "top_k": {"families": 1, "docs": 2},
        },
        repo_root=tmp_path,
    )
    loaded_paths = [doc.path for doc in context.loaded_docs]
    assert "knowledge/pattt/repo/Server Side Request Forgery/README.md" in loaded_paths
    assert (
        "knowledge/pattt/repo/Server Side Request Forgery/SSRF-Cloud-Instances.md" in loaded_paths
    )
    assert any("169.254.169.254" in doc.content for doc in context.loaded_docs)
    assert any(candidate.risk_tier == "verification" for candidate in context.payload_candidates)
    assert any("raw http" in candidate.tool_hints for candidate in context.payload_candidates)


def test_legacy_protocol_still_works_and_payload_has_compat_aliases(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(
        objective="ssrf aws metadata verification",
        family_hint="ssrf",
        tech_stack=["aws"],
        max_families=1,
        max_docs=2,
        repo_root=tmp_path,
    )
    payload = context.to_payload()
    assert payload["families"] == payload["ranked_families"]
    assert payload["candidates"] == payload["payload_candidates"]
    assert payload["request"]["vuln_family"] == "ssrf"
    assert payload["request"]["top_k"] == {"families": 1, "docs": 2}
    assert payload["loaded_docs"]
    assert all("content" not in doc for doc in payload["loaded_docs"])
    assert all(doc["content_redacted"] is True for doc in payload["loaded_docs"])


def test_legacy_request_object_family_hint_is_backfilled(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(
        request={
            "objective": "ssrf aws metadata verification",
            "family_hint": "ssrf",
            "tech_stack": ["aws"],
            "max_families": 1,
            "max_docs": 2,
        },
        repo_root=tmp_path,
    )
    assert context.request.vuln_family == "ssrf"
    assert context.to_payload()["request"]["family_hint"] == "ssrf"


def test_sql_injection_mysql_reads_expected_docs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(
        objective="mysql sql injection verification",
        repo_root=tmp_path,
    )
    loaded_paths = [doc.path for doc in context.loaded_docs]
    assert "knowledge/pattt/repo/SQL Injection/README.md" in loaded_paths
    assert "knowledge/pattt/repo/SQL Injection/MySQL Injection.md" in loaded_paths
    assert all(candidate.payload == candidate.text for candidate in context.payload_candidates)
    assert any("db error" in candidate.expected_signals for candidate in context.payload_candidates)
    assert any(
        "manual verification" in candidate.tool_hints for candidate in context.payload_candidates
    )


def test_xss_csp_reads_expected_docs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(
        objective="xss csp bypass",
        explicit_bypass=True,
        repo_root=tmp_path,
    )
    loaded_paths = [doc.path for doc in context.loaded_docs]
    assert "knowledge/pattt/repo/XSS Injection/README.md" in loaded_paths
    assert "knowledge/pattt/repo/XSS Injection/4 - CSP Bypass.md" in loaded_paths
    assert any(candidate.risk_tier == "bypass" for candidate in context.payload_candidates)


def test_prompt_injection_reads_readme(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(objective="prompt injection verification", repo_root=tmp_path)
    assert [doc.path for doc in context.loaded_docs] == [
        "knowledge/pattt/repo/Prompt Injection/README.md"
    ]
    assert context.loaded_docs[0].content.startswith("# Prompt Injection")


def test_log4shell_reads_expected_docs_and_gates_exploit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    verification_context = resolve_pattt_context(
        objective="log4shell verification", repo_root=tmp_path
    )
    loaded_paths = [doc.path for doc in verification_context.loaded_docs]
    assert "knowledge/pattt/repo/CVE Exploits/README.md" in loaded_paths
    assert "knowledge/pattt/repo/CVE Exploits/Log4Shell.md" in loaded_paths
    assert all(
        candidate.candidate_type != "exploit"
        for candidate in verification_context.payload_candidates
    )
    assert all(
        candidate.risk_tier != "exploit" for candidate in verification_context.payload_candidates
    )

    exploit_context = resolve_pattt_context(
        objective="log4shell exploit chain",
        explicit_exploit=True,
        repo_root=tmp_path,
    )
    assert any(
        candidate.candidate_type == "exploit" for candidate in exploit_context.payload_candidates
    )
    assert any(candidate.risk_tier == "exploit" for candidate in exploit_context.payload_candidates)
    assert any(
        "command output" in candidate.expected_signals
        for candidate in exploit_context.payload_candidates
    )


def test_methodology_collection_supports_standalone_manuals(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(objective="aws pentest methodology", repo_root=tmp_path)
    assert [doc.path for doc in context.loaded_docs] == [
        "knowledge/pattt/repo/Methodology and Resources/AWS Pentest.md"
    ]
    assert all(
        candidate.source_path == "knowledge/pattt/repo/Methodology and Resources/AWS Pentest.md"
        for candidate in context.payload_candidates
    )


def test_candidate_payloads_are_execution_chain_ready(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(objective="xss verification payload", repo_root=tmp_path)
    assert context.payload_candidates
    payload = context.payload_candidates[0].to_payload()
    assert payload["candidate_id"]
    assert payload["payload"]
    assert payload["text"] == payload["payload"]
    assert payload["risk_tier"] in {"verification", "bypass", "exploit"}
    assert isinstance(payload["expected_signals"], list)
    assert isinstance(payload["tool_hints"], list)
    assert payload["source_path"].startswith("knowledge/pattt/repo/")
    assert payload["section_title"]
