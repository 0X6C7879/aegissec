from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.core.settings import REPO_ROOT

_TEMPLATES_ROOT = REPO_ROOT / "config" / "workflows"


@dataclass(frozen=True)
class WorkflowStageTemplate:
    key: str
    title: str
    role: str
    phase: str
    role_prompt: str = ""
    sub_agent_role_prompt: str = ""
    requires_approval: bool = False


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    title: str
    description: str
    template_kinds: tuple[str, ...]
    stages: tuple[WorkflowStageTemplate, ...]


class WorkflowTemplateLoader:
    def __init__(self, templates_root: Path = _TEMPLATES_ROOT) -> None:
        self._templates_root = templates_root

    def list_template_names(self) -> tuple[str, ...]:
        if not self._templates_root.exists():
            return ()
        names = sorted(
            path.stem
            for path in self._templates_root.glob("*.yaml")
            if path.is_file() and self._is_safe_template_name(path.stem)
        )
        return tuple(names)

    def list_templates(self) -> tuple[WorkflowTemplate, ...]:
        templates: list[WorkflowTemplate] = []
        for template_name in self.list_template_names():
            loaded = self.load(template_name)
            if loaded is not None:
                templates.append(loaded)
        return tuple(templates)

    def load(self, template_name: str) -> WorkflowTemplate | None:
        if not self._is_safe_template_name(template_name):
            return None
        template_path = self._templates_root / f"{template_name}.yaml"
        if not template_path.exists():
            return None

        payload = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None

        name = payload.get("name")
        title = payload.get("title", name)
        description = payload.get("description", "")
        raw_template_kinds = payload.get("template_kinds", [])
        raw_stages = payload.get("stages", [])
        if (
            not isinstance(name, str)
            or not isinstance(title, str)
            or not isinstance(description, str)
        ):
            return None
        if not isinstance(raw_template_kinds, list) or not raw_template_kinds:
            return None
        template_kinds = tuple(
            kind for kind in raw_template_kinds if isinstance(kind, str) and kind.strip()
        )
        if len(template_kinds) != len(raw_template_kinds):
            return None
        if not isinstance(raw_stages, list) or not raw_stages:
            return None

        stages: list[WorkflowStageTemplate] = []
        for raw_stage in raw_stages:
            if not isinstance(raw_stage, dict):
                return None
            key = raw_stage.get("key")
            stage_title = raw_stage.get("title")
            role = raw_stage.get("role")
            phase = raw_stage.get("phase")
            role_prompt = raw_stage.get("role_prompt", "")
            sub_agent_role_prompt = raw_stage.get("sub_agent_role_prompt", "")
            requires_approval = raw_stage.get("requires_approval", False)
            if (
                not isinstance(key, str)
                or not isinstance(stage_title, str)
                or not isinstance(role, str)
                or not isinstance(phase, str)
                or not isinstance(role_prompt, str)
                or not isinstance(sub_agent_role_prompt, str)
            ):
                return None
            stages.append(
                WorkflowStageTemplate(
                    key=key,
                    title=stage_title,
                    role=role,
                    phase=phase,
                    role_prompt=role_prompt,
                    sub_agent_role_prompt=sub_agent_role_prompt,
                    requires_approval=bool(requires_approval),
                )
            )

        return WorkflowTemplate(
            name=name,
            title=title,
            description=description,
            template_kinds=template_kinds,
            stages=tuple(stages),
        )

    @staticmethod
    def _is_safe_template_name(template_name: str) -> bool:
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", template_name))
