"""Narrow Agent Tool surface for stable research and user-profile memory."""

import json
from typing import Any

from app.runtime.tool_executor import ToolExecutionResult
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


def _payload(result: ToolExecutionResult) -> dict[str, Any]:
    try:
        value = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _failure(tool: str, result: ToolExecutionResult, payload: dict[str, Any]) -> dict[str, Any]:
    return {"tool": tool, "status": "error", "summary": str(payload.get("error") or result.error or "Tool execution failed.")[:300]}


def project_save_user_profile_history(_args: dict[str, Any], result: ToolExecutionResult) -> dict[str, Any]:
    payload = _payload(result)
    if not result.ok or payload.get("success") is False:
        return _failure("save_user_profile", result, payload)
    updated = payload.get("updated_fields")
    return {
        "tool": "save_user_profile",
        "status": "success",
        "saved": payload.get("saved") is True,
        **({"updated": updated} if isinstance(updated, dict) else {}),
    }


def project_save_research_memory_history(_args: dict[str, Any], result: ToolExecutionResult) -> dict[str, Any]:
    payload = _payload(result)
    if not result.ok or payload.get("success") is False:
        return _failure("save_research_memory", result, payload)
    category, entry = payload.get("category"), payload.get("entry")
    updated = {str(category): entry} if payload.get("saved") is True and category in {"paper", "theme", "finding"} and isinstance(entry, str) else {}
    return {"tool": "save_research_memory", "status": "success", "saved": payload.get("saved") is True, "updated": updated}


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
    registry.register("save_research_memory", save_research_memory, produces=("research_memory_updated",), project_history_result=project_save_research_memory_history)
    registry.register("save_user_profile", save_user_profile, produces=("user_profile_updated",), project_history_result=project_save_user_profile_history)
