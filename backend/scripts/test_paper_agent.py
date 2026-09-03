"""Pure-local checks for the Paper Agent's LLM-selected tool decisions."""

from types import SimpleNamespace

from app.agents.agent_loop import AgentMaxStepsError
from app.agents.paper_agent import run_paper_agent
from app.services.llm_service import AgentLLMResponse, AgentToolCall
from app.tools import paper_tools


def _parsed_paper() -> SimpleNamespace:
    section_text = "内容。" * 100
    return SimpleNamespace(
        parser_name="pymupdf",
        title="Agent Test Paper",
        authors=["Test Author"],
        abstract="用于验证 Paper Agent 的本地论文摘要。",
        parser_warnings=[],
        parser_meta={"year": "2026", "venue": "Local Venue"},
        raw_text="\n".join(
            [
                "摘要",
                "用于章节提取测试的摘要。",
                "1 引言",
                section_text,
                "2 相关工作",
                section_text,
                "3 方法",
                section_text,
                "4 实验",
                section_text,
                "5 结论",
                section_text,
            ]
        ),
    )


def _sequence_llm(responses: list[AgentLLMResponse]):
    calls: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []

    def call(messages: list[dict[str, object]], tools: list[dict[str, object]]) -> AgentLLMResponse:
        calls.append((list(messages), list(tools)))
        return responses[len(calls) - 1]

    return call, calls


def _assert_paper_schemas(tools: list[dict[str, object]]) -> None:
    assert [tool["function"]["name"] for tool in tools] == [
        "get_paper_info",
        "extract_sections",
        "retrieve_paper_context",
        "save_research_memory",
        "get_multi_paper_context",
    ]


def main() -> None:
    original_loader = paper_tools._load_paper
    paper_tools._load_paper = lambda paper_id: (_parsed_paper(), "zh", "pymupdf")
    try:
        direct_llm, direct_calls = _sequence_llm([AgentLLMResponse("我可以帮你阅读论文。", [])])
        direct = run_paper_agent("你能做什么？", llm_call=direct_llm)
        assert direct.final_answer == "我可以帮你阅读论文。" and len(direct_calls) == 1
        _assert_paper_schemas(direct_calls[0][1])

        single_llm, single_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("call_info", "get_paper_info", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("论文标题是 Agent Test Paper。", []),
            ]
        )
        single = run_paper_agent("标题是什么？", paper_id="paper_1", llm_call=single_llm)
        assert single.final_answer == "论文标题是 Agent Test Paper。" and len(single_calls) == 2
        assert "Current paper_id: paper_1." in str(single_calls[0][0][0]["content"])

        multi_llm, multi_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("call_1", "get_paper_info", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("", [AgentToolCall("call_2", "extract_sections", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("论文信息和主要章节已整理。", []),
            ]
        )
        multi = run_paper_agent("告诉我论文信息和主要章节", paper_id="paper_1", llm_call=multi_llm)
        assert multi.final_answer == "论文信息和主要章节已整理。" and len(multi_calls) == 3
        assert [event["tool_name"] for event in multi.context.metadata["agent_trace"] if event.get("event") == "tool_succeeded"] == [
            "get_paper_info",
            "extract_sections",
        ]

        recovery_llm, recovery_calls = _sequence_llm(
            [
                AgentLLMResponse("", [AgentToolCall("call_bad", "missing", "{}")]),
                AgentLLMResponse("", [AgentToolCall("call_good", "get_paper_info", '{"paper_id":"paper_1"}')]),
                AgentLLMResponse("已恢复并读取论文信息。", []),
            ]
        )
        recovery = run_paper_agent("读取论文信息", paper_id="paper_1", llm_call=recovery_llm)
        assert recovery.final_answer == "已恢复并读取论文信息。" and len(recovery_calls) == 3
        assert any("Unknown tool: missing" in str(message["content"]) for message in recovery.context.messages if message["role"] == "tool")

        no_paper_llm, no_paper_calls = _sequence_llm([AgentLLMResponse("请先上传或指定论文。", [])])
        no_paper = run_paper_agent("这篇论文有哪些章节？", llm_call=no_paper_llm)
        assert no_paper.final_answer == "请先上传或指定论文。" and len(no_paper_calls) == 1
        assert "No current paper is available" in str(no_paper_calls[0][0][0]["content"])

        looping_llm = lambda *_: AgentLLMResponse("", [AgentToolCall("call_loop", "get_paper_info", '{"paper_id":"paper_1"}')])
        try:
            run_paper_agent("持续调用", paper_id="paper_1", max_steps=2, llm_call=looping_llm)
        except AgentMaxStepsError as exc:
            assert (exc.step, exc.max_steps) == (2, 2)
        else:
            raise AssertionError("Expected AgentMaxStepsError")
    finally:
        paper_tools._load_paper = original_loader

    print("ALL PAPER AGENT TESTS PASSED")


if __name__ == "__main__":
    main()
