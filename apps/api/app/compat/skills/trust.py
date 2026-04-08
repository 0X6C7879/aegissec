from __future__ import annotations

from dataclasses import dataclass


def resolve_effective_trust_level(*, source: str, source_kind: str) -> str:
    if source_kind == "mcp":
        return "external_mcp"
    if source_kind == "bundled":
        return "bundled_trusted"
    if source == "claude":
        return "project_imported"
    return "local_trusted"


@dataclass(slots=True)
class SkillTrustMetadata:
    verification_mode: str | None = None
    shell_profile: str | None = None
    trust_level: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.verification_mode is not None:
            payload["verification_mode"] = self.verification_mode
        if self.shell_profile is not None:
            payload["shell_profile"] = self.shell_profile
        if self.trust_level is not None:
            payload["trust_level"] = self.trust_level
        return payload

    @property
    def is_empty(self) -> bool:
        return not any((self.verification_mode, self.shell_profile, self.trust_level))
