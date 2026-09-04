"""Name-to-callable registry for runtime tools."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


Tool = Callable[..., Any]
ConcurrencySafety = Callable[[dict[str, Any]], bool]
HistoryResultProjector = Callable[[dict[str, Any], Any], dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    """One callable plus the runtime state it consumes and exposes."""

    tool: Tool
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    is_concurrency_safe: ConcurrencySafety | None = None
    project_history_result: HistoryResultProjector | None = None


class ToolRegistry:
    """Store exact tool names and their callable implementations."""

    def __init__(self, tools: Mapping[str, Tool] | None = None) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for name, tool in (tools or {}).items():
            self.register(name, tool)

    @classmethod
    def from_mapping(cls, tools: Mapping[str, Tool]) -> "ToolRegistry":
        """Create a registry from the Phase 1 tools mapping."""
        return cls(tools)

    def register(
        self,
        name: str,
        tool: Tool,
        *,
        requires: Iterable[str] = (),
        produces: Iterable[str] = (),
        is_concurrency_safe: ConcurrencySafety | None = None,
        project_history_result: HistoryResultProjector | None = None,
    ) -> None:
        """Register one callable under its exact tool name."""
        if not name:
            raise ValueError("Tool name cannot be empty.")
        if not callable(tool):
            raise TypeError(f"Tool {name!r} must be callable.")
        if is_concurrency_safe is not None and not callable(is_concurrency_safe):
            raise TypeError(f"Tool {name!r} concurrency safety must be callable.")
        if project_history_result is not None and not callable(project_history_result):
            raise TypeError(f"Tool {name!r} history projector must be callable.")
        self._definitions[name] = ToolDefinition(
            tool=tool,
            requires=self._state_names(requires, "requires"),
            produces=self._state_names(produces, "produces"),
            is_concurrency_safe=is_concurrency_safe,
            project_history_result=project_history_result,
        )

    @staticmethod
    def _state_names(values: Iterable[str], field: str) -> tuple[str, ...]:
        names = tuple(values)
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError(f"Tool {field} must contain non-empty state names.")
        return tuple(dict.fromkeys(name.strip() for name in names))

    def get(self, name: str) -> Tool | None:
        """Return a tool by name, or None when it is not registered."""
        definition = self._definitions.get(name)
        return definition.tool if definition else None

    def definition(self, name: str) -> ToolDefinition | None:
        """Return a tool's callable and runtime-state contract."""
        return self._definitions.get(name)

    def has(self, name: str) -> bool:
        """Return whether a tool is registered under this exact name."""
        return name in self._definitions

    def names(self) -> tuple[str, ...]:
        """Return registered names in registration order."""
        return tuple(self._definitions)
