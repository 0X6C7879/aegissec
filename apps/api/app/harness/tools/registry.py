from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from .base import BaseTool, NoOpToolExecutionHooks, ToolExecutionHooks


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: OrderedDict[str, BaseTool[Any]] = OrderedDict()

    def register(self, tool: BaseTool[Any]) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool[Any] | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool[Any]]:
        return list(self._tools.values())

    def update(self, tools: Iterable[BaseTool[Any]]) -> None:
        for tool in tools:
            self.register(tool)

    def to_openai_tools_schema(self) -> list[dict[str, object]]:
        return [tool.to_openai_tool_schema() for tool in self._tools.values()]

    def to_anthropic_tools_schema(self) -> list[dict[str, object]]:
        return [tool.to_anthropic_tool_schema() for tool in self._tools.values()]


class ToolHookRegistry:
    def __init__(self) -> None:
        self._global_hooks: list[ToolExecutionHooks] = []
        self._tool_hooks: dict[str, list[ToolExecutionHooks]] = {}

    def register_global(self, hook: ToolExecutionHooks) -> None:
        self._global_hooks.append(hook)

    def register_for_tool(self, tool_name: str, hook: ToolExecutionHooks) -> None:
        self._tool_hooks.setdefault(tool_name, []).append(hook)

    def iter_hooks(self, tool_name: str) -> Iterable[ToolExecutionHooks]:
        registered_hooks = [*self._global_hooks, *self._tool_hooks.get(tool_name, [])]
        if registered_hooks:
            return registered_hooks
        return [NoOpToolExecutionHooks()]
