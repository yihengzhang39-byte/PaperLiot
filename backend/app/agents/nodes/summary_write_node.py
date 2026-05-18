"""Markdown note generation node."""

from app.agents.paper_state import PaperState
from app.core.config import NOTES_DIR, ensure_storage_dirs
from app.services.file_service import write_text_file


def _list_to_markdown(items: list[str]) -> str:
    """Render a list of strings as Markdown bullets."""
    if not items:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in items)


def _build_note(state: PaperState) -> str:
    """Build the final Chinese intensive-reading Markdown note."""
    authors = "、".join(state.get("authors", [])) or "未知"

    return f"""# 论文精读笔记

## 1. 基本信息

- 标题：{state.get("title") or "未知"}
- 作者：{authors}
- 年份：{state.get("year") or "未知"}
- 会议/期刊：{state.get("venue") or "未知"}

### 摘要

{state.get("abstract") or "暂无"}

## 2. 论文解决的问题

{state.get("problem") or "暂无"}

## 3. 背景与动机

{state.get("motivation") or "暂无"}

## 4. 核心方法

{state.get("method_summary") or "暂无"}

## 5. 创新点总结

{_list_to_markdown(state.get("innovation_points", []))}

## 6. 实验设计与结果

{state.get("experiment_summary") or "暂无"}

## 7. 方法优点

{state.get("method_summary") or "暂无"}

## 8. 方法局限

{_list_to_markdown(state.get("limitations", []))}

## 9. 对我研究的启发

{_list_to_markdown(state.get("inspirations", []))}
"""


def summary_write_node(state: PaperState) -> dict[str, str]:
    """Write the final Markdown note to local storage."""
    ensure_storage_dirs()
    final_note = _build_note(state)
    note_path = NOTES_DIR / f"{state['paper_id']}.md"
    write_text_file(note_path, final_note)
    return {"final_note": final_note, "note_path": str(note_path)}
