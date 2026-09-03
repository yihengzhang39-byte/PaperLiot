"""Narrow Agent Tool surface for stable research and user-profile memory."""

from typing import Any

from app.runtime.tool_registry import ToolRegistry
from app.services.memory_service import append_memory_entry, save_user_profile as save_user_profile_entry


def save_research_memory(category: str = "", entry: str = "") -> dict[str, object]:
    """Save one durable research fact; no arbitrary file paths are accepted."""
    return append_memory_entry(category, entry)


def save_user_profile(
    university: str = "",
    education: str = "",
    identity: str = "",
    research_interest: list[str] | None = None,
    technical_background: list[str] | None = None,
    preferences: list[str] | None = None,
) -> dict[str, object]:
    """Save explicit, durable user information to user.md only."""
    return save_user_profile_entry(
        university=university,
        education=education,
        identity=identity,
        research_interest=research_interest,
        technical_background=technical_background,
        preferences=preferences,
    )


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
    },
    {
        "type": "function",
        "function": {
            "name": "save_user_profile",
            "description": "Save user information explicitly stated as stable profile data, such as university, education, identity, research interests, technical background, or preferences. Use it for explicit corrections and do not save ordinary chat. When passing a list field, provide the complete desired list because it replaces the existing field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "university": {"type": "string", "minLength": 1, "maxLength": 500},
                    "education": {"type": "string", "minLength": 1, "maxLength": 500},
                    "identity": {"type": "string", "minLength": 1, "maxLength": 500},
                    "research_interest": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "technical_background": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "preferences": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 500}},
                },
            },
        },
    },
]


def register_memory_tools(registry: ToolRegistry) -> None:
    """Register the controlled memory write capabilities exposed to the Agent."""
    registry.register("save_research_memory", save_research_memory)
    registry.register("save_user_profile", save_user_profile)
