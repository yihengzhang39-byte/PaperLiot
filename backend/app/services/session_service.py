"""Safe JSON persistence for bounded semantic chat sessions."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.config import CHAT_SESSIONS_DIR, get_session_config


@dataclass
class ChatSessionState:
    """The small, user-visible portion of one conversational session."""

    messages: list[dict[str, str]] = field(default_factory=list)
    paper_id: str | None = None
    active_paper_ids: list[str] = field(default_factory=list)
    updated_at: str = ""


def _session_path(session_id: str, storage_dir: Path | None = None) -> Path:
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
        raise ValueError("session_id must be a non-empty string up to 512 characters")
    digest = hashlib.sha256(session_id.strip().encode("utf-8")).hexdigest()
    return (storage_dir or CHAT_SESSIONS_DIR) / f"{digest}.json"


def trim_history(messages: list[dict[str, Any]], max_messages: int | None = None) -> list[dict[str, str]]:
    """Keep only bounded user/final-assistant history; never retain runtime trace."""
    limit = get_session_config().max_history_messages if max_messages is None else max_messages
    if limit < 1:
        raise ValueError("max_messages must be at least 1")
    if not isinstance(messages, list):
        return []
    semantic = [
        {"role": item["role"], "content": item["content"]}
        for item in messages
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]
    return semantic[-limit:]


def normalize_active_paper_ids(paper_ids: list[Any], current_paper_id: str | None = None) -> list[str]:
    """Keep distinct, non-empty paper IDs in first-seen order."""
    values = list(paper_ids) if isinstance(paper_ids, list) else []
    if current_paper_id:
        values.append(current_paper_id)
    normalized: list[str] = []
    for paper_id in values:
        if isinstance(paper_id, str) and paper_id.strip() and paper_id.strip() not in normalized:
            normalized.append(paper_id.strip())
    return normalized


def load_session(session_id: str, *, storage_dir: Path | None = None) -> ChatSessionState | None:
    """Load a session when its hashed JSON file is present and valid."""
    path = _session_path(session_id, storage_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("session_id") != session_id.strip():
        return None
    paper_id = payload.get("paper_id")
    normalized_paper_id = paper_id.strip() if isinstance(paper_id, str) and paper_id.strip() else None
    return ChatSessionState(
        messages=trim_history(payload.get("messages", [])),
        paper_id=normalized_paper_id,
        active_paper_ids=normalize_active_paper_ids(payload.get("active_paper_ids", []), normalized_paper_id),
        updated_at=str(payload.get("updated_at", "")),
    )


def save_session(
    session_id: str,
    state: ChatSessionState,
    *,
    storage_dir: Path | None = None,
    max_messages: int | None = None,
) -> Path:
    """Persist one session, using a hashed filename rather than user input."""
    path = _session_path(session_id, storage_dir)
    state.messages = trim_history(state.messages, max_messages)
    state.active_paper_ids = normalize_active_paper_ids(state.active_paper_ids, state.paper_id)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": session_id.strip(),
                "paper_id": state.paper_id,
                "active_paper_ids": state.active_paper_ids,
                "messages": state.messages,
                "updated_at": state.updated_at,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path
