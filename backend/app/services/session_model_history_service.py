"""Project completed SQLite session events into provider-compatible history."""

import json
from pathlib import Path
from typing import Any

from app.repositories.session_event_repository import SessionEventRepository


def project_session_events_to_messages(
    session_id: str,
    *,
    before_turn_id: str | None = None,
    database_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return only completed prior turns, preserving Tool call/result order."""
    events = SessionEventRepository(database_path).list_events(session_id)
    if before_turn_id:
        for index, event in enumerate(events):
            if event.get("turn_id") == before_turn_id:
                events = events[:index]
                break
    completed = {
        event["turn_id"]
        for event in events
        if event.get("event_type") == "turn/end" and event.get("data", {}).get("status") == "success" and event.get("turn_id")
    }
    messages: list[dict[str, Any]] = []
    pending_calls: list[dict[str, Any]] = []
    issued_call_ids: set[str] = set()

    def flush_calls() -> None:
        nonlocal pending_calls
        if pending_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": pending_calls})
            issued_call_ids.update(str(call["id"]) for call in pending_calls)
            pending_calls = []

    for event in events:
        if event.get("turn_id") not in completed:
            continue
        event_type, data = event.get("event_type"), event.get("data", {})
        if not isinstance(data, dict):
            continue
        if event_type in {"user/message", "assistant/message"}:
            flush_calls()
            content = data.get("content")
            if isinstance(content, str):
                messages.append({"role": "user" if event_type == "user/message" else "assistant", "content": content})
        elif event_type == "tool/call":
            call_id, name = data.get("tool_call_id"), data.get("name")
            if isinstance(call_id, str) and call_id and isinstance(name, str) and name:
                arguments = data.get("arguments", {})
                pending_calls.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}})
        elif event_type == "tool/result":
            flush_calls()
            call_id, name = data.get("tool_call_id"), data.get("name")
            if isinstance(call_id, str) and call_id in issued_call_ids and isinstance(name, str) and name:
                history_result = data.get("history_result")
                if not isinstance(history_result, dict):
                    history_result = {"tool": name, "status": data.get("status", "error"), "summary": data.get("summary", "")}
                messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": json.dumps(history_result, ensure_ascii=False)})
    flush_calls()
    return messages
