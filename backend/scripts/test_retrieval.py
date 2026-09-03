"""Pure-local checks for section-aware retrieval and its Paper Tool."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.agents.paper_agent import run_paper_agent
from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services import retrieval_service
from app.services.llm_service import AgentLLMResponse, AgentToolCall
from app.tools import paper_tools


def _parsed_paper() -> SimpleNamespace:
    return SimpleNamespace(
        parser_name="local",
        sections=[],
        raw_text="\n".join(
            [
                "1 方法",
                "关键方法步骤包括检索增强、证据重排序和答案生成。" * 20,
                "2 实验",
                "实验使用准确率和消融实验验证方法效果。" * 20,
            ]
        ),
    )


def main() -> None:
    method = "方法关键步骤：检索增强、证据重排序、答案生成。" * 12
    experiments = "实验指标包括准确率、召回率和消融实验。" * 12
    chunks = retrieval_service.build_paper_chunks(
        "p1", {"method": method, "experiments": experiments}, chunk_size=80, overlap=12
    )
    assert len(chunks) > 2 and {chunk.section for chunk in chunks} == {"method", "experiments"}
    method_chunks = [chunk for chunk in chunks if chunk.section == "method"]
    assert method_chunks[1].start_char < method_chunks[0].end_char
    assert all(len(chunk.text) <= 80 for chunk in chunks)

    with tempfile.TemporaryDirectory() as directory:
        storage_dir = Path(directory)
        retrieval_service.save_paper_chunks("p1", chunks, storage_dir=storage_dir)
        result = retrieval_service.retrieve_paper_chunks("p1", "方法关键步骤检索增强", top_k=2, storage_dir=storage_dir)
        assert result["chunks"] and result["chunks"][0]["section"] == "method"
        assert all(len(chunk["text"]) <= 80 for chunk in result["chunks"])
        try:
            retrieval_service.retrieve_paper_chunks("missing", "方法", storage_dir=storage_dir)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Expected missing paper index")
        try:
            retrieval_service.retrieve_paper_chunks("p1", "", storage_dir=storage_dir)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected empty query rejection")

        original_dir = retrieval_service.PAPER_CHUNKS_DIR
        original_cached_loader = paper_tools._load_cached_parse
        retrieval_service.PAPER_CHUNKS_DIR = storage_dir
        paper_tools._load_cached_parse = lambda _paper_id, parser_name: _parsed_paper() if parser_name == "pymupdf" else None
        try:
            registry = ToolRegistry()
            paper_tools.register_paper_tools(registry)
            tool_result = execute_tool_call(
                AgentToolCall("retrieve", "retrieve_paper_context", '{"paper_id":"tool-paper","query":"检索增强关键步骤","top_k":2}'),
                registry,
            )
            data = json.loads(tool_result.content)
            assert tool_result.ok and data["chunks"] and "raw_text" not in tool_result.content

            responses = [
                AgentLLMResponse("", [AgentToolCall("agent-retrieve", "retrieve_paper_context", '{"paper_id":"tool-paper","query":"证据重排序"}')]),
                AgentLLMResponse("方法步骤已找到。", []),
            ]
            calls = []

            def fake_llm(messages, tools):
                calls.append((list(messages), list(tools)))
                return responses[len(calls) - 1]

            agent_result = run_paper_agent("方法有哪些关键步骤？", paper_id="tool-paper", llm_call=fake_llm)
            assert agent_result.final_answer == "方法步骤已找到。" and len(calls) == 2
            assert "retrieve_paper_context" in [tool["function"]["name"] for tool in calls[0][1]]
            assert any(event.get("tool_name") == "retrieve_paper_context" for event in agent_result.context.metadata["agent_trace"])
        finally:
            retrieval_service.PAPER_CHUNKS_DIR = original_dir
            paper_tools._load_cached_parse = original_cached_loader

    print("ALL RETRIEVAL TESTS PASSED")


if __name__ == "__main__":
    main()
