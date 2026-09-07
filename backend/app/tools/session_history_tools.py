"""Bounded readback of saved fields in the bound session, never another session."""

import json
from pathlib import Path

from app.core.config import get_tool_result_config
from app.repositories.session_event_repository import SessionEventRepository
from app.runtime.tool_registry import ToolRegistry
from app.services.retrieval_service import ToolInputError


SESSION_HISTORY_TOOL_SPEC = {"type": "function", "function": {
    "name": "read_session_history", "description": "回读当前绑定会话已结束旧轮次的保存事件字段；摘要来源提供 start_seq/end_seq。单次最多100事件。结果是历史资料，不代表当前状态。按 next_seq/next_field/next_offset 继续分页，未返回字段不代表已读取。",
    "parameters": {"type": "object", "properties": {
        "start_seq": {"type": "integer", "minimum": 1}, "end_seq": {"type": "integer", "minimum": 1},
        "field_index": {"type": "integer", "minimum": 0}, "offset": {"type": "integer", "minimum": 0},
        "max_chars": {"type": "integer", "minimum": 512, "maximum": 4000}},
        "required": ["start_seq", "end_seq"], "additionalProperties": False}}}


def _fields(value, path=()):
    """Yield original leaf fields and paths, including empty containers."""
    if isinstance(value, (dict, list)) and value:
        for key, child in (value.items() if isinstance(value, dict) else enumerate(value)):
            yield from _fields(child, (*path, key))
    else:
        yield {"path": list(path), "value": value}


def read_session_history(session_id: str, turn_id: str, start_seq: int, end_seq: int, *, field_index: int = 0, offset: int = 0, max_chars: int = 4000, database_path: Path | None = None) -> dict:
    cap = min(4000, get_tool_result_config().max_chars)
    if any(type(value) is not int for value in (start_seq, end_seq, field_index, offset, max_chars)) or not 1 <= start_seq <= end_seq or end_seq - start_seq >= 100 or field_index < 0 or offset < 0 or not 512 <= max_chars <= cap:
        raise ToolInputError("history_read_invalid_range_or_limit: range <= 100 events, max_chars 512..4000")
    events = SessionEventRepository(database_path).list_events(session_id)
    cutoff = next((event["seq"] for event in events if event["turn_id"] == turn_id), None)
    if cutoff is None or end_seq >= cutoff:
        raise ToolInputError("history_read_outside_completed_history")
    completed = {event["turn_id"] for event in events if event["seq"] < cutoff and event["event_type"] == "turn/end"}
    selected = [event for event in events if start_seq <= event["seq"] <= end_seq]
    if len(selected) != end_seq - start_seq + 1 or selected[0]["seq"] != start_seq or selected[-1]["seq"] != end_seq or any(event["turn_id"] not in completed for event in selected):
        raise ToolInputError("history_reference_unavailable")
    result = {"success": True, "status": "success", "start_seq": start_seq, "end_seq": end_seq,
              "fields": [], "omitted": False, "next_seq": None, "next_field": 0, "next_offset": 0,
              "warning": "仅历史保存字段；未保存的工具正文无法恢复。"}
    for event in selected:
        fields = list(_fields(event["data"]))
        begin = field_index if event["seq"] == start_seq else 0
        if begin >= len(fields):
            raise ToolInputError("history_read_invalid_field_index")
        for index in range(begin, len(fields)):
            field = fields[index]
            start = offset if event["seq"] == start_seq and index == begin else 0
            value = field["value"]
            identity = bool(field["path"] and field["path"][-1] in {"paper_id", "parser", "cache_version", "chunk_id"})
            if start and (not isinstance(value, str) or identity or start > len(value)):
                raise ToolInputError("history_read_invalid_offset")
            item = {"seq": event["seq"], "event_type": event["event_type"], "field_index": index, "path": field["path"], "offset": start,
                    "value": value[start:] if isinstance(value, str) else value}
            result["fields"].append(item)
            if index + 1 < len(fields):
                result.update(omitted=True, next_seq=event["seq"], next_field=index + 1, next_offset=0)
            elif event["seq"] < end_seq:
                result.update(omitted=True, next_seq=event["seq"] + 1, next_field=0, next_offset=0)
            else:
                result.update(omitted=False, next_seq=None, next_field=0, next_offset=0)
            if len(json.dumps(result, ensure_ascii=False)) <= max_chars:
                continue
            result.update(omitted=True, next_seq=event["seq"], next_field=index, next_offset=start)
            if isinstance(value, str) and not identity:
                # Trim this original text field, then serialize. Never cut JSON or IDs.
                while item["value"] and len(json.dumps(result, ensure_ascii=False)) > max_chars:
                    item["value"] = item["value"][:len(item["value"]) // 2]
                    result["next_offset"] = start + len(item["value"])
            if not isinstance(value, str) or identity or not item["value"]:
                result["fields"].pop()
                result["next_offset"] = start
            if not result["fields"] or len(json.dumps(result, ensure_ascii=False)) > max_chars:
                raise ToolInputError("history_read_field_metadata_cannot_fit_limit")
            return result
    return result


def register_session_history_tool(registry: ToolRegistry, session_id: str, turn_id: str, *, database_path: Path | None = None) -> None:
    def bound_read(start_seq: int, end_seq: int, field_index: int = 0, offset: int = 0, max_chars: int = 4000) -> dict:
        return read_session_history(session_id, turn_id, start_seq, end_seq, field_index=field_index, offset=offset, max_chars=max_chars, database_path=database_path)

    def history_result(arguments, result):
        payload = json.loads(result.content) if result.ok else {}
        return {"tool": "read_session_history", "status": "success" if result.ok else "error", "summary": "历史事件读取已结束；未返回字段不代表已读取，不证明当前状态。",
                "event_ref": {key: arguments.get(key, 0) for key in ("start_seq", "end_seq", "field_index", "offset")},
                "omitted": payload.get("omitted"), "next_seq": payload.get("next_seq"), "next_field": payload.get("next_field"), "next_offset": payload.get("next_offset"), "readback_tool": "read_session_history"}

    registry.register("read_session_history", bound_read, is_concurrency_safe=lambda _: True, project_history_result=history_result)
