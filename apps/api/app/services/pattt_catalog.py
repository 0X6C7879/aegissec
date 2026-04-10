from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

DEFAULT_PATTT_UPSTREAM = "swisskyrepo/PayloadsAllTheThings"
DEFAULT_PATTT_BRANCH = "master"
# PATTT validator output is intentionally richer than build metadata.
PATTT_ROOT_ENV = "AEGISSEC_PATTT_ROOT"
IGNORED_TOP_LEVEL_DIRS = frozenset({".github", "_template_vuln", "_LEARNING_AND_SOCIALS"})
IGNORED_TOP_LEVEL_FILES = frozenset({"README.md", "LICENSE", "mkdocs.yml", "CONTRIBUTING.md"})
ASSET_DIRECTORY_NAMES = frozenset({"files", "images", "intruder", "intruders"})
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CODE_FENCE_RE = re.compile(r"^(```|~~~)")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-/+.#]{1,}")
ACRONYM_OVERRIDES = {
    "server-side-request-forgery": "ssrf",
    "sql-injection": "sqli",
    "cross-site-scripting": "xss",
    "xss-injection": "xss",
    "xml-external-entity": "xxe",
    "xxe-injection": "xxe",
    "server-side-template-injection": "ssti",
    "prompt-injection": "prompt-injection",
    "json-web-token": "jwt",
}


@dataclass(frozen=True, slots=True)
class PatttPaths:
    repo_root: Path
    pattt_root: Path
    repo_dir: Path
    catalog_dir: Path
    cache_dir: Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def get_pattt_paths(
    *,
    repo_root: Path | None = None,
    pattt_root: Path | None = None,
) -> PatttPaths:
    resolved_repo_root = (repo_root or get_repo_root()).resolve()
    resolved_pattt_root = pattt_root
    env_override_active = False
    if resolved_pattt_root is None:
        env_pattt_root = os.environ.get(PATTT_ROOT_ENV)
        if env_pattt_root:
            resolved_pattt_root = Path(env_pattt_root)
            env_override_active = True
    resolved_pattt_root = (
        resolved_pattt_root or (resolved_repo_root / "knowledge" / "pattt")
    ).resolve()
    if repo_root is None and pattt_root is None and env_override_active:
        resolved_repo_root = resolved_pattt_root.parents[1]
    return PatttPaths(
        repo_root=resolved_repo_root,
        pattt_root=resolved_pattt_root,
        repo_dir=resolved_pattt_root / "repo",
        catalog_dir=resolved_pattt_root / "catalog",
        cache_dir=resolved_pattt_root / "cache" / "readme-cache",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_repo_path(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().casefold()).strip("-")
    return normalized or "item"


def _tokenize_text(value: str) -> set[str]:
    lowered = value.casefold().replace("&", " and ")
    tokens = {_slugify(match.group(0)).replace("-", "") for match in WORD_RE.finditer(lowered)}
    return {token for token in tokens if token}


def _family_aliases(display_name: str, *, extra_aliases: list[str] | None = None) -> list[str]:
    aliases = {
        display_name.casefold(),
        _slugify(display_name),
        display_name.casefold().replace("-", " "),
    }
    words = [part for part in re.split(r"[^A-Za-z0-9]+", display_name) if part]
    if len(words) > 1:
        acronym = "".join(word[0] for word in words).casefold()
        if len(acronym) > 1:
            aliases.add(acronym)
    slug = _slugify(display_name)
    override = ACRONYM_OVERRIDES.get(slug)
    if override:
        aliases.add(override)
    if extra_aliases:
        aliases.update(alias.casefold() for alias in extra_aliases if alias.strip())
    return sorted(alias for alias in aliases if alias)


def _iter_top_level_directories(repo_dir: Path) -> list[Path]:
    directories: list[Path] = []
    for path in sorted(repo_dir.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_dir():
            continue
        if path.name.startswith("."):
            continue
        if path.name in IGNORED_TOP_LEVEL_DIRS:
            continue
        directories.append(path)
    return directories


def _iter_family_markdown_files(family_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in family_dir.rglob("*.md")
            if all(not part.startswith(".") for part in path.relative_to(family_dir).parts)
        ],
        key=lambda path: path.as_posix().casefold(),
    )


def _iter_family_assets(family_dir: Path) -> list[Path]:
    assets: list[Path] = []
    for path in sorted(family_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(family_dir).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        if path.suffix.casefold() == ".md":
            continue
        assets.append(path)
    return assets


def _detect_layout(*, has_readme: bool, child_docs: list[Path], assets: list[Path]) -> str:
    if has_readme and child_docs and assets:
        return "readme_with_child_docs_and_assets"
    if has_readme and child_docs:
        return "readme_with_child_docs"
    if has_readme and assets:
        return "readme_with_assets"
    if has_readme:
        return "readme_only"
    return "standalone_manual"


def _doc_title_and_sections(text: str, *, doc_id: str) -> tuple[str, list[dict[str, Any]], int]:
    lines = text.splitlines()
    headings: list[tuple[int, list[str], int]] = []
    stack: list[str] = []
    in_fence = False
    code_block_count = 0
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if CODE_FENCE_RE.match(stripped):
            code_block_count += 1
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading_match = HEADING_RE.match(stripped)
        if heading_match is None:
            continue
        level = len(heading_match.group(1))
        title = heading_match.group(2).strip()
        while len(stack) >= level:
            stack.pop()
        stack.append(title)
        headings.append((level, list(stack), line_number))

    sections: list[dict[str, Any]] = []
    if not headings:
        title = _first_non_empty_line(lines) or doc_id
        sections.append(
            {
                "section_id": f"{doc_id}:root",
                "heading_path": title,
                "line_start": 1,
                "line_end": max(len(lines), 1),
                "keywords": sorted(_tokenize_text(title)),
            }
        )
        return title, sections, code_block_count // 2

    title = headings[0][1][-1]
    for index, (_, heading_path, line_start) in enumerate(headings):
        next_line_start = headings[index + 1][2] if index + 1 < len(headings) else len(lines) + 1
        heading_text = " > ".join(heading_path)
        sections.append(
            {
                "section_id": f"{doc_id}:{index + 1}",
                "heading_path": heading_text,
                "line_start": line_start,
                "line_end": max(line_start, next_line_start - 1),
                "keywords": sorted(_tokenize_text(heading_text)),
            }
        )
    return title, sections, code_block_count // 2


def _first_non_empty_line(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


def _asset_group(path: Path) -> str:
    for part in path.parts:
        normalized = part.casefold()
        if normalized in ASSET_DIRECTORY_NAMES:
            return normalized
    return "auxiliary"


def _family_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(file_sha256(path).encode("utf-8"))
    return digest.hexdigest()


def _repo_fingerprint(repo_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(repo_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(repo_dir).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        digest.update(path.relative_to(repo_dir).as_posix().encode("utf-8"))
        digest.update(file_sha256(path).encode("utf-8"))
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _build_family_breakdown(families: list[dict[str, Any]]) -> dict[str, int]:
    canonical_readme_family_count = 0
    readme_with_child_manuals_family_count = 0
    readme_only_family_count = 0
    standalone_manual_family_count = 0
    for family in families:
        canonical_doc = str(family.get("canonical_doc") or "")
        child_docs = family.get("child_docs") if isinstance(family.get("child_docs"), list) else []
        if canonical_doc.endswith("/README.md"):
            canonical_readme_family_count += 1
            if child_docs:
                readme_with_child_manuals_family_count += 1
            else:
                readme_only_family_count += 1
        else:
            standalone_manual_family_count += 1
    return {
        "canonical_readme_family_count": canonical_readme_family_count,
        "readme_with_child_manuals_family_count": readme_with_child_manuals_family_count,
        "readme_only_family_count": readme_only_family_count,
        "standalone_manual_family_count": standalone_manual_family_count,
    }


def build_pattt_catalog(
    *,
    repo_dir: Path | None = None,
    catalog_dir: Path | None = None,
    repo_root: Path | None = None,
    source_commit: str | None = None,
) -> dict[str, Any]:
    paths = get_pattt_paths(repo_root=repo_root)
    resolved_repo_dir = (repo_dir or paths.repo_dir).resolve()
    resolved_catalog_dir = (catalog_dir or paths.catalog_dir).resolve()
    resolved_catalog_dir.mkdir(parents=True, exist_ok=True)
    if not resolved_repo_dir.exists():
        raise FileNotFoundError(f"PATTT repo directory not found: {resolved_repo_dir}")

    families: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    alias_map: dict[str, list[str]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    ignored_directories = sorted(IGNORED_TOP_LEVEL_DIRS)
    repo_root_path = paths.repo_root

    for family_dir in _iter_top_level_directories(resolved_repo_dir):
        markdown_files = _iter_family_markdown_files(family_dir)
        asset_files = _iter_family_assets(family_dir)
        readme_path = family_dir / "README.md"
        has_readme = readme_path in markdown_files

        if not markdown_files:
            message = f"Top-level directory '{family_dir.name}' has no markdown entrypoint."
            if asset_files:
                errors.append(message + " Assets exist without an entrypoint doc.")
            else:
                errors.append(message)
            continue

        if has_readme:
            family_id = _slugify(family_dir.name)
            child_docs = [path for path in markdown_files if path != readme_path]
            family_assets = [_relative_repo_path(path, repo_root_path) for path in asset_files]
            family_record = {
                "family_id": family_id,
                "display_name": family_dir.name,
                "root_dir": _relative_repo_path(family_dir, repo_root_path),
                "layout": _detect_layout(
                    has_readme=True,
                    child_docs=child_docs,
                    assets=asset_files,
                ),
                "canonical_doc": _relative_repo_path(readme_path, repo_root_path),
                "child_docs": [_relative_repo_path(path, repo_root_path) for path in child_docs],
                "assets": family_assets,
                "aliases": _family_aliases(family_dir.name),
                "sha256": _family_fingerprint(markdown_files + asset_files),
            }
            families.append(family_record)
            for alias in family_record["aliases"]:
                alias_map.setdefault(alias, []).append(family_id)
            for path in markdown_files:
                text = path.read_text(encoding="utf-8")
                doc_id = hashlib.sha1(
                    _relative_repo_path(path, repo_root_path).encode("utf-8")
                ).hexdigest()[:16]
                title, parsed_sections, code_block_count = _doc_title_and_sections(
                    text, doc_id=doc_id
                )
                kind = "canonical" if path.name == "README.md" else "child_manual"
                doc_record = {
                    "doc_id": doc_id,
                    "family_id": family_id,
                    "path": _relative_repo_path(path, repo_root_path),
                    "kind": kind,
                    "title": title,
                    "aliases": _family_aliases(title),
                    "heading_count": len(parsed_sections),
                    "code_block_count": code_block_count,
                    "word_count": len(WORD_RE.findall(text)),
                    "sha256": file_sha256(path),
                }
                docs.append(doc_record)
                for alias in cast(list[str], doc_record["aliases"]):
                    alias_map.setdefault(alias, []).append(family_id)
                for section in parsed_sections:
                    sections.append(
                        {
                            "doc_id": doc_id,
                            "family_id": family_id,
                            "path": doc_record["path"],
                            **section,
                            "sha256": doc_record["sha256"],
                        }
                    )
            for asset_path in asset_files:
                assets.append(
                    {
                        "family_id": family_id,
                        "path": _relative_repo_path(asset_path, repo_root_path),
                        "root_dir": _relative_repo_path(family_dir, repo_root_path),
                        "asset_group": _asset_group(asset_path.relative_to(family_dir)),
                        "sha256": file_sha256(asset_path),
                    }
                )
            continue

        for markdown_path in markdown_files:
            standalone_family_id = f"{_slugify(family_dir.name)}__{_slugify(markdown_path.stem)}"
            standalone_assets = (
                [_relative_repo_path(path, repo_root_path) for path in asset_files]
                if markdown_path == markdown_files[0]
                else []
            )
            family_record = {
                "family_id": standalone_family_id,
                "display_name": f"{family_dir.name} / {markdown_path.stem}",
                "root_dir": _relative_repo_path(family_dir, repo_root_path),
                "layout": "standalone_manual",
                "canonical_doc": _relative_repo_path(markdown_path, repo_root_path),
                "child_docs": [],
                "assets": standalone_assets,
                "aliases": _family_aliases(
                    markdown_path.stem,
                    extra_aliases=[family_dir.name, f"{family_dir.name} {markdown_path.stem}"],
                ),
                "sha256": _family_fingerprint([markdown_path]),
            }
            families.append(family_record)
            for alias in cast(list[str], family_record["aliases"]):
                alias_map.setdefault(alias, []).append(standalone_family_id)

            text = markdown_path.read_text(encoding="utf-8")
            doc_id = hashlib.sha1(
                _relative_repo_path(markdown_path, repo_root_path).encode("utf-8")
            ).hexdigest()[:16]
            title, parsed_sections, code_block_count = _doc_title_and_sections(text, doc_id=doc_id)
            doc_record = {
                "doc_id": doc_id,
                "family_id": standalone_family_id,
                "path": _relative_repo_path(markdown_path, repo_root_path),
                "kind": "standalone_manual",
                "title": title,
                "aliases": _family_aliases(title, extra_aliases=[family_dir.name]),
                "heading_count": len(parsed_sections),
                "code_block_count": code_block_count,
                "word_count": len(WORD_RE.findall(text)),
                "sha256": file_sha256(markdown_path),
            }
            docs.append(doc_record)
            for alias in cast(list[str], doc_record["aliases"]):
                alias_map.setdefault(alias, []).append(standalone_family_id)
            for section in parsed_sections:
                sections.append(
                    {
                        "doc_id": doc_id,
                        "family_id": standalone_family_id,
                        "path": doc_record["path"],
                        **section,
                        "sha256": doc_record["sha256"],
                    }
                )
        if markdown_files:
            standalone_asset_owner = (
                f"{_slugify(family_dir.name)}__{_slugify(markdown_files[0].stem)}"
            )
            for asset_path in asset_files:
                assets.append(
                    {
                        "family_id": standalone_asset_owner,
                        "path": _relative_repo_path(asset_path, repo_root_path),
                        "root_dir": _relative_repo_path(family_dir, repo_root_path),
                        "asset_group": _asset_group(asset_path.relative_to(family_dir)),
                        "sha256": file_sha256(asset_path),
                    }
                )

    for alias, family_ids in list(alias_map.items()):
        alias_map[alias] = sorted(set(family_ids))

    docs.sort(key=lambda row: (str(row["path"]).casefold(), str(row["doc_id"])))
    families.sort(key=lambda row: (str(row["root_dir"]).casefold(), str(row["family_id"])))
    sections.sort(key=lambda row: (str(row["path"]).casefold(), int(row["line_start"])))
    assets.sort(key=lambda row: str(row["path"]).casefold())

    build_meta = {
        "source": DEFAULT_PATTT_UPSTREAM,
        "source_branch": DEFAULT_PATTT_BRANCH,
        "source_commit": source_commit or _read_source_commit(resolved_repo_dir),
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_dir": _relative_repo_path(resolved_repo_dir, repo_root_path),
        "repo_fingerprint": _repo_fingerprint(resolved_repo_dir),
        "family_count": len(families),
        "doc_count": len(docs),
        "section_count": len(sections),
        "asset_count": len(assets),
        **_build_family_breakdown(families),
        "ignored_directories": ignored_directories,
        "errors": errors,
        "warnings": warnings,
    }

    _write_json(resolved_catalog_dir / "families.json", families)
    _write_jsonl(resolved_catalog_dir / "docs.jsonl", docs)
    _write_jsonl(resolved_catalog_dir / "sections.jsonl", sections)
    _write_jsonl(resolved_catalog_dir / "assets.jsonl", assets)
    _write_json(resolved_catalog_dir / "aliases.json", alias_map)
    _write_json(resolved_catalog_dir / "build-meta.json", build_meta)

    return {
        "ok": not errors,
        "families": families,
        "docs": docs,
        "sections": sections,
        "assets": assets,
        "aliases": alias_map,
        "build_meta": build_meta,
    }


def _read_source_commit(repo_dir: Path) -> str:
    commit_file = repo_dir / ".source-commit"
    if commit_file.exists():
        return commit_file.read_text(encoding="utf-8").strip() or "unknown"
    legacy_version_file = repo_dir / ".version"
    if legacy_version_file.exists():
        return legacy_version_file.read_text(encoding="utf-8").strip() or "unknown"
    return "unknown"


def load_pattt_catalog(*, repo_root: Path | None = None) -> dict[str, Any]:
    paths = get_pattt_paths(repo_root=repo_root)
    catalog_dir = paths.catalog_dir
    return {
        "families": _load_json(catalog_dir / "families.json"),
        "docs": _load_jsonl(catalog_dir / "docs.jsonl"),
        "sections": _load_jsonl(catalog_dir / "sections.jsonl"),
        "assets": _load_jsonl(catalog_dir / "assets.jsonl"),
        "aliases": _load_json(catalog_dir / "aliases.json"),
        "build_meta": _load_json(catalog_dir / "build-meta.json"),
    }


def validate_pattt_catalog(
    *,
    repo_dir: Path | None = None,
    catalog_dir: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    paths = get_pattt_paths(repo_root=repo_root)
    resolved_repo_dir = (repo_dir or paths.repo_dir).resolve()
    _ = (catalog_dir or paths.catalog_dir).resolve()
    catalog = load_pattt_catalog(repo_root=repo_root)

    errors: list[str] = []
    catalog_docs = {str(row["path"]): row for row in catalog["docs"]}
    live_docs = {
        _relative_repo_path(path, paths.repo_root): path
        for family_dir in _iter_top_level_directories(resolved_repo_dir)
        for path in _iter_family_markdown_files(family_dir)
    }
    docs_disk_mismatches = sorted(set(catalog_docs) ^ set(live_docs))
    if docs_disk_mismatches:
        errors.append("docs.jsonl does not match markdown files on disk.")
    catalog_assets = {str(row["path"]): row for row in catalog["assets"]}
    live_assets = {
        _relative_repo_path(path, paths.repo_root): path
        for family_dir in _iter_top_level_directories(resolved_repo_dir)
        for path in _iter_family_assets(family_dir)
    }
    assets_disk_mismatches = sorted(set(catalog_assets) ^ set(live_assets))
    if assets_disk_mismatches:
        errors.append("assets.jsonl does not match asset files on disk.")
    catalog_families = {str(row["family_id"]): row for row in catalog["families"]}
    if not catalog_families:
        errors.append("families.json is empty.")

    missing_entrypoint_directories: list[str] = []
    for family_dir in _iter_top_level_directories(resolved_repo_dir):
        markdown_files = _iter_family_markdown_files(family_dir)
        if markdown_files:
            continue
        relative_dir = _relative_repo_path(family_dir, paths.repo_root)
        missing_entrypoint_directories.append(relative_dir)
        if _iter_family_assets(family_dir):
            errors.append(f"Top-level directory missing entrypoint docs: {relative_dir}")

    for doc in catalog["docs"]:
        if doc["path"].endswith("/README.md") and doc["kind"] != "canonical":
            errors.append(f"README not marked canonical: {doc['path']}")
    for family in catalog["families"]:
        canonical_doc = family.get("canonical_doc")
        if not isinstance(canonical_doc, str) or not canonical_doc:
            errors.append(f"Family missing entrypoint doc: {family['family_id']}")
    doc_ids_with_sections = {str(row["doc_id"]) for row in catalog["sections"]}
    for doc in catalog["docs"]:
        if doc["kind"] == "canonical" and doc["doc_id"] not in doc_ids_with_sections:
            errors.append(f"Canonical doc missing section index: {doc['path']}")

    build_meta = catalog["build_meta"]
    live_fingerprint = _repo_fingerprint(resolved_repo_dir)
    fingerprint_matches = str(build_meta.get("repo_fingerprint") or "") == live_fingerprint
    if not fingerprint_matches:
        errors.append("Catalog fingerprint does not match repo content on disk.")

    family_breakdown = _build_family_breakdown(catalog["families"])

    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": list(catalog["build_meta"].get("warnings", [])),
        "family_count": len(catalog["families"]),
        **family_breakdown,
        "doc_count": len(catalog["docs"]),
        "section_count": len(catalog["sections"]),
        "asset_count": len(catalog["assets"]),
        "missing_entrypoint_directories": missing_entrypoint_directories,
        "docs_disk_mismatches": docs_disk_mismatches,
        "assets_disk_mismatches": assets_disk_mismatches,
        "fingerprint_matches": fingerprint_matches,
        "ignored_directories": list(catalog["build_meta"].get("ignored_directories", [])),
        "repo_fingerprint": live_fingerprint,
    }
    return report
