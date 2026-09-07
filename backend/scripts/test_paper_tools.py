"""Pure-local checks for Agent-selectable PDF parser Tools."""

import json
from types import SimpleNamespace

from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall
from app.services.parsers.schema import ParsedPaper
from app.tools import paper_tools


def _section(title: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(title=title, text=text, level=1, page_start=None, page_end=None, source="test")


def _parsed(parser_name: str) -> SimpleNamespace:
    if parser_name == "grobid":
        return SimpleNamespace(
            parser_name="grobid",
            title="",
            authors=[],
            abstract="结构化摘要。",
            references=[],
            sections=[_section("Method", "GROBID section text." * 80)],
            parser_warnings=["GROBID test result"],
            parser_meta={"section_count": 1},
            raw_text="Method\n" + "GROBID section text. " * 80,
        )
    return SimpleNamespace(
        parser_name="pymupdf",
        title="",
        authors=[],
        abstract="",
        references=[],
        sections=[],
        parser_warnings=[],
        parser_meta={"page_count": 2, "candidate_title": "第一页标题", "candidate_authors": ["测试作者"]},
        raw_text="--- Page 1 ---\n第一页正文\n--- Page 2 ---\n第二页正文",
    )


def main() -> None:
    registry = ToolRegistry()
    paper_tools.register_paper_tools(registry)
    assert registry.names() == (
        "parse_pdf_with_grobid",
        "parse_pdf_with_pymupdf",
        "get_paper_info",
        "extract_sections",
        "retrieve_paper_context",
        "read_paper_chunk",
    )
    assert [spec["function"]["name"] for spec in paper_tools.PAPER_TOOL_SPECS] == list(registry.names())

    original_parse = paper_tools._parse_with_cache
    original_cached = paper_tools._load_cached_parse
    original_find = paper_tools.find_paper_pdf
    original_parse_pdf = paper_tools.parse_pdf
    original_save_cache = paper_tools.save_paper_parse_cache
    try:
        calls: list[str] = []

        def fake_parse(paper_id: str, parser_name: str):
            assert paper_id == "local"
            calls.append(parser_name)
            return _parsed(parser_name), False

        paper_tools._parse_with_cache = fake_parse
        grobid = execute_tool_call(
            AgentToolCall("call_grobid", "parse_pdf_with_grobid", '{"paper_id":"local"}'), registry
        )
        grobid_data = json.loads(grobid.content)
        assert grobid.ok and grobid_data["success"] and "title" in grobid_data["missing_fields"]
        assert grobid_data["sections"] and calls == ["grobid"]

        pymupdf = execute_tool_call(
            AgentToolCall("call_pymupdf", "parse_pdf_with_pymupdf", '{"paper_id":"local","pages":[1]}'), registry
        )
        pymupdf_data = json.loads(pymupdf.content)
        assert pymupdf.ok and pymupdf_data["success"] and pymupdf_data["pages"] == [
            {"page": 1, "text": "第一页正文", "found": True}
        ]
        assert calls == ["grobid", "pymupdf"]

        paper_tools._load_cached_parse = lambda _paper_id, parser_name: _parsed(parser_name) if parser_name == "pymupdf" else None
        info = execute_tool_call(AgentToolCall("call_info", "get_paper_info", '{"paper_id":"local"}'), registry)
        assert info.ok and json.loads(info.content)["title"] == "第一页标题"
        sections = execute_tool_call(
            AgentToolCall("call_sections", "extract_sections", '{"paper_id":"local","parser":"pymupdf"}'), registry
        )
        assert sections.ok and json.loads(sections.content)["success"]

        paper_tools._parse_with_cache = lambda *_args: (_ for _ in ()).throw(RuntimeError("GROBID unavailable"))
        failure_events = []
        failed = execute_tool_call(
            AgentToolCall("call_failed", "parse_pdf_with_grobid", '{"paper_id":"local"}'), registry, event_sink=failure_events.append
        )
        failed_data = json.loads(failed.content)
        assert failed.ok and not failed_data["success"] and "GROBID unavailable" in failed_data["error"]
        assert failure_events[-1]["status"] == "error"

        paper_tools._parse_with_cache = original_parse
        cached_results = {}
        parse_calls = []
        paper_tools._load_cached_parse = lambda paper_id, parser_name: cached_results.get((paper_id, parser_name))
        paper_tools.find_paper_pdf = lambda _paper_id: "/tmp/local.pdf"
        paper_tools.parse_pdf = lambda _path, parser_name: parse_calls.append(parser_name) or ParsedPaper(parser_name=parser_name)
        paper_tools.save_paper_parse_cache = lambda paper_id, parser_name, payload: cached_results.__setitem__(
            (paper_id, parser_name), ParsedPaper.model_validate(payload)
        )
        _, first_cached = paper_tools._parse_with_cache("cached", "grobid")
        _, second_cached = paper_tools._parse_with_cache("cached", "grobid")
        assert parse_calls == ["grobid"] and not first_cached and second_cached
    finally:
        paper_tools._parse_with_cache = original_parse
        paper_tools._load_cached_parse = original_cached
        paper_tools.find_paper_pdf = original_find
        paper_tools.parse_pdf = original_parse_pdf
        paper_tools.save_paper_parse_cache = original_save_cache

    print("ALL PAPER TOOL TESTS PASSED")


if __name__ == "__main__":
    main()
