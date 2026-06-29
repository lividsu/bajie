from typing import Any
from .base import Tool, ToolSpec


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def to_prompt_summary(self) -> str:
        lines = []
        for spec in self.list_specs():
            lines.append(f"- {spec.name}: {spec.description}")
            if spec.parameters:
                lines.append(f"  params: {spec.parameters}")
        return "\n".join(lines)

    def execute(self, name: str, args: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        return tool.execute(args=args, runtime=runtime)
