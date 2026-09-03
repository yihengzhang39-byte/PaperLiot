"""Name-to-callable registry for runtime tools."""

from collections.abc import Callable, Mapping
from typing import Any


Tool = Callable[..., Any]


class ToolRegistry:
    """Store exact tool names and their callable implementations."""

    def __init__(self, tools: Mapping[str, Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for name, tool in (tools or {}).items():
            self.register(name, tool)

    @classmethod
    def from_mapping(cls, tools: Mapping[str, Tool]) -> "ToolRegistry":
        """Create a registry from the Phase 1 tools mapping."""
        return cls(tools)

    def register(self, name: str, tool: Tool) -> None:
        """Register one callable under its exact tool name."""
        if not name:
            raise ValueError("Tool name cannot be empty.")
        if not callable(tool):
            raise TypeError(f"Tool {name!r} must be callable.")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        """Return a tool by name, or None when it is not registered."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Return whether a tool is registered under this exact name."""
        return name in self._tools

    def names(self) -> tuple[str, ...]:
        """Return registered names in registration order."""
        return tuple(self._tools)
