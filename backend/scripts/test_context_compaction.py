"""Offline, temporary SQLite checks for bounded compaction and durable recovery."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import run_agent
from app.core.config import CompactionConfig, ContextConfig, LLMConfig
from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository
from app.services import context_compaction_service as compaction, llm_service
from app.services.context_service import ContextBudgetError, check_request
from app.services.session_model_history_service import SUMMARY_SECTIONS, load_model_history
from app.tools.session_history_tools import register_session_history_tool, read_session_history
from app.runtime.tool_registry import ToolRegistry
from app.runtime.tool_executor import execute_tool_call
from app.services.llm_service import AgentLLMResponse, AgentLLMDelta, AgentToolCall


CONFIG = ContextConfig(window=12000, max_output_tokens=1000, safety_tokens=100)
SUMMARY = CompactionConfig(recent_ratio=.16, target_tokens=512, max_output_tokens=2048)
LLM = LLMConfig(provider="mock", api_key="", base_url="", model="offline")


def seed(database, name, sizes, current=1600):
    repo = SessionEventRepository(database)
    SessionRepository(database).get_or_create(name)
    for index, size in enumerate(sizes):
        turn = f"old-{index}"
        repo.append_event(name, "user/message", {"content": f"goal-{index}:" + "x" * size}, turn_id=turn)
        repo.append_event(name, "turn/start", {}, turn_id=turn)
        repo.append_event(name, "assistant/message", {"content": "已确认结论"}, turn_id=turn)
        repo.append_event(name, "turn/end", {"status": "success"}, turn_id=turn)
    repo.append_event(name, "user/message", {"content": "current:" + "y" * current}, turn_id="current")
    repo.append_event(name, "turn/start", {}, turn_id="current")
    history = load_model_history(name, before_turn_id="current", database_path=database)
    context = AgentContext(session_id=name, turn_id="current", database_path=database, history=history,
        history_end=1 + len(history.messages), messages=[{"role": "system", "content": "fixed system"}, *history.messages, {"role": "user", "content": "current:" + "y" * current}],
        metadata={"message_origins": [{"origin": "system_prompt"}, *history.origins, {"origin": "current_user"}], "paper_agent": {"paper_id": "p1", "active_paper_ids": ["p1", "p2"]}})
    return context, repo


def answer(request, **kwargs):
    budget = check_request(request, [], kwargs["budget_config"], model=LLM.model)
    budget.require_sendable()
    payload = json.loads(request[1]["content"])
    assert payload["current_papers"]["active_paper_ids"] == ["p1", "p2"]
    assert "current:" not in json.dumps(payload["old_turns"])
    ref = payload["sources"][0]["id"]
    sections = dict.fromkeys(SUMMARY_SECTIONS, "无")
    sections["关键结论及证据引用"] = f"已确认历史记录 [{ref}]"
    return AgentLLMResponse(json.dumps({"sections": sections, "source_refs": [ref]}, ensure_ascii=False), [], usage={"total_tokens": 80}, finish_reason="stop")


def run(context, caller=answer, config=CONFIG):
    return compaction.compact_for_request(context, [], LLM, config, summary_call=caller)


def rejects(fn, text=None):
    try:
        fn()
    except Exception as exc:
        if text:
            assert text in str(exc), (text, str(exc))
        return exc
    raise AssertionError("Expected rejection")


def main():
    with tempfile.TemporaryDirectory() as directory, patch.object(compaction, "get_compaction_config", return_value=SUMMARY):
        db = Path(directory) / "events.db"
        c, repo = seed(db, "below", [7000, 1400])
        raw = deepcopy(repo.list_events("below"))
        b = run(c)
        assert b.I <= b.G and c.metadata["compaction"]["summary_calls"] == 1, (b, c.metadata)
        assert c.metadata["compaction"]["revision"] == 1
        assert repo.list_events("below")[:len(raw)] == raw  # Original UI events untouched.
        restored = load_model_history("below", before_turn_id="current", database_path=db)
        assert restored.messages == c.history.messages
        assert len(c.metadata["message_origins"]) == len(c.messages)
        assert sum(m["content"].startswith("current:") for m in c.messages) == 1
        assert restored.messages[0]["role"] == "assistant"
        assert "goal-0:" not in json.dumps(restored.messages)
        cp = restored.checkpoint
        assert cp["data"]["usage"] == {"total_tokens": 80}
        ref = cp["data"]["source_refs"][0]
        read = read_session_history("below", "current", ref["start_seq"], ref["end_seq"], max_chars=512, database_path=db)
        assert len(json.dumps(read, ensure_ascii=False)) <= 512 and read["omitted"]
        assert read["fields"][0]["seq"] == ref["start_seq"]
        next_page = read_session_history("below", "current", read["next_seq"], ref["end_seq"], field_index=read["next_field"], offset=read["next_offset"], max_chars=512, database_path=db)
        assert next_page["fields"][0]["offset"] > 0
        registry = ToolRegistry()
        register_session_history_tool(registry, "below", "current", database_path=db)
        rejects(lambda: registry.get("read_session_history")(1, 4, session_id="foreign"))
        for args in [(True, 4), (1, 200), (1, 999), (1, 4, 0, 999999)]:
            rejects(lambda args=args: registry.get("read_session_history")(*args))

        failed_read = execute_tool_call(AgentToolCall("read-id", "read_session_history", {"start_seq": 1, "end_seq": 999}), registry)
        assert not failed_read.ok and "history_read_invalid_range_or_limit" in failed_read.model_content
        assert failed_read.tool_call_id == "read-id"
        actual_read = execute_tool_call(AgentToolCall("read-ok", "read_session_history", {"start_seq": 1, "end_seq": 4, "max_chars": 512}), registry)
        assert actual_read.ok and actual_read.history_result["omitted"] is True
        assert actual_read.history_result["event_ref"]["start_seq"] == 1

        # Field pagination reconstructs Chinese/JSON-like text without cutting JSON or IDs.
        text = '中文{"number":12,"unit":"ms"}\n' * 100
        repo.append_event("field-pages", "user/message", {"content": text}, turn_id="old")
        reference = {"paper_id": "p1", "parser": "grobid", "cache_version": "sha256:" + "a" * 64, "chunk_id": "c_" + "b" * 64}
        repo.append_event("field-pages", "tool/result", {"history_result": {"evidence_refs": [reference]}}, turn_id="old")
        repo.append_event("field-pages", "turn/end", {"status": "success"}, turn_id="old")
        repo.append_event("field-pages", "turn/start", {}, turn_id="current")
        cursor, index, offset, reconstructed, identities = 1, 0, 0, "", {}
        for _ in range(300):
            page = read_session_history("field-pages", "current", cursor, 3, field_index=index, offset=offset, max_chars=512, database_path=db)
            assert len(json.dumps(page, ensure_ascii=False)) <= 512
            for field in page["fields"]:
                if field["path"] == ["content"]:
                    reconstructed += field["value"]
                if field["path"] and field["path"][-1] in reference:
                    identities[field["path"][-1]] = field["value"]
                    assert field["value"] == reference[field["path"][-1]] and field["offset"] == 0
            if not page["omitted"]:
                break
            following = page["next_seq"], page["next_field"], page["next_offset"]
            assert following > (cursor, index, offset)
            cursor, index, offset = following
        else:
            raise AssertionError("Readback pagination did not make progress")
        assert reconstructed == text and identities == reference

        c, _ = seed(db, "acceptable", [3000, 1600, 1600], current=6000)
        b = run(c)
        assert b.G < b.I < b.T and c.metadata["compaction"]["summary_calls"] == 1, (b, c.metadata)

        c, _ = seed(db, "two", [3000, 1600, 1600], current=7800)
        requests = []
        def record(request, **kwargs):
            requests.append(deepcopy(request))
            return answer(request, **kwargs)
        b = run(c, record)
        assert b.I < b.T and len(requests) == 2, (b, c.metadata)
        assert requests[0] != requests[1]
        assert json.loads(requests[1][1]["content"])["previous_summary"]
        assert c.metadata["compaction"]["revision"] == 2
        c.messages.append({"role": "assistant", "content": "new step" * 600})
        c.metadata["message_origins"].append({"origin": "current_run_assistant"})
        c.step = 2
        run(c, record)
        assert len(requests) == 2

        for name, current, hard in [("overtrigger", 9400, False), ("overhard", 11000, True)]:
            c, _ = seed(db, name, [3000, 1600, 1600], current=current)
            b = run(c)
            assert c.metadata["compaction"]["summary_calls"] == 2
            assert b.I >= b.T and b.hard_budget_exceeded == hard, (name, b)
            if hard:
                rejects(b.require_sendable)
            else:
                b.require_sendable()

        c, _ = seed(db, "second-fails", [3000, 1600, 1600], current=7800)
        calls = []
        def fail_second(request, **kwargs):
            calls.append(request)
            if len(calls) == 2:
                raise TimeoutError("fake timeout")
            return answer(request, **kwargs)
        b = run(c, fail_second)
        assert len(calls) == 2 and c.metadata["compaction"]["revision"] == 1
        assert load_model_history(c.session_id, before_turn_id="current", database_path=db).messages == c.history.messages
        assert b.I < b.B

        c, _ = seed(db, "fail-count", [3000, 1600, 1600], current=7800)
        b = run(c, lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("fake")))
        assert c.metadata["compaction"]["summary_calls"] == 2 and c.metadata["compaction"]["revision"] == 0
        run(c)
        assert c.metadata["compaction"]["summary_calls"] == 2

        for name, mutation in [
            ("length", lambda response: replace(response, finish_reason="length")),
            ("empty", lambda response: replace(response, content="")),
            ("structure", lambda response: replace(response, content='{"sections":{},"source_refs":[]}')),
            ("ref", lambda response: replace(response, content=response.content.replace('R1', 'R999'))),
            ("growth", lambda response: replace(response, content=response.content.replace('无', 'z' * 6000))),
        ]:
            c, repo = seed(db, name, [7000, 1400])
            original = deepcopy(c.messages)
            run(c, lambda request, mutation=mutation, **kwargs: mutation(answer(request, **kwargs)))
            assert c.messages == original and not any(e["event_type"] == "context/checkpoint" for e in repo.list_events(name)), name
            assert c.metadata["compaction"]["summary_calls"] <= 2
            if name == "growth":
                assert c.metadata["compaction"]["summary_calls"] == 1

        c, _ = seed(db, "save-fail", [7000, 1400])
        original = deepcopy(c.messages)
        with patch.object(SessionEventRepository, "append_checkpoint", side_effect=OSError("disk full")):
            run(c)
        assert c.messages == original and c.metadata["compaction"]["revision"] == 0
        assert c.metadata["compaction"]["stop_reason"] == "checkpoint_save_failed"

        c, repo = seed(db, "conflict", [7000, 1400])
        run(c)
        data = deepcopy(c.history.checkpoint["data"])
        rejects(lambda: repo.append_checkpoint("conflict", "current", data), "checkpoint_conflict")
        data["previous_checkpoint_seq"] = c.history.checkpoint["seq"]
        data["covered_through_seq"] = 1
        data["source_refs"] = [{"id": "R1", "kind": "events", "start_seq": 1, "end_seq": 1}]
        rejects(lambda: repo.append_checkpoint("conflict", "current", data), "boundary")
        data = deepcopy(c.history.checkpoint["data"])
        SessionRepository(db).delete("conflict")
        rejects(lambda: repo.append_checkpoint("conflict", "current", data), "session_deleted")
        assert SessionRepository(db).get("conflict") is None

        # Invalid latest checkpoint cannot displace the last valid committed version.
        c, repo = seed(db, "invalid-latest", [7000, 1400])
        run(c)
        valid_seq = c.history.checkpoint["seq"]
        malformed = deepcopy(c.history.checkpoint["data"])
        malformed["previous_checkpoint_seq"] = valid_seq
        malformed["summary"] = ""
        repo.append_event(c.session_id, "context/checkpoint", malformed, turn_id="current")
        assert load_model_history(c.session_id, before_turn_id="current", database_path=db).checkpoint["seq"] == valid_seq
        # A prefix must never cross an old still-running turn.
        SessionRepository(db).get_or_create("open-boundary")
        repo.append_event("open-boundary", "turn/start", {}, turn_id="open")
        repo.append_event("open-boundary", "user/message", {"content": "ended goal"}, turn_id="ended")
        end_seq = repo.append_event("open-boundary", "turn/end", {"status": "success"}, turn_id="ended")
        repo.append_event("open-boundary", "turn/start", {}, turn_id="current")
        unsafe = deepcopy(c.history.checkpoint["data"])
        unsafe.update(previous_checkpoint_seq=None, covered_through_seq=end_seq, source_refs=[{"id": "R1", "kind": "events", "start_seq": 2, "end_seq": end_seq}])
        rejects(lambda: repo.append_checkpoint("open-boundary", "current", unsafe), "incomplete_turn")
        assert not load_model_history("open-boundary", before_turn_id="current", database_path=db).turns[0].compressible

        c, _ = seed(db, "own-budget", [4500, 4500, 1400], current=3000)
        b = run(c)
        attempts = c.metadata["compaction"]["attempts"]
        assert attempts[0]["covered_through_seq"] == 4  # Entire next turn does not fit.
        assert all(a["input_budget"]["I"] < a["input_budget"]["B"] for a in attempts)
        assert c.metadata["compaction"]["summary_calls"] <= 2
        assert c.history.checkpoint["data"]["covered_through_seq"] + len(c.history.turns) * 4 == 12
        c, _ = seed(db, "too-big-old", [14000])
        run(c)
        assert c.metadata["compaction"]["summary_calls"] == 0
        assert c.metadata["compaction"]["stop_reason"] == "summary_input_budget_no_complete_prefix"
        c, _ = seed(db, "no-old", [], current=13000)
        b = run(c)
        assert b.hard_budget_exceeded and c.metadata["compaction"]["summary_calls"] == 0

        # Ended failures retain user goals and confirmed paired tools, never fake success.
        c, repo = seed(db, "failed-old", [], current=0)
        repo.append_event("failed-old", "tool/call", {"tool_call_id": "ok", "name": "echo", "arguments": {}}, turn_id="current")
        repo.append_event("failed-old", "tool/call", {"tool_call_id": "unknown", "name": "missing", "arguments": {}}, turn_id="current")
        repo.append_event("failed-old", "tool/result", {"tool_call_id": "ok", "name": "echo", "status": "success", "history_result": {"status": "success", "summary": "confirmed"}}, turn_id="current")
        repo.append_event("failed-old", "turn/end", {"status": "error"}, turn_id="current")
        h = load_model_history("failed-old", database_path=db)
        assert h.turns[0].status == "error"
        assert len([m for m in h.messages if m["role"] == "tool"]) == 1
        assert h.messages[1]["tool_calls"][0]["id"] == h.messages[2]["tool_call_id"] == "ok"
        assert "未确认" in json.dumps(h.messages, ensure_ascii=False) and "未完成用户目标" in h.messages[-1]["content"]

        # One successful call at step 1, then tool growth triggers the same turn's second.
        c, _ = seed(db, "cross-step", [3000, 1600, 1600], current=6000)
        responses = [AgentLLMResponse("", [AgentToolCall("grow", "echo", "{}")]), AgentLLMResponse("done", [])]
        with patch("app.agents.agent_loop.get_context_config", return_value=CONFIG), patch.object(llm_service, "get_llm_config", return_value=LLM):
            run_agent(c, tools={"echo": lambda: {"evidence": "actual" * 300}}, llm_call=lambda *_: responses.pop(0), summary_call=answer)
        assert [a["step"] for a in c.metadata["compaction"]["attempts"]] == [1, 2]
        assert c.metadata["compaction"]["summary_calls"] == 2
        assert c.messages[-2]["role"] == "tool" and "actual" * 300 in c.messages[-2]["content"]

        # No effective scope expansion: a failed prefix is not sent a second time.
        c, _ = seed(db, "no-expansion", [4500, 4500, 1400], current=3000)
        run(c, lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("fake")))
        assert c.metadata["compaction"]["summary_calls"] == 1
        assert c.metadata["compaction"]["stop_reason"] == "cannot_expand_summary_scope"
        for changes in ({"recent_ratio": 1}, {"target_tokens": 2048}, {"max_output_tokens": 0}):
            rejects(lambda changes=changes: replace(SUMMARY, **changes))

        # Normal Paper Agent entry ignores legacy JSON-style messages and restores SQLite.
        from app.agents.paper_agent import run_paper_agent, build_paper_agent_messages, PAPER_AGENT_TOOL_SPECS
        from app.tools.session_history_tools import SESSION_HISTORY_TOOL_SPEC
        from app.services.debug_trace_service import DebugTraceService
        c, repo = seed(db, "paper-entry", [15000, 10000], current=0)
        messages = build_paper_agent_messages("current:", "p1", history=c.history.messages, active_paper_ids=["p1", "p2"])
        initial = check_request(messages, [*PAPER_AGENT_TOOL_SPECS, SESSION_HISTORY_TOOL_SPEC], replace(CONFIG, window=100000), model=LLM.model)
        config = replace(CONFIG, window=int(initial.I / .85))
        with patch("app.agents.agent_loop.get_context_config", return_value=config), patch.object(llm_service, "get_llm_config", return_value=LLM):
            result = run_paper_agent("current:", session_id="paper-entry", turn_id="current", database_path=db, paper_id="p1", active_paper_ids=["p1", "p2"], history=[{"role": "user", "content": "legacy-truncated-must-not-return"}], llm_call=lambda *_: AgentLLMResponse("done", []), summary_call=answer, debug_trace=DebugTraceService("paper-entry", "current", database_path=db, enabled=False))
        assert result.context.metadata["compaction"]["revision"] >= 1
        assert "legacy-truncated-must-not-return" not in json.dumps(result.context.messages)
        assert "read_session_history" in result.context.metadata["paper_agent"]["available_tools"]
        repo.append_event("paper-entry", "assistant/message", {"content": "done"}, turn_id="current")
        repo.append_event("paper-entry", "turn/end", {"status": "success"}, turn_id="current")
        repo.append_event("paper-entry", "user/message", {"content": "new-question"}, turn_id="new")
        fresh = load_model_history("paper-entry", before_turn_id="new", database_path=db)
        assert fresh.checkpoint["seq"] == result.context.history.checkpoint["seq"]
        assert sum(m["content"] == "current:" for m in fresh.messages) == 1
        assert all(m["content"] != "new-question" for m in fresh.messages)

        # Real loop, current tools and origins survive; normal and SSE send identical inputs.
        seen = []
        for stream in (False, True):
            c, _ = seed(db, f"loop-{stream}", [3000, 1600, 1600], current=7800)
            outputs = [AgentLLMResponse("", [AgentToolCall("paired", "echo", "{}")], finish_reason="tool_calls"), AgentLLMResponse("done", [], finish_reason="stop")]
            sent = []
            def caller(messages, _tools):
                sent.append(deepcopy(messages))
                return outputs.pop(0)
            def streaming(messages, tools):
                response = caller(messages, tools)
                from app.services.llm_service import AgentToolCallDelta
                if response.tool_calls:
                    yield AgentLLMDelta(tool_calls=[AgentToolCallDelta(index=0, id="paired", name="echo", arguments_delta="{}")], finish_reason="tool_calls")
                else:
                    yield AgentLLMDelta(content="done", finish_reason="stop")
            with patch("app.agents.agent_loop.get_context_config", return_value=CONFIG), patch.object(llm_service, "get_llm_config", return_value=LLM):
                result = run_agent(c, tools={"echo": lambda: {"evidence": "actual"}}, llm_call=caller, llm_stream=streaming if stream else None, summary_call=answer)
            assert result.final_answer == "done" and c.step == 2
            assert c.metadata["compaction"]["summary_calls"] == 2
            assert sent[1][-1]["tool_call_id"] == sent[1][-2]["tool_calls"][0]["id"] == "paired"
            assert len(c.messages) == len(c.metadata["message_origins"])
            seen.append(sent)
        assert seen[0] == seen[1]
        c, _ = seed(db, "large-current", [], current=13000)
        with patch("app.agents.agent_loop.get_context_config", return_value=CONFIG), patch.object(llm_service, "get_llm_config", return_value=LLM):
            exc = rejects(lambda: run_agent(c, llm_call=lambda *_: (_ for _ in ()).throw(AssertionError("must not send"))))
            assert isinstance(exc, ContextBudgetError) and "当前轮次" in str(exc)

        # Summary transport has its own cap, no tools, no SSE deltas, and real metadata.
        bodies = []
        def transport(body, config):
            bodies.append(body)
            return {"content": "summary", "usage": {"total_tokens": 9}, "finish_reason": "stop"}
        real = replace(LLM, provider="openai_compatible", api_key="fake-local", base_url="http://unused.invalid")
        with patch.object(llm_service, "_request_chat_completion", side_effect=transport):
            response = llm_service.call_llm_summary([{"role": "user", "content": "small"}], config=real, budget_config=replace(CONFIG, max_output_tokens=2048))
        assert bodies[0]["max_tokens"] == 2048 and "tools" not in bodies[0] and "stream" not in bodies[0]
        assert response.usage == {"total_tokens": 9} and response.finish_reason == "stop" and response.elapsed_ms is not None
    print("ALL CONTEXT COMPACTION TESTS PASSED")


if __name__ == "__main__":
    main()
