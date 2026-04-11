from __future__ import annotations


def normalize_skill_identifier(identifier: str) -> str:
    stripped = identifier.strip()
    if not stripped:
        return ""

    normalized = stripped.replace("\\", "/").casefold()
    if "/" not in normalized:
        return normalized

    parts = [part.strip() for part in normalized.split("/") if part.strip()]
    return "/".join(parts)


def iter_skill_identifier_candidates(identifier: str) -> tuple[str, ...]:
    normalized = normalize_skill_identifier(identifier)
    if not normalized:
        return ()

    candidates = [normalized]
    if "/" not in normalized:
        return tuple(candidates)

    parts = normalized.split("/")
    for end in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:end])
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)
