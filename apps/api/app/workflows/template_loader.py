from __future__ import annotations

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
    requires_approval: bool = False


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    title: str
    description: str
    stages: tuple[WorkflowStageTemplate, ...]


class WorkflowTemplateLoader:
    def __init__(self, templates_root: Path = _TEMPLATES_ROOT) -> None:
        self._templates_root = templates_root

    def load(self, template_name: str) -> WorkflowTemplate | None:
        template_path = self._templates_root / f"{template_name}.yaml"
        if not template_path.exists():
            return None

        payload = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None

        name = payload.get("name")
        title = payload.get("title", name)
        description = payload.get("description", "")
        raw_stages = payload.get("stages", [])
        if (
            not isinstance(name, str)
            or not isinstance(title, str)
            or not isinstance(description, str)
        ):
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
            requires_approval = raw_stage.get("requires_approval", False)
            if (
                not isinstance(key, str)
                or not isinstance(stage_title, str)
                or not isinstance(role, str)
            ):
                return None
            stages.append(
                WorkflowStageTemplate(
                    key=key,
                    title=stage_title,
                    role=role,
                    requires_approval=bool(requires_approval),
                )
            )

        return WorkflowTemplate(
            name=name,
            title=title,
            description=description,
            stages=tuple(stages),
        )
