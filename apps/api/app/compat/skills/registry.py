from __future__ import annotations

from dataclasses import dataclass, field

from app.compat.skills import models as skill_models
from app.compat.skills.identifiers import (
    iter_skill_identifier_candidates,
    normalize_skill_identifier,
)


@dataclass(slots=True)
class SkillRegistryEntry:
    compiled_skill: skill_models.CompiledSkill
    alias_tokens: tuple[str, ...] = field(default_factory=tuple)


class CompiledSkillRegistry:
    def __init__(self) -> None:
        self._entries_by_identity: dict[tuple[str, str, str, str], SkillRegistryEntry] = {}
        self._identity_by_skill_id: dict[str, tuple[str, str, str, str]] = {}
        self._identity_by_token: dict[str, tuple[str, str, str, str]] = {}

    def register(self, compiled_skill: skill_models.CompiledSkill) -> SkillRegistryEntry:
        identity_key = compiled_skill.identity.dedup_key
        if identity_key in self._entries_by_identity:
            existing_entry = self._entries_by_identity[identity_key]
            if (
                self._preferred_entry(compiled_skill, existing_entry.compiled_skill)
                is existing_entry.compiled_skill
            ):
                self._register_tokens(existing_entry.compiled_skill, identity_key)
                return existing_entry

        entry = SkillRegistryEntry(compiled_skill=compiled_skill)
        self._entries_by_identity[identity_key] = entry
        entry.alias_tokens = self._register_tokens(compiled_skill, identity_key)
        return entry

    def get_by_token(self, token: str) -> skill_models.CompiledSkill | None:
        for candidate in iter_skill_identifier_candidates(token):
            identity_key = self._identity_by_skill_id.get(candidate)
            if identity_key is not None:
                entry = self._entries_by_identity.get(identity_key)
                if entry is not None:
                    return entry.compiled_skill
            identity_key = self._identity_by_token.get(candidate)
            if identity_key is None:
                continue
            entry = self._entries_by_identity.get(identity_key)
            if entry is not None:
                return entry.compiled_skill
        return None

    def list_entries(self) -> list[SkillRegistryEntry]:
        return list(self._entries_by_identity.values())

    def list_compiled_skills(self) -> list[skill_models.CompiledSkill]:
        return [entry.compiled_skill for entry in self.list_entries()]

    def list_unconditional_skills(self) -> list[skill_models.CompiledSkill]:
        return [skill for skill in self.list_compiled_skills() if not skill.is_conditional]

    def list_conditional_skills(self) -> list[skill_models.CompiledSkill]:
        return [skill for skill in self.list_compiled_skills() if skill.is_conditional]

    def activate_for_touched_paths(
        self, touched_paths: list[str]
    ) -> list[skill_models.CompiledSkill]:
        activated: list[skill_models.CompiledSkill] = []
        for skill in self.list_compiled_skills():
            if skill_models.skill_matches_touched_paths(skill, touched_paths):
                activated.append(skill)
        return activated

    def _register_tokens(
        self,
        compiled_skill: skill_models.CompiledSkill,
        identity_key: tuple[str, str, str, str],
    ) -> tuple[str, ...]:
        tokens: list[str] = []
        normalized_skill_id = normalize_skill_identifier(compiled_skill.skill_id)
        if normalized_skill_id:
            existing_identity = self._identity_by_skill_id.get(normalized_skill_id)
            if existing_identity is None:
                self._identity_by_skill_id[normalized_skill_id] = identity_key
            else:
                existing_entry = self._entries_by_identity.get(existing_identity)
                if existing_entry is not None and (
                    self._preferred_entry(compiled_skill, existing_entry.compiled_skill)
                    is compiled_skill
                ):
                    self._identity_by_skill_id[normalized_skill_id] = identity_key
            if normalized_skill_id not in self._identity_by_token:
                self._identity_by_token[normalized_skill_id] = identity_key
                tokens.append(normalized_skill_id)

        for candidate in (
            compiled_skill.directory_name,
            compiled_skill.name,
            *compiled_skill.aliases,
        ):
            normalized = normalize_skill_identifier(candidate)
            if not normalized or normalized in self._identity_by_token:
                continue
            self._identity_by_token[normalized] = identity_key
            tokens.append(normalized)
        return tuple(tokens)

    @staticmethod
    def _preferred_entry(
        left: skill_models.CompiledSkill, right: skill_models.CompiledSkill
    ) -> skill_models.CompiledSkill:
        left_score = (
            int(bool(left.aliases)),
            len(left.content),
        )
        right_score = (
            int(bool(right.aliases)),
            len(right.content),
        )
        return left if left_score >= right_score else right
