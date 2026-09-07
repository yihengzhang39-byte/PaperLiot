"""Offline phase-two checks with temporary canonical parser caches and fake LLMs."""

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import run_agent
from app.agents.paper_agent import build_paper_tool_registry, run_paper_agent
from app.core.config import ContextConfig, ToolResultConfig
from app.repositories.debug_trace_repository import DebugTraceRepository
from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services import file_service, retrieval_service
from app.services.context_service import ContextBudgetError, check_request
from app.services.debug_trace_service import DebugTraceService
from app.services.llm_service import AgentLLMDelta, AgentLLMResponse, AgentToolCall, AgentToolCallDelta
from app.services.parsers.schema import ParsedPaper, ParsedSection
from app.services.session_event_service import SessionEventService, _history_result, _sanitize
from app.services.session_model_history_service import project_session_events_to_messages
from app.services.tool_result_service import model_evidence_refs, project_tool_result
from app.tools import paper_tools
from app.tools.multi_paper_tools import get_multi_paper_context


def expect_error(call, error_type=ValueError, text=""):
    try:
        call()
    except error_type as error:
        assert text in str(error), str(error)
        return error
    raise AssertionError(f"Expected {error_type.__name__}")


def tool_call(name, arguments, call_id="call-1"):
    return AgentToolCall(call_id, name, arguments)


def make_cache(root, paper_id, parser):
    text = "\n".join(f"EVIDENCE {paper_id} {parser} 中文引号 JSON \\\" 片段{i:04d} accuracy improves. " * 10 for i in range(80))
    parsed = ParsedPaper(parser_name=parser, raw_text=text, title="Paper " + paper_id, sections=[ParsedSection(title="Method", text=text)], parser_warnings=["OCR uncertainty: verify equations", "missing page labels"])
    path = root / f"{paper_id}_{parser}.json"
    path.write_text(json.dumps(parsed.model_dump(), ensure_ascii=False), encoding="utf-8")
    return path


def test_projection(registry, cache):
    before = {path.name: path.read_bytes() for path in cache.iterdir()}
    result = execute_tool_call(tool_call("retrieve_paper_context", {"paper_id": "p1", "query": "EVIDENCE", "top_k": 8}), registry)
    raw, model = json.loads(result.content), json.loads(result.model_content)
    assert len(result.content) > 12000 >= len(result.model_content)
    assert raw["chunks"] and raw["sources"][0]["chunks"]  # raw contract stays intact
    assert all("chunks" not in source for source in model["sources"])
    assert model["chunks"] and all("EVIDENCE p1" in chunk["text"] for chunk in model["chunks"])
    assert model["success"] is True and model["omitted"] is True
    assert any("OCR uncertainty" in warning for source in model["sources"] for warning in source["warnings"])
    assert result.tool_call_id == "call-1" and result.ok
    for chunk in model["chunks"]:
        assert result.model_content.count(json.dumps(chunk["text"], ensure_ascii=False)) == 1
        assert retrieval_service.valid_evidence_ref(chunk)
    assert result.history_result["evidence_refs"] == model_evidence_refs(result.model_content)
    assert "EVIDENCE" not in json.dumps(result.history_result["evidence_refs"])
    frozen = copy.deepcopy(raw)
    project_tool_result("retrieve_paper_context", json.dumps(raw))
    assert raw == frozen and before == {path.name: path.read_bytes() for path in cache.iterdir()}

    failed = {"paper_id": "p1", "parser": "grobid", "success": False, "error": "parse failed: " + "x" * 20000, "warnings": ["critical warning " + "w" * 10000], "sections": [{"text": "huge" * 10000}]}
    output = json.loads(project_tool_result("parse_pdf_with_grobid", json.dumps(failed)))
    assert output["success"] is False and output["status"] == "error"
    assert output["error"].startswith("parse failed") and output["warnings"][0].startswith("critical warning")
    assert output["omitted"] and failed["sections"][0]["text"] == "huge" * 10000
    source = {"success": True, "paper_id": "p1", "chunks": [raw["chunks"][0]] * 3, "sources": raw["sources"]}
    output = json.loads(project_tool_result("retrieve_paper_context", json.dumps(source)))
    assert len(output["chunks"]) == 1
    # No generic file spill for memory writes or other Tools without a replay source.
    memory = json.loads(project_tool_result("save_user_profile", json.dumps({"saved": True, "updated_fields": {"preferences": ["x" * 500] * 1000}})))
    assert memory["saved"] is True and memory["omitted"] and memory["readback"] is None
    escaped = project_tool_result("plain_text", "\x00\\\"" * 20000)
    assert len(escaped) <= 12000 and json.loads(escaped)["omitted"]
    arbitrary = execute_tool_call(tool_call("custom", {}), ToolRegistry({"custom": lambda: {"papers": 7, "chunks": None}}))
    assert arbitrary.ok and json.loads(arbitrary.model_content) == {"papers": 7, "chunks": None}
    state = AgentContext()
    custom = ToolRegistry()
    custom.register("write_fact", lambda: {"success": True, "fact": "unchanged"}, produces=("written",))
    with patch("app.runtime.tool_executor.project_tool_result", side_effect=RuntimeError("projection failed")):
        preserved = execute_tool_call(tool_call("write_fact", {}), custom, context=state)
    assert preserved.ok and json.loads(preserved.content) == {"success": True, "fact": "unchanged"}
    assert state.state["written"] and "projection_error" in json.loads(preserved.model_content)


def test_multi(registry):
    requested = [f"p{i}" for i in range(1, 8)] + ["missing"]
    result = execute_tool_call(tool_call("get_multi_paper_context", {"paper_ids": requested, "query": "EVIDENCE", "top_k": 8}), registry)
    model = json.loads(result.model_content)
    assert len(result.model_content) <= 12000 and model["status"] == "partial"
    assert [paper["paper_id"] for paper in model["papers"]] == requested
    for paper in model["papers"][:-1]:
        assert paper["chunks"], paper
        assert paper["omitted_chunks"] > 0 and paper["omitted_chunk_range"]
        assert all(chunk["paper_id"] == paper["paper_id"] and f"EVIDENCE {paper['paper_id']}" in chunk["text"] for chunk in paper["chunks"])
    assert model["papers"][-1]["success"] is False and model["papers"][-1]["error"]
    assert result.history_result["papers"][-1]["success"] is False
    with patch.dict(os.environ, {"TOOL_RESULT_MAX_CHARS": "4096"}):
        bounded = execute_tool_call(tool_call("get_multi_paper_context", {"paper_ids": requested, "query": "EVIDENCE", "top_k": 8}), registry)
        view = json.loads(bounded.model_content)
        assert len(bounded.model_content) <= 4096
        assert [paper["paper_id"] for paper in view["papers"]] == requested
        assert all(paper.get("chunks") or paper.get("coverage") == "none" for paper in view["papers"])


def test_readback(registry, cache):
    result = execute_tool_call(tool_call("retrieve_paper_context", {"paper_id": "p1", "query": "EVIDENCE", "parser": "grobid"}), registry)
    ref = result.history_result["evidence_refs"][0]
    identity = {key: ref[key] for key in ("paper_id", "parser", "cache_version", "chunk_id")}
    chunk = json.loads(result.content)["chunks"][0]
    read = paper_tools.read_paper_chunk(**identity, max_chars=100)
    assert read["text"] == chunk["text"][:100] and read["next_offset"] == 100
    more = paper_tools.read_paper_chunk(**identity, offset=100, max_chars=100)
    assert more["text"] == chunk["text"][100:200]
    assert len(json.dumps(read, ensure_ascii=False)) <= 12000
    assert _sanitize({"evidence_refs": [ref]}, limit=1)["evidence_refs"][0] == ref
    assert _history_result({"evidence_refs": [ref] * 64})["evidence_refs"] == [ref] * 64
    denied = execute_tool_call(tool_call("read_paper_chunk", {**identity, "paper_id": "outside"}), registry)
    assert not denied.ok and "evidence_access_denied" in denied.content
    for arguments in ({"max_chars": 100000}, {"max_chars": True}, {"offset": -1}, {"offset": True}, {"offset": 1000000}, {"chunk_id": "old:Method:0"}, {"chunk_id": "c_" + "0" * 64}, {"cache_version": "sha256:" + "0" * 64}, {"parser": "pymupdf"}, {"paper_id": "../outside"}):
        expect_error(lambda: paper_tools.read_paper_chunk(**{**identity, **arguments}))
    cache_path = cache / "p1_grobid.json"
    original = cache_path.read_bytes()
    changed = json.loads(original)
    changed["raw_text"] += " changed"
    cache_path.write_text(json.dumps(changed), encoding="utf-8")
    stale = execute_tool_call(tool_call("read_paper_chunk", identity, "stale-call"), registry)
    assert not stale.ok and stale.tool_call_id == "stale-call" and "stale_evidence_reference" in stale.content
    cache_path.write_bytes(original)
    with patch.dict(os.environ, {"RAG_CHUNK_SIZE": "800"}):
        expect_error(lambda: paper_tools.read_paper_chunk(**identity), text="stale_evidence_reference")
    cache_path.write_text("{}")
    expect_error(lambda: paper_tools.read_paper_chunk(**identity), text="evidence_cache_missing")
    cache_path.write_bytes(original)
    # A cache symlink cannot escape the fixed cache root.
    with tempfile.TemporaryDirectory() as outside:
        target = Path(outside) / "secret.json"
        target.write_bytes(original)
        (cache / "symlink_grobid.json").symlink_to(target)
        expect_error(lambda: paper_tools.read_paper_chunk(**{**identity, "paper_id": "symlink"}), text="inside the local cache")
        (cache / "symlink_grobid.json").unlink()


def test_validation(registry):
    for top_k in (0, -1, 9, True, 1.5, "2"):
        for name, args in (("retrieve_paper_context", {"paper_id": "missing", "query": "EVIDENCE", "top_k": top_k}), ("get_multi_paper_context", {"paper_ids": ["p1"], "query": "EVIDENCE", "top_k": top_k})):
            result = execute_tool_call(tool_call(name, args), registry)
            assert not result.ok and "top_k" in result.content
    expect_error(lambda: get_multi_paper_context(["p1"] * 9, "EVIDENCE"))
    expect_error(lambda: get_multi_paper_context(["../../etc/passwd"], "EVIDENCE"))
    expect_error(lambda: paper_tools.retrieve_paper_context("p1", "x" * 2001))
    for pages in ([1] * 9, [True], [0], "all"):
        with patch.object(paper_tools, "_parse_with_cache", side_effect=AssertionError("must validate before parsing")) as parser_call:
            assert paper_tools.parse_pdf_with_pymupdf("p1", pages)["success"] is False
            parser_call.assert_not_called()
    expect_error(lambda: ToolResultConfig(max_chars=100))


def test_loop_and_history(registry, root):
    projected = []
    for streaming in (False, True):
        database = root / f"session-{streaming}.db"
        session = SessionEventService("s", database_path=database)
        session.start("query", paper_id="p1", active_paper_ids=["p1"])
        debug = DebugTraceService("s", session.turn_id, database_path=database, enabled=True)
        call_count = 0
        arguments = {"paper_id": "p1", "query": "EVIDENCE", "top_k": 8}
        def fake(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return iter([AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "paired-id", "retrieve_paper_context", json.dumps(arguments))])]) if streaming else AgentLLMResponse("", [tool_call("retrieve_paper_context", arguments, "paired-id")])
            tool_message = messages[-1]
            assert tool_message["tool_call_id"] == "paired-id"
            assert messages[-2]["tool_calls"][0]["id"] == "paired-id"
            projected.append(tool_message["content"])
            return iter([AgentLLMDelta("answer")]) if streaming else AgentLLMResponse("answer", [])
        state = AgentContext(paper_id="p1", messages=[{"role": "user", "content": "query"}], state={"paper_id": "p1", "parsed_pdf": True})
        result = run_agent(state, tool_registry=registry, event_sink=session.persist_runtime_event, debug_trace=debug, **{("llm_stream" if streaming else "llm_call"): fake})
        assert result.final_answer == "answer" and call_count == 2
        history = project_session_events_to_messages("s", database_path=database)
        history_tool = next(item for item in history if item["role"] == "tool")
        assert history_tool["tool_call_id"] == "paired-id"
        refs = json.loads(history_tool["content"])["evidence_refs"]
        assert refs == model_evidence_refs(projected[-1])
        for ref in refs:
            read = paper_tools.read_paper_chunk(**{key: ref[key] for key in ("paper_id", "parser", "cache_version", "chunk_id")}, offset=ref["offset"], max_chars=min(ref["shown_chars"], 2000))
            shown = next(item for item in json.loads(projected[-1])["chunks"] if item["chunk_id"] == ref["chunk_id"])
            assert read["text"] == shown["text"][:len(read["text"])]
        records = DebugTraceRepository(database).list_by_turn("s", session.turn_id)
        tool_record = next(row["data"] for row in records if row["event_type"] == "tool/result_debug")
        assert tool_record["raw_result_size"] > tool_record["model_result_size"] == len(projected[-1])
        assert tool_record["history_result_size"] < tool_record["raw_result_size"]
        expected = check_request(state.messages[:-1], [], ContextConfig(1000000, 4096), model="")
        assert state.metadata["context_budgets"][-1]["I"] == expected.I
    assert projected[0] == projected[1]

    for streaming in (False, True):
        calls = []
        def only_first(*_args):
            calls.append(1)
            assert len(calls) == 1, "Projected Tool results still exceed B; do not send step two"
            args = {"paper_id": "p1", "query": "EVIDENCE", "top_k": 8}
            return iter([AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "bounded-id", "retrieve_paper_context", json.dumps(args))])]) if streaming else AgentLLMResponse("", [tool_call("retrieve_paper_context", args, "bounded-id")])
        state = AgentContext(paper_id="p1", messages=[{"role": "user", "content": "query"}], state={"paper_id": "p1", "parsed_pdf": True})
        with patch("app.agents.agent_loop.get_context_config", return_value=ContextConfig(2500, 100, 10)):
            expect_error(lambda: run_agent(state, tool_registry=registry, **{("llm_stream" if streaming else "llm_call"): only_first}), ContextBudgetError)
        assert len(calls) == 1 and state.messages[-1]["tool_call_id"] == "bounded-id"
        assert len(state.messages[-1]["content"]) <= 12000 and state.metadata["context_budgets"][-1]["hard_budget_exceeded"]

    # Model Tool control does not delete a large user message to get under B.
    with patch("app.agents.agent_loop.get_context_config", return_value=ContextConfig(1000, 100, 10)):
        state = AgentContext(messages=[{"role": "user", "content": "用户" * 1000}])
        expect_error(lambda: run_agent(state, llm_call=lambda *_: (_ for _ in ()).throw(AssertionError("must not send"))), ContextBudgetError)
        assert state.messages[0]["content"] == "用户" * 1000


def main():
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory)
        cache = root / "cache"
        cache.mkdir()
        stack.enter_context(patch.dict(os.environ, {"LLM_PROVIDER": "mock", "LLM_MODEL": "", "AGENT_CONTEXT_WINDOW": "1000000", "AGENT_MAX_OUTPUT_TOKENS": "4096", "AGENT_CONTEXT_SAFETY_TOKENS": "1024", "TOOL_RESULT_MAX_CHARS": "12000", "TOOL_READ_MAX_CHARS": "2000", "RAG_CHUNK_SIZE": "1200", "RAG_CHUNK_OVERLAP": "200"}))
        stack.enter_context(patch.dict("sys.modules", {"tiktoken": None}))
        stack.enter_context(patch.object(file_service, "PAPER_PARSE_CACHE_DIR", cache))
        stack.enter_context(patch.object(retrieval_service, "PAPER_CHUNKS_DIR", root / "unused-index"))
        stack.enter_context(patch.object(paper_tools, "parse_pdf", side_effect=AssertionError("readback must not parse")))
        stack.enter_context(patch("app.services.llm_service.urlopen", side_effect=AssertionError("no real LLM")))
        for i in range(1, 8):
            for parser in ("grobid", "pymupdf"):
                make_cache(cache, f"p{i}", parser)
        original_cache = {path.name: path.read_bytes() for path in cache.iterdir()}
        registry = build_paper_tool_registry(allowed_paper_ids=[f"p{i}" for i in range(1, 8)])
        test_projection(registry, cache)
        test_multi(registry)
        test_readback(registry, cache)
        test_validation(registry)
        test_loop_and_history(registry, root)
        assert not (root / "unused-index").exists()
        assert original_cache == {path.name: path.read_bytes() for path in cache.iterdir()}
    print("ALL TOOL RESULT CONTROL AND EVIDENCE READBACK TESTS PASSED")


if __name__ == "__main__":
    main()
