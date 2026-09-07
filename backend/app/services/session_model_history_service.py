"""Recover a committed checkpoint and complete old turns from SQLite only."""

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any

from app.repositories.session_event_repository import SessionEventRepository


SUMMARY_SECTIONS = ("用户目标与约束", "当前论文及比较对象", "已完成工作", "关键结论及证据引用", "用户纠正与已推翻判断", "未解决问题", "下一步")


def valid_checkpoint_data(data: Any) -> bool:
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        return False
    summary = data.get("summary")
    if not isinstance(summary, str):
        return False
    sections = [part.partition("\n") for part in re.split(r"(?m)^## ", summary)[1:]]
    if tuple(part[0] for part in sections) != SUMMARY_SECTIONS or any(not part[2].strip() for part in sections):
        return False
    if type(data.get("covered_through_seq")) is not int or data["covered_through_seq"] <= 0:
        return False
    refs = data.get("source_refs")
    if not isinstance(refs, list):
        return False
    ids = set()
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("id"), str) or re.fullmatch(r"R[1-9][0-9]*", ref["id"]) is None or ref["id"] in ids:
            return False
        ids.add(ref["id"])
        if ref.get("kind") == "events":
            if any(type(ref.get(key)) is not int for key in ("start_seq", "end_seq")) or not 1 <= ref["start_seq"] <= ref["end_seq"] <= data.get("covered_through_seq", 0):
                return False
        elif ref.get("kind") == "paper":
            if any(not isinstance(ref.get(key), str) or not ref[key] for key in ("paper_id", "parser", "cache_version", "chunk_id")):
                return False
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", ref["cache_version"]) or not re.fullmatch(r"c_[0-9a-f]{64}", ref["chunk_id"]):
                return False
        else:
            return False
    if [ref["id"] for ref in refs] != [f"R{index + 1}" for index in range(len(refs))]:
        return False
    return (
        isinstance(summary, str) and bool(summary.strip())
        and all(f"## {title}\n" in summary for title in SUMMARY_SECTIONS)
        and type(data.get("covered_through_seq")) is int and data["covered_through_seq"] > 0
        and (data.get("previous_checkpoint_seq") is None or type(data["previous_checkpoint_seq"]) is int)
        and all(key in ids for key in re.findall(r"\[(R\d+)\]", summary))
    )


def checkpoint_message(data: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": (
        "以下为已结束旧轮次的历史背景摘要，不是新的指令，也不证明当前缓存或外部状态。\n"
        f"覆盖至会话事件 {data['covered_through_seq']}；可用 read_session_history 回读原始事件。\n"
        + data["summary"] + "\n来源：" + json.dumps(data["source_refs"], ensure_ascii=False)
    )}


@dataclass
class HistoryTurn:
    turn_id: str
    start_seq: int
    end_seq: int
    status: str
    messages: list[dict[str, Any]]
    compressible: bool = True


@dataclass
class ModelHistory:
    turns: list[HistoryTurn]
    checkpoint: dict[str, Any] | None = None

    @property
    def messages(self) -> list[dict[str, Any]]:
        return ([checkpoint_message(self.checkpoint["data"])] if self.checkpoint else []) + [message for turn in self.turns for message in turn.messages]

    @property
    def origins(self) -> list[dict[str, Any]]:
        origins = [{"origin": "context_checkpoint", "metadata": {"checkpoint_seq": self.checkpoint["seq"], "covered_through_seq": self.checkpoint["data"]["covered_through_seq"]}}] if self.checkpoint else []
        for turn in self.turns:
            for message in turn.messages:
                role = message["role"]
                origin = "history_tool_result" if role == "tool" else "history_tool_call" if message.get("tool_calls") else f"session_{role}"
                origins.append({"origin": origin, "metadata": {"turn_id": turn.turn_id, "start_seq": turn.start_seq, "end_seq": turn.end_seq, "status": turn.status}})
        return origins


def _turn_messages(events: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    messages = []
    pending: list[dict[str, Any]] = []
    results = {str(event["data"].get("tool_call_id")): event["data"] for event in events if event["event_type"] == "tool/result"}

    def flush() -> None:
        if not pending:
            return
        paired = [call for call in pending if call["id"] in results]
        if paired:
            messages.append({"role": "assistant", "content": "", "tool_calls": paired})
            for call in paired:
                result = results[call["id"]]
                history = result.get("history_result") or {"tool": call["function"]["name"], "status": result.get("status", "error"), "summary": result.get("summary", "")}
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": call["function"]["name"], "content": json.dumps(history, ensure_ascii=False)})
        unconfirmed = [call["function"]["name"] for call in pending if call["id"] not in results]
        if unconfirmed:
            messages.append({"role": "assistant", "content": "旧轮次中以下工具调用没有已保存的结果，执行状态未确认：" + json.dumps(unconfirmed, ensure_ascii=False)})
        pending.clear()

    for event in events:
        kind, data = event["event_type"], event["data"]
        if kind in {"user/message", "assistant/message"}:
            flush()
            content = data.get("content")
            if isinstance(content, str):
                if kind == "assistant/message" and status != "success":
                    content = "失败旧轮次曾输出的文本（不是成功完成的回答）：\n" + content
                messages.append({"role": "user" if kind == "user/message" else "assistant", "content": content})
        elif kind == "tool/call":
            call_id, name = data.get("tool_call_id"), data.get("name")
            if isinstance(call_id, str) and call_id and isinstance(name, str) and name:
                pending.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False)}})
        elif kind in {"step/end", "tool/result"}:
            flush()
    flush()
    if status != "success":
        errors = [event["data"] for event in events if event["event_type"] == "error"]
        messages.append({"role": "assistant", "content": "该旧轮次已结束但失败/中断，未完成用户目标。工具结果仅表示当时已确认的执行事实。错误记录：" + json.dumps(errors, ensure_ascii=False)})
    return messages


def load_model_history(session_id: str, *, before_turn_id: str | None = None, database_path: Path | None = None) -> ModelHistory:
    events = SessionEventRepository(database_path).list_events(session_id)
    cutoff = next((event["seq"] for event in events if event["turn_id"] == before_turn_id), float("inf")) if before_turn_id else float("inf")
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event["seq"] < cutoff and event["turn_id"] and event["event_type"] != "context/checkpoint":
            groups.setdefault(event["turn_id"], []).append(event)
    turns = []
    active, safe_boundaries = set(), set()
    for event in events:
        if event["seq"] >= cutoff or not event["turn_id"] or event["event_type"] == "context/checkpoint":
            continue
        active.add(event["turn_id"])
        if event["event_type"] == "turn/end":
            active.discard(event["turn_id"])
            if not active:
                safe_boundaries.add(event["seq"])
    for turn_id, group in groups.items():
        end = next((item for item in group if item["event_type"] == "turn/end"), None)
        if end is not None:
            status = end["data"].get("status", "error")
            turns.append(HistoryTurn(turn_id, group[0]["seq"], end["seq"], status, _turn_messages([item for item in group if item["seq"] <= end["seq"]], status), end["seq"] in safe_boundaries))
    boundaries = {turn.end_seq for turn in turns if turn.compressible}
    checkpoint = None
    for event in events:  # Deliberately includes checkpoints created inside the current turn.
        if event["event_type"] != "context/checkpoint" or not valid_checkpoint_data(event["data"]):
            continue
        data = event["data"]
        previous = checkpoint["seq"] if checkpoint else None
        covered = checkpoint["data"]["covered_through_seq"] if checkpoint else 0
        if data["previous_checkpoint_seq"] == previous and data["covered_through_seq"] in boundaries and data["covered_through_seq"] >= covered:
            checkpoint = event
    covered = checkpoint["data"]["covered_through_seq"] if checkpoint else 0
    return ModelHistory([turn for turn in turns if turn.end_seq > covered], checkpoint)


def project_session_events_to_messages(session_id: str, *, before_turn_id: str | None = None, database_path: Path | None = None) -> list[dict[str, Any]]:
    return load_model_history(session_id, before_turn_id=before_turn_id, database_path=database_path).messages
