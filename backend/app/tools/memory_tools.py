"""Narrow Agent Tool surface for writing stable research memory."""

from typing import Any

from app.runtime.tool_registry import ToolRegistry
from app.services.memory_service import append_memory_entry


def save_research_memory(category: str = "", entry: str = "") -> dict[str, object]:
    """Save one durable research fact; no arbitrary file paths are accepted."""
    return append_memory_entry(category, entry)


MEMORY_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "save_research_memory",
            "description": "Save one stable research paper, theme, or cross-paper finding to long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["paper", "theme", "finding"]},
                    "entry": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": ["category", "entry"],
            },
        },
    }
]


def register_memory_tools(registry: ToolRegistry) -> None:
    """Register the only memory write capability exposed to the Agent."""
    registry.register("save_research_memory", save_research_memory)
