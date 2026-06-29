from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    def execute(self, args: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
        pass
