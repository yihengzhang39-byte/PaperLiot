"""Pure-local checks for LLM-selected parser Tool decisions."""

from types import SimpleNamespace

from app.agents.agent_loop import AgentMaxStepsError
from app.agents.paper_agent import PAPER_AGENT_SYSTEM_PROMPT, run_paper_agent
from app.services.llm_service import AgentLLMResponse, AgentToolCall
from app.tools import paper_tools


def _parsed(parser_name: str) -> SimpleNamespace:
    if parser_name == "grobid":
        return SimpleNamespace(
            parser_name="grobid",
            title="",
            authors=[],
            abstract="结构化摘要。",
            sections=[SimpleNamespace(title="Method", text="方法内容。" * 80, level=1, source="grobid")],
            references=[],
            parser_warnings=[],
            parser_meta={"section_count": 1},
            raw_text="Method\n" + "方法内容。" * 80,
        )
    return SimpleNamespace(
        parser_name="pymupdf",
        title="",
        authors=[],
        abstract="",
        sections=[],
        references=[],
        parser_warnings=[],
        parser_meta={"page_count": 1, "candidate_title": "Agent Test Paper"},
        raw_text="--- Page 1 ---\nAgent Test Paper\n第一页内容",
    )


def _sequence_llm(responses: list[AgentLLMResponse]):
    calls: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []

    def call(messages: list[dict[str, object]], tools: list[dict[str, object]]) -> AgentLLMResponse:
        calls.append((list(messages), list(tools)))
        return responses[len(calls) - 1]

    return call, calls


def _assert_paper_schemas(tools: list[dict[str, object]]) -> None:
    assert [tool["function"]["name"] for tool in tools] == [
        "parse_pdf_with_grobid",
        "parse_pdf_with_pymupdf",
        "get_paper_info",
        "extract_sections",
        "retrieve_paper_context",
        "read_paper_chunk",
        "save_research_memory",
        "save_user_profile",
        "get_multi_paper_context",
    ]


def main() -> None:
    original_parse = paper_tools._parse_with_cache
    paper_tools._parse_with_cache = lambda _paper_id, parser_name: (_parsed(parser_name), False)
    try:
        direct_llm, direct_calls = _sequence_llm([AgentLLMResponse("我可以帮你阅读论文。", [])])
        direct = run_paper_agent("你能做什么？", llm_call=direct_llm)
        assert direct.final_answer == "我可以帮你阅读论文。" and len(direct_calls) == 1
        _assert_paper_schemas(direct_calls[0][1])
        descriptions = {
            tool["function"]["name"]: tool["function"]["description"] for tool in direct_calls[0][1]
        }
        assert "without parsing a PDF" in descriptions["get_paper_info"]
        assert "may create or update parser cache" in descriptions["parse_pdf_with_grobid"]

        for question in ("这篇论文分析过吗？", "这篇论文之前解析过吗？"):
            state_llm, state_calls = _sequence_llm(
                [
                    AgentLLMResponse("", [AgentToolCall("info", "get_paper_info", '{"paper_id":"paper_1"}')]),
                    AgentLLMResponse("当前 paper_id 没有已有解析缓存。", []),
                ]
            )
            state_result = run_paper_agent(question, paper_id="paper_1", llm_call=state_llm)
            assert state_result.final_answer == "当前 paper_id 没有已有解析缓存。" and len(state_calls) == 2
            assert [event["name"] for event in state_result.events if event["type"] == "tool_call"] == ["get_paper_info"]

        analyze_llm, analyze_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("grobid", "parse_pdf_with_grobid", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("已开始基于论文内容分析方法。", []),
            ]
        )
        analyzed = run_paper_agent("帮我分析这篇新论文的方法", paper_id="paper_1", llm_call=analyze_llm)
        assert analyzed.final_answer == "已开始基于论文内容分析方法。" and len(analyze_calls) == 2
        assert [event["name"] for event in analyzed.events if event["type"] == "tool_call"] == ["parse_pdf_with_grobid"]
        assert "do not call a state-mutating\nTool to discover that state" in PAPER_AGENT_SYSTEM_PROMPT
        assert "Research memory records durable research\nnotes" in PAPER_AGENT_SYSTEM_PROMPT
        assert "current paper Tool Result as evidence" in PAPER_AGENT_SYSTEM_PROMPT

        memory_llm, memory_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("info", "get_paper_info", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("当前 paper_id 没有解析缓存；历史研究记忆中有相关论文记录。", []),
            ]
        )
        memory_result = run_paper_agent(
            "当前这篇论文之前分析过吗？",
            paper_id="paper_1",
            system_context="Research memory: paper_1 was discussed previously.",
            llm_call=memory_llm,
        )
        assert memory_result.final_answer == "当前 paper_id 没有解析缓存；历史研究记忆中有相关论文记录。"
        assert "Research memory: paper_1" in str(memory_calls[0][0][0]["content"])
        assert [event["name"] for event in memory_result.events if event["type"] == "tool_call"] == ["get_paper_info"]

        parser_llm, parser_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("grobid", "parse_pdf_with_grobid", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("", [AgentToolCall("pymupdf", "parse_pdf_with_pymupdf", '{"paper_id":"paper_1","pages":[1]}')]),
                AgentLLMResponse("根据首页，论文标题是 Agent Test Paper。", []),
            ]
        )
        parser_result = run_paper_agent("这篇论文叫什么？", paper_id="paper_1", llm_call=parser_llm)
        assert parser_result.final_answer == "根据首页，论文标题是 Agent Test Paper。" and len(parser_calls) == 3
        assert "Current paper_id: paper_1." in str(parser_calls[0][0][0]["content"])
        assert "missing_fields" in str(parser_calls[1][0]) and "title" in str(parser_calls[1][0])
        assert [event["tool_name"] for event in parser_result.context.metadata["agent_trace"] if event.get("event") == "tool_succeeded"] == [
            "parse_pdf_with_grobid",
            "parse_pdf_with_pymupdf",
        ]
        assert [event["name"] for event in parser_result.events if event["type"] == "tool_call"] == [
            "parse_pdf_with_grobid",
            "parse_pdf_with_pymupdf",
        ]
        assert any(
            event["type"] == "tool_result" and event["name"] == "parse_pdf_with_grobid" and "title missing" in event["summary"]
            for event in parser_result.events
        )

        recovery_llm, recovery_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("call_bad", "missing", "{}")]),
                AgentLLMResponse("", [AgentToolCall("call_good", "parse_pdf_with_pymupdf", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("已从第一页读取论文信息。", []),
            ]
        )
        recovery = run_paper_agent("读取论文信息", paper_id="paper_1", llm_call=recovery_llm)
        assert recovery.final_answer == "已从第一页读取论文信息。" and len(recovery_calls) == 3
        assert any("Unknown tool: missing" in str(message["content"]) for message in recovery.context.messages if message["role"] == "tool")

        no_paper_llm, no_paper_calls = _sequence_llm([AgentLLMResponse("请先上传或指定论文。", [])])
        no_paper = run_paper_agent("这篇论文有哪些章节？", llm_call=no_paper_llm)
        assert no_paper.final_answer == "请先上传或指定论文。" and len(no_paper_calls) == 1
        assert "No current paper is available" in str(no_paper_calls[0][0][0]["content"])

        looping_llm = lambda *_: AgentLLMResponse("", [AgentToolCall("call_loop", "parse_pdf_with_pymupdf", '{"paper_id":"paper_1"}')])
        try:
            run_paper_agent("持续调用", paper_id="paper_1", max_steps=2, llm_call=looping_llm)
        except AgentMaxStepsError as exc:
            assert (exc.step, exc.max_steps) == (2, 2)
        else:
            raise AssertionError("Expected AgentMaxStepsError")
    finally:
        paper_tools._parse_with_cache = original_parse

    print("ALL PAPER AGENT TESTS PASSED")


if __name__ == "__main__":
    main()
