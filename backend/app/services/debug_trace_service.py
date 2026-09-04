"""Best-effort, bounded persistence of real Agent execution facts."""

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.core.config import get_harness_debug_trace_config
from app.repositories.debug_trace_repository import DebugTraceRepository


logger = logging.getLogger(__name__)

_SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "secret", "token", "password", "credential")
_QUOTED_SECRET_VALUE = re.compile(r"(?i)([\"']?(?:api[_ -]?key|authorization|secret|token|password|credential)[\"']?\s*[:=]\s*[\"'])(.*?)([\"'])")
_SECRET_VALUE = re.compile(r"(?i)([\"']?(?:api[_ -]?key|authorization|secret|token|password|credential)[\"']?\s*[:=]\s*)([^\"'\s,}]+)")
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._-]+")
_API_KEY = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
_MAX_MESSAGE_CHARS = 12_000
_MAX_TOOL_PREVIEW_CHARS = 4_000
_MAX_HISTORY_CHARS = 20_000


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _redact(text: str) -> str:
    text = _QUOTED_SECRET_VALUE.sub(r"\1<redacted>\3", text)
    text = _SECRET_VALUE.sub(r"\1<redacted>", text)
    text = _BEARER_TOKEN.sub("Bearer <redacted>", text)
    return _API_KEY.sub("<redacted-api-key>", text)


def _safe(value: Any, *, text_limit: int, depth: int = 0) -> Any:
    if depth >= 8:
        return "[truncated]"
    if isinstance(value, str):
        text = _redact(value)
        return text if len(text) <= text_limit else f"{text[:text_limit]}…"
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS) else _safe(item, text_limit=text_limit, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item, text_limit=text_limit, depth=depth + 1) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe(str(value), text_limit=text_limit, depth=depth + 1)


class DebugTraceService:
    """Record trace data without allowing observability failures to affect the Agent."""

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        *,
        database_path: Path | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.enabled = get_harness_debug_trace_config().enabled if enabled is None else enabled
        self.repository: DebugTraceRepository | None = None
        if self.enabled:
            try:
                self.repository = DebugTraceRepository(database_path)
            except Exception:
                logger.exception("Failed to initialize debug trace for session %s", session_id)
        self._ended = False

    def _record(self, event_type: str, data: dict[str, Any], *, step: int | None = None) -> None:
        if not self.repository:
            return
        try:
            self.repository.append(self.session_id, self.turn_id, event_type, data, step=step)
        except Exception:
            logger.exception("Failed to persist debug trace %s for session %s", event_type, self.session_id)

    def start(self) -> None:
        self._record("turn/debug_start", {"user_message_ref": {"event_type": "user/message", "turn_id": self.turn_id}})

    def finish(self, status: str) -> None:
        if self._ended:
            return
        self._ended = True
        self._record(
            "turn/debug_end",
            {
                "status": "success" if status == "success" else "error",
                "final_message_ref": {"event_type": "assistant/message", "turn_id": self.turn_id},
            },
        )

    def record_llm_input(
        self,
        messages: list[dict[str, Any]],
        origins: list[dict[str, Any]],
        *,
        step: int,
    ) -> None:
        snapshots: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            origin = origins[index] if index < len(origins) and isinstance(origins[index], dict) else {"origin": "unknown"}
            content = message.get("content", "")
            snapshots.append(
                {
                    "index": index,
                    "role": str(message.get("role", "unknown")),
                    "origin": str(origin.get("origin", "unknown")),
                    "origin_metadata": _safe(origin.get("metadata", {}), text_limit=1_000),
                    "content": _safe(content, text_limit=_MAX_MESSAGE_CHARS),
                    "content_length": len(_text(content)),
                    "tool_call_id": _safe(message.get("tool_call_id"), text_limit=500),
                    "name": _safe(message.get("name"), text_limit=500),
                    "tool_calls": _safe(message.get("tool_calls"), text_limit=_MAX_TOOL_PREVIEW_CHARS),
                }
            )
        self._record("llm/input", {"message_count": len(messages), "messages": snapshots}, step=step)

    def record_llm_output(self, response: Any, *, step: int) -> None:
        self._record(
            "llm/output",
            {
                "content": _safe(response.content, text_limit=_MAX_MESSAGE_CHARS),
                "content_length": len(response.content),
                "tool_calls": [
                    {"tool_call_id": _safe(call.id, text_limit=500), "name": _safe(call.name, text_limit=500), "arguments": _safe(call.arguments, text_limit=_MAX_TOOL_PREVIEW_CHARS)}
                    for call in response.tool_calls
                ],
            },
            step=step,
        )

    def record_tool_call(self, tool_call: Any, *, step: int, order_index: int) -> None:
        self._record(
            "tool/call_debug",
            {"tool_call_id": _safe(tool_call.id, text_limit=500), "name": _safe(tool_call.name, text_limit=500), "arguments": _safe(tool_call.arguments, text_limit=_MAX_TOOL_PREVIEW_CHARS), "model_call_order_index": order_index},
            step=step,
        )

    def record_tool_result(self, result: Any, *, step: int, order_index: int) -> None:
        model_size = len(result.content)
        history = result.history_result or {}
        history_text = _text(history)
        history_size = len(history_text)
        preview = _safe(result.content, text_limit=_MAX_TOOL_PREVIEW_CHARS)
        self._record(
            "tool/result_debug",
            {
                "tool_call_id": _safe(result.tool_call_id, text_limit=500),
                "name": _safe(result.tool_name, text_limit=500),
                "model_call_order_index": order_index,
                "status": "success" if result.ok else "error",
                "current_run_result": {"type": "text", "size_chars": model_size, "preview": preview, "truncated": model_size > _MAX_TOOL_PREVIEW_CHARS},
                "history_result": _safe(history, text_limit=_MAX_HISTORY_CHARS),
                "model_result_size": model_size,
                "history_result_size": history_size,
                "compression_ratio": history_size / model_size if model_size else 0.0,
                "history_projection": {"projector": result.history_projector or "fallback"},
            },
            step=step,
        )
