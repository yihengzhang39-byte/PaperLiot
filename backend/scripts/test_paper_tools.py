"""Pure-local checks for PaperPilot tools through the generic runtime."""

import json
from types import SimpleNamespace

from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall
from app.tools import paper_tools


def _section_text(label: str) -> str:
    return f"{label} 内容。" * 40


def _parsed_paper() -> SimpleNamespace:
    return SimpleNamespace(
        parser_name="pymupdf",
        title="Local Paper",
        authors=["Test Author"],
        abstract="这是一段用于本地 Paper Tool 验证的摘要内容。",
        parser_warnings=[],
        parser_meta={"year": "2026", "venue": "Local Venue"},
        raw_text="\n".join(
            [
                "摘要",
                "这是一段用于章节提取验证的摘要内容，包含足够的文字。",
                "1 引言",
                _section_text("引言"),
                "2 相关工作",
                _section_text("相关工作"),
                "3 方法",
                _section_text("方法"),
                "4 实验",
                _section_text("实验"),
                "5 结论",
                _section_text("结论"),
            ]
        ),
    )


def main() -> None:
    registry = ToolRegistry()
    paper_tools.register_paper_tools(registry)
    assert registry.has("get_paper_info") and registry.has("extract_sections") and registry.has("retrieve_paper_context")
    assert [spec["function"]["name"] for spec in paper_tools.PAPER_TOOL_SPECS] == [
        "get_paper_info",
        "extract_sections",
        "retrieve_paper_context",
    ]

    original_loader = paper_tools._load_paper
    try:
        paper_tools._load_paper = lambda paper_id: (_parsed_paper(), "zh", "pymupdf")
        info = execute_tool_call(AgentToolCall("call_info", "get_paper_info", '{"paper_id":"local"}'), registry)
        assert info.ok and json.loads(info.content)["title"] == "Local Paper"

        sections = execute_tool_call(AgentToolCall("call_sections", "extract_sections", '{"paper_id":"local"}'), registry)
        section_data = json.loads(sections.content)
        assert sections.ok and section_data["section_lengths"]["method"] > 150
        assert section_data["section_previews"]["method"]

        paper_tools._load_paper = lambda paper_id: (_ for _ in ()).throw(ValueError("broken capability"))
        broken = execute_tool_call(AgentToolCall("call_broken", "get_paper_info", '{"paper_id":"local"}'), registry)
        assert not broken.ok and broken.tool_call_id == "call_broken"

        paper_tools._load_paper = lambda paper_id: (_ for _ in ()).throw(FileNotFoundError("paper not found"))
        not_found = execute_tool_call(AgentToolCall("call_not_found", "get_paper_info", '{"paper_id":"missing"}'), registry)
        assert not not_found.ok and not_found.tool_call_id == "call_not_found"
    finally:
        paper_tools._load_paper = original_loader

    missing = execute_tool_call(AgentToolCall("call_missing", "get_paper_info", "{}"), registry)
    assert not missing.ok and missing.tool_call_id == "call_missing"
    print("ALL PAPER TOOL TESTS PASSED")


if __name__ == "__main__":
    main()
