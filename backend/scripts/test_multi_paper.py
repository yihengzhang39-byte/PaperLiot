"""Pure-local checks for active papers and source-labeled multi-paper context."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.agents.paper_agent import run_paper_agent
from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services import retrieval_service
from app.services.session_service import ChatSessionState, normalize_active_paper_ids
from app.services.llm_service import AgentLLMResponse, AgentToolCall
from app.tools import paper_tools
from app.tools.multi_paper_tools import get_multi_paper_context, register_multi_paper_tools


def _parsed_paper(paper_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        parser_name="local",
        sections=[],
        raw_text=f"1 方法\n{paper_id} 的方法使用共享检索信号和独有证据。" * 20,
    )


def main() -> None:
    assert normalize_active_paper_ids(["p1", "p2", "p1"], "p2") == ["p1", "p2"]
    assert ChatSessionState(paper_id="p2", active_paper_ids=["p1", "p2"]).paper_id == "p2"

    with tempfile.TemporaryDirectory() as directory:
        original_dir = retrieval_service.PAPER_CHUNKS_DIR
        original_cached_loader = paper_tools._load_cached_parse
        retrieval_service.PAPER_CHUNKS_DIR = Path(directory)
        def load_cached_paper(paper_id, parser_name):
            if paper_id == "missing" or parser_name != "pymupdf":
                return None
            return _parsed_paper(paper_id)

        paper_tools._load_cached_parse = load_cached_paper
        try:
            result = get_multi_paper_context(["p1", "p2", "p1"], "共享检索信号", top_k=1)
            assert [paper["paper_id"] for paper in result["papers"]] == ["p1", "p2"]
            assert all(paper["chunks"] and paper["chunks"][0]["paper_id"] == paper["paper_id"] for paper in result["papers"])
            unknown = get_multi_paper_context(["missing"], "检索", top_k=1)
            assert unknown["papers"][0]["paper_id"] == "missing" and unknown["papers"][0]["error"]

            registry = ToolRegistry()
            register_multi_paper_tools(registry)
            dispatched = execute_tool_call(
                AgentToolCall("multi", "get_multi_paper_context", '{"paper_ids":["p1","p2"],"query":"共享检索信号","top_k":1}'),
                registry,
            )
            assert dispatched.ok and len(json.loads(dispatched.content)["papers"]) == 2

            responses = [
                AgentLLMResponse("", [AgentToolCall("compare", "get_multi_paper_context", '{"paper_ids":["p1","p2"],"query":"共享检索信号"}')]),
                AgentLLMResponse("两篇论文的上下文已分别比较。", []),
            ]
            calls = []

            def fake_llm(messages, tools):
                calls.append((list(messages), list(tools)))
                return responses[len(calls) - 1]

            agent_result = run_paper_agent(
                "比较两篇论文的方法", paper_id="p2", active_paper_ids=["p1", "p2"], llm_call=fake_llm
            )
            assert agent_result.final_answer == "两篇论文的上下文已分别比较。" and len(calls) == 2
            assert "Active paper_ids: p1, p2." in calls[0][0][0]["content"]
            assert "get_multi_paper_context" in [tool["function"]["name"] for tool in calls[0][1]]
        finally:
            retrieval_service.PAPER_CHUNKS_DIR = original_dir
            paper_tools._load_cached_parse = original_cached_loader

    print("ALL MULTI PAPER TESTS PASSED")


if __name__ == "__main__":
    main()
