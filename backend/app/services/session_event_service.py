"""Translate runtime events into the durable semantic session event log."""

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository, build_session_title


logger = logging.getLogger(__name__)

_SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "secret", "token", "password", "credential")
_MAX_TRACE_CHARS = 4_000
_MAX_TOOL_VALUE_CHARS = 2_000
_MAX_SUMMARY_CHARS = 1_000
_MAX_ERROR_CHARS = 1_000


def _short_text(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _sanitize(value: Any, *, limit: int = _MAX_TOOL_VALUE_CHARS, depth: int = 0) -> Any:
    """Keep displayable tool metadata bounded and redact obvious credentials."""
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, str):
        return _short_text(value, limit)
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS) else _sanitize(item, limit=limit, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, limit=limit, depth=depth + 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _short_text(value, limit)


class SessionEventService:
    """One turn's semantic-event adapter; runtime and Tool code remain storage-free."""

    def __init__(self, session_id: str, *, database_path: Path | None = None) -> None:
        self.session_id = session_id
        self.turn_id = uuid4().hex
        self.repository = SessionEventRepository(database_path)
        self.sessions = SessionRepository(database_path)
        self._started = False
        self._ended = False
        self._final_parts: list[str] = []
        self._final_saved = False
        self._tool_order: dict[int, list[str]] = {}
        self._tool_results: dict[int, dict[str, dict[str, Any]]] = {}

    @property
    def ended(self) -> bool:
        return self._ended

    def _append(self, event_type: str, data: dict[str, Any], *, step: int | None = None) -> None:
        try:
            self.repository.append_event(self.session_id, event_type, data, turn_id=self.turn_id, step=step)
        except Exception:
            logger.exception("Failed to persist %s for session %s", event_type, self.session_id)

    def start(self, message: str, *, paper_id: str | None, active_paper_ids: list[str]) -> None:
        """Record the formal send action exactly once before Agent execution."""
        if self._started:
            return
        self._started = True
        self.sessions.get_or_create(self.session_id)
        self.sessions.touch(self.session_id)
        title = build_session_title(message)
        if title:
            self.sessions.set_title_if_empty(self.session_id, title)
        if paper_id is not None:
            self.sessions.set_current_paper(self.session_id, paper_id)
        for active_paper_id in active_paper_ids:
            self.sessions.add_paper(self.session_id, active_paper_id)
        self._append("user/message", {"content": message, "paper_id": paper_id})
        self._append("turn/start", {"paper_id": paper_id, "active_paper_ids": _sanitize(active_paper_ids)})

    def _flush_tool_results(self, step: int) -> None:
        results = self._tool_results.pop(step, {})
        for call_id in self._tool_order.pop(step, []):
            result = results.pop(call_id, None)
            if result is not None:
                self._append("tool/result", result, step=step)
        for result in results.values():
            self._append("tool/result", result, step=step)

    def _save_final_message(self) -> None:
        if self._final_saved:
            return
        self._final_saved = True
        self._append("assistant/message", {"content": "".join(self._final_parts)})

    def persist_runtime_event(self, event: dict[str, Any]) -> None:
        """Persist semantic events and retain transport deltas only until finalization."""
        event_type = event.get("type")
        if not isinstance(event_type, str) or not self._started:
            return
        step = event.get("step")
        step = step if isinstance(step, int) else None
        if event_type == "step_start" and step is not None:
            self._append("step/start", {}, step=step)
        elif event_type == "assistant_trace" and step is not None:
            self._append("assistant/trace", {"content": _short_text(event.get("content", ""), _MAX_TRACE_CHARS)}, step=step)
        elif event_type == "tool_call" and step is not None:
            call_id = str(event.get("tool_call_id", ""))
            self._tool_order.setdefault(step, []).append(call_id)
            self._append(
                "tool/call",
                {"tool_call_id": call_id, "name": _short_text(event.get("name", ""), 200), "arguments": _sanitize(event.get("arguments", {}))},
                step=step,
            )
        elif event_type == "tool_result" and step is not None:
            call_id = str(event.get("tool_call_id", ""))
            self._tool_results.setdefault(step, {})[call_id] = {
                "tool_call_id": call_id,
                "name": _short_text(event.get("name", ""), 200),
                "status": "success" if event.get("status") == "success" else "error",
                "summary": _short_text(event.get("summary", ""), _MAX_SUMMARY_CHARS),
                "history_result": _sanitize(event.get("history_result", {}), limit=_MAX_SUMMARY_CHARS),
            }
        elif event_type == "final_delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                self._final_parts.append(delta)
        elif event_type == "final_end":
            self._save_final_message()
        elif event_type == "step_end" and step is not None:
            self._flush_tool_results(step)
            self._append("step/end", {"outcome": _short_text(event.get("status", ""), 100)}, step=step)
        elif event_type == "error":
            if step is not None:
                self._flush_tool_results(step)
            self._append(
                "error",
                {"code": _short_text(event.get("code", "agent_failed"), 100), "message": _short_text(event.get("message", "Agent execution failed."), _MAX_ERROR_CHARS)},
                step=step,
            )
        elif event_type == "agent_done":
            self.finish("error" if event.get("status") == "error" else "success")

    def finish(self, status: str, *, code: str | None = None, message: str | None = None) -> None:
        """Close a turn once, including failures that occur before runtime emits an event."""
        if self._ended:
            return
        for step in list(self._tool_results):
            self._flush_tool_results(step)
        if status == "error" and code and message:
            self._append("error", {"code": _short_text(code, 100), "message": _short_text(message, _MAX_ERROR_CHARS)})
        self._append("turn/end", {"status": "success" if status == "success" else "error"})
        self._ended = True
