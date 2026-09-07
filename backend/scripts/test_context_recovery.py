"""Offline acceptance for one overflow retry, shared quota, streaming, and Inspector."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from app.agents.agent_loop import run_agent
from app.core.config import get_harness_debug_trace_config
from app.repositories.debug_trace_repository import DebugTraceRepository
from app.services import context_compaction_service as compaction, llm_service
from app.services.context_service import check_request
from app.services.debug_trace_service import DebugTraceService
from app.services.llm_service import AgentLLMResponse, AgentLLMDelta, AgentToolCall, AgentToolCallDelta, LLMProviderError, LLMStreamInterruptedError
from app.services.session_event_service import SessionEventService
from app.services.session_model_history_service import load_model_history
from app.services.tool_result_service import shrink_readable_result
from app.services.turn_trace_service import TurnTraceService
from scripts.test_context_compaction import seed, answer, rejects, CONFIG, SUMMARY, LLM


def overflow(status=400, code="context_length_exceeded"):
    return LLMProviderError("openai_compatible", status, json.dumps({"error": {"code": code, "message": "DO-NOT-EXPOSE-RAW-SECRET"}}))


def execute(context, caller, *, stream=False, summary=answer, config=CONFIG, tools=None, debug=None, sink=None):
    with patch("app.agents.agent_loop.get_context_config", return_value=config), patch.object(llm_service, "get_llm_config", return_value=LLM):
        return run_agent(context, tools=tools, llm_call=caller, llm_stream=caller if stream else None, summary_call=summary, debug_trace=debug, event_sink=sink)


def main():
    with tempfile.TemporaryDirectory() as directory, patch.object(compaction, "get_compaction_config", return_value=SUMMARY):
        db = Path(directory) / "acceptance.db"
        normal_inputs = []
        for stream in (False, True):
            c, repo = seed(db, f"recover-{stream}", [3000, 1400])
            assert not check_request(c.messages, [], CONFIG, model=LLM.model).trigger_reached
            original = deepcopy(repo.list_events(c.session_id))
            sent, events = [], []
            def call(messages, tools):
                sent.append(deepcopy(messages))
                if len(sent) == 1:
                    raise overflow(None if stream else 400)
                return AgentLLMResponse("main answer", [], usage={"total_tokens": 99}, finish_reason="stop")
            def streaming(messages, tools):
                if not sent:
                    yield AgentLLMDelta(usage={"prompt_tokens": 21})  # Usage-only is not public output.
                response = call(messages, tools)
                yield AgentLLMDelta(content=response.content)
                yield AgentLLMDelta(usage=response.usage, finish_reason="stop")
            debug = DebugTraceService(c.session_id, "current", database_path=db, enabled=True)
            debug.start()
            persistence = SessionEventService(c.session_id, database_path=db)
            persistence.turn_id, persistence._started = "current", True
            def sink(event):
                events.append(event)
                persistence.persist_runtime_event(event)
            result = execute(c, streaming if stream else call, stream=stream, debug=debug, sink=sink)
            debug.finish("success")
            assert result.final_answer == "main answer" and c.step == 1
            assert c.metadata["context_recovery"]["retries"] == 1 and len(sent) == 2
            assert c.metadata["compaction"]["summary_calls"] == 1
            assert check_request(sent[1], [], CONFIG, model=LLM.model).I < check_request(sent[0], [], CONFIG, model=LLM.model).I
            assert repo.list_events(c.session_id)[:len(original)] == original
            assert sum(m["content"].startswith("current:") for m in sent[1]) == 1
            assert all("DO-NOT-EXPOSE" not in json.dumps(event) for event in events)
            assert "".join(e["delta"] for e in events if e["type"] == "final_delta") == "main answer"
            assert [e["phase"] for e in events if e["type"] == "context_status"] == ["summarizing", "summarized", "retrying", "retried"]
            assert load_model_history(c.session_id, before_turn_id="current", database_path=db).checkpoint["seq"] == c.history.checkpoint["seq"]
            with patch("app.services.turn_trace_service.get_harness_debug_trace_config", return_value=replace(get_harness_debug_trace_config(), enabled=True)):
                trace = TurnTraceService(database_path=db).get_turn_trace(c.session_id, "current")
            calls = trace["steps"][0]["llm_calls"]
            assert len(calls) == 2 and calls[0]["output"] is None and calls[0]["error"]["code"] == "provider_context_exceeded"
            assert calls[1]["output"]["usage"] == {"total_tokens": 99} and not calls[1].get("error")
            if stream:
                assert calls[0]["error"]["usage"] == {"prompt_tokens": 21}
            context_events = trace["steps"][0]["context_events"]
            summaries = [e["data"] for e in context_events if e["type"] == "context/compaction" and "number" in e["data"]]
            assert len(summaries) == 1 and summaries[0]["usage"] == {"total_tokens": 80}
            assert summaries[0]["trigger_reason"] == "provider_context_exceeded"
            assert trace["checkpoints"][0]["covered_through_seq"] == 8
            assert "summary" not in trace["checkpoints"][0]
            assert "DO-NOT-EXPOSE" not in json.dumps(trace)
            assert trace["final_answer"] == "main answer"
            normal_inputs.append(sent)
        assert normal_inputs[0] == normal_inputs[1]

        # An earlier tool-planning output is not a final answer when the later request fails.
        failed_trace = TurnTraceService._project("s", "t", [
            {"event_type": "user/message", "data": {"content": "question"}, "created_at": "now", "step": None},
            {"event_type": "turn/end", "data": {"status": "error"}, "created_at": "now", "step": None}],
            [{"event_type": "llm/output", "data": {"content": "I will inspect the paper", "tool_calls": [{"name": "read"}]}, "created_at": "now", "step": 1, "trace_seq": 1}])
        assert failed_trace["final_answer"] is None and failed_trace["status"] == "error"

        # Classification excludes authentication, throttling, generic HTTP 400, and network errors.
        for index, error in enumerate([overflow(401), overflow(429), overflow(400, "invalid_request"), RuntimeError("network failed")]):
            c, _ = seed(db, f"ordinary-{index}", [3000])
            calls = []
            def fail(*_):
                calls.append(1)
                raise error
            caught = rejects(lambda: execute(c, fail))
            assert caught is error and len(calls) == 1
            assert c.metadata["compaction"]["summary_calls"] == 0 and c.metadata["context_recovery"]["retries"] == 0

        # No old history, no changed evidence => no identical retry.
        c, _ = seed(db, "empty", [])
        count = []
        def fail(*_):
            count.append(1)
            raise overflow()
        rejects(lambda: execute(c, fail))
        assert len(count) == 1 and c.metadata["context_recovery"]["retries"] == 0
        assert c.metadata["context_recovery"]["events"][-1]["reason"] == "no_input_reduction"

        # Automatic threshold compaction and recovery share the same two calls.
        c, _ = seed(db, "shared", [3000, 1600, 1600], current=6000)
        count = []
        def retry_call(*_):
            count.append(1)
            if len(count) == 1:
                raise overflow()
            return AgentLLMResponse("done", [], finish_reason="stop")
        execute(c, retry_call)
        assert len(count) == 2 and c.metadata["compaction"]["summary_calls"] == 2
        assert [a["trigger_reason"] for a in c.metadata["compaction"]["attempts"]] == ["budget_trigger", "provider_context_exceeded"]
        c, _ = seed(db, "exhausted", [3000, 1600, 1600], current=7800)
        count.clear()
        rejects(lambda: execute(c, retry_call))
        assert len(count) == 1 and c.metadata["compaction"]["summary_calls"] == 2
        assert c.metadata["context_recovery"]["retries"] == 0

        for name, summary in [("invalid", lambda *_a, **_kw: AgentLLMResponse("bad", [], finish_reason="stop")), ("timeout", lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("fake")))]:
            c, _ = seed(db, name, [3000, 1400])
            count.clear()
            rejects(lambda: execute(c, fail, summary=summary))
            assert len(count) == 1 and 0 < c.metadata["compaction"]["summary_calls"] <= 2
            assert c.metadata["context_recovery"]["retries"] == 0
        c, _ = seed(db, "save-failed", [3000])
        from app.repositories.session_event_repository import SessionEventRepository
        count.clear()
        with patch.object(SessionEventRepository, "append_checkpoint", side_effect=OSError("disk full")):
            rejects(lambda: execute(c, fail))
        assert len(count) == 1 and c.history.checkpoint is None

        # A second main failure never recurses, even with summary quota remaining.
        c, _ = seed(db, "twice", [3000, 1400])
        count.clear()
        rejects(lambda: execute(c, fail))
        assert len(count) == 2 and c.metadata["context_recovery"]["retries"] == 1
        assert c.metadata["compaction"]["summary_calls"] == 1
        assert c.metadata["context_recovery"]["events"][-1]["reason"] == "retry_failed"

        # Partial content/tool fragments prohibit recovery and cannot execute partial tools.
        for kind in ("content", "tools", "network"):
            c, _ = seed(db, "partial-" + kind, [3000])
            count, executed, events = [], [], []
            def partial(*_):
                count.append(1)
                yield AgentLLMDelta(content="partial" if kind != "tools" else "", tool_calls=[AgentToolCallDelta(index=0, id="partial", name="write", arguments_delta='{"value":')] if kind == "tools" else None)
                raise RuntimeError("network") if kind == "network" else overflow(None)
            error = rejects(lambda: execute(c, partial, stream=True, tools={"write": lambda **_: executed.append(1)}, sink=events.append))
            assert isinstance(error, LLMStreamInterruptedError) and "未自动重放" in str(error)
            assert len(count) == 1 and not executed
            assert not any(e["type"] in {"final_delta", "tool_call"} for e in events)
            assert c.metadata["compaction"]["summary_calls"] == 0

        # Versioned multi-paper evidence can shrink without another summary; tools run once.
        refs = [{"paper_id": f"p{i}", "parser": "grobid", "cache_version": "sha256:" + str(i) * 64, "chunk_id": "c_" + str(i) * 64} for i in (1, 2)]
        payload = {"success": True, "status": "partial", "warning": "one old warning", "papers": [{"paper_id": ref["paper_id"], "success": True, "chunks": [{**ref, "text": "evidence" * 240, "offset": 30}]} for ref in refs]}
        raw = json.dumps(payload, ensure_ascii=False)
        projected = json.loads(shrink_readable_result("get_multi_paper_context", raw))
        assert projected["warning"] == payload["warning"] and projected["status"] == "partial"
        for paper, ref in zip(projected["papers"], refs):
            chunk = paper["chunks"][0]
            assert all(chunk[key] == value for key, value in ref.items()) and len(chunk["text"]) == 256
            assert chunk["next_offset"] == 286 and paper["coverage"] == "partial"
        assert json.dumps(payload, ensure_ascii=False) == raw
        assert shrink_readable_result("save_research_memory", raw) == raw
        for stream in (False, True):
            c, _ = seed(db, f"tools-once-{stream}", [])
            c.metadata["compaction"] = {"summary_calls": 2, "revision": 0, "attempts": [], "checkpoint_seqs": []}
            calls, executed = [], []
            def call(messages, _tools):
                calls.append(deepcopy(messages))
                if len(calls) == 1:
                    return AgentLLMResponse("", [AgentToolCall("read", "get_multi_paper_context", "{}"), AgentToolCall("write", "save_research_memory", "{}")])
                if len(calls) == 2:
                    raise overflow()
                return AgentLLMResponse("done", [], finish_reason="stop")
            def stream_call(messages, tools):
                response = call(messages, tools)
                if response.tool_calls:
                    yield AgentLLMDelta(tool_calls=[AgentToolCallDelta(index=i, id=t.id, name=t.name, arguments_delta="{}") for i, t in enumerate(response.tool_calls)], finish_reason="tool_calls")
                else:
                    yield AgentLLMDelta(content=response.content, finish_reason="stop")
            result = execute(c, stream_call if stream else call, stream=stream, config=replace(CONFIG, window=30000), tools={"get_multi_paper_context": lambda: (executed.append("read"), deepcopy(payload))[1], "save_research_memory": lambda: (executed.append("write"), {"saved": True})[1]})
            assert executed == ["read", "write"] and len(calls) == 3 and c.step == 2
            assert c.metadata["compaction"]["summary_calls"] == 2 and result.final_answer == "done"
            assert len(calls[2][-2]["content"]) < len(calls[1][-2]["content"])
            assert calls[2][-1] == calls[1][-1] and calls[2][-2]["tool_call_id"] == "read"

        # Repeated turns extend one checkpoint chain and recover the same model history.
        from app.agents.agent_context import AgentContext
        c, repo = seed(db, "chain", [3000])
        previous = None
        for index in range(3):
            if index:
                turn_id = f"next-{index}"
                question = f"question-{index}:" + "z" * 1600
                repo.append_event("chain", "user/message", {"content": question}, turn_id=turn_id)
                repo.append_event("chain", "turn/start", {}, turn_id=turn_id)
                history = load_model_history("chain", before_turn_id=turn_id, database_path=db)
                c = AgentContext(session_id="chain", turn_id=turn_id, database_path=db, history=history, history_end=1 + len(history.messages),
                    messages=[{"role": "system", "content": "fixed"}, *history.messages, {"role": "user", "content": question}],
                    metadata={"message_origins": [{"origin": "system_prompt"}, *history.origins, {"origin": "current_user"}], "paper_agent": {"paper_id": "p1", "active_paper_ids": ["p1", "p2"]}})
            question = c.messages[-1]["content"]
            count = []
            def chain_summary(request, **kwargs):
                from app.services.session_model_history_service import SUMMARY_SECTIONS
                sections = dict.fromkeys(SUMMARY_SECTIONS, "无")
                sections["关键结论及证据引用"] = "历史目标 [R1]"
                return AgentLLMResponse(json.dumps({"sections": sections, "source_refs": ["R1"]}, ensure_ascii=False), [], finish_reason="stop")
            execute(c, retry_call, summary=chain_summary, debug=DebugTraceService("chain", c.turn_id, database_path=db, enabled=False))
            cp = c.history.checkpoint
            assert cp["data"]["previous_checkpoint_seq"] == previous
            previous = cp["seq"]
            fresh = load_model_history("chain", before_turn_id=c.turn_id, database_path=db)
            assert fresh.messages == c.history.messages
            assert sum(m["content"] == question for m in c.messages) == 1
            repo.append_event("chain", "assistant/message", {"content": "done"}, turn_id=c.turn_id)
            repo.append_event("chain", "turn/end", {"status": "success"}, turn_id=c.turn_id)

        # Real SSE parser: an EOF with public content but no ending marker is interrupted.
        class LocalStream:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def __iter__(self):
                return iter([b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'])
        real = replace(LLM, provider="openai_compatible", api_key="fake", base_url="http://unused.invalid")
        c, _ = seed(db, "eof", [3000])
        with patch.object(llm_service, "urlopen", return_value=LocalStream()):
            interrupted = rejects(lambda: execute(c, lambda *_: llm_service._stream_chat_completion({"messages": [], "model": "offline"}, real), stream=True))
        assert isinstance(interrupted, LLMStreamInterruptedError)
        assert c.metadata["context_recovery"]["retries"] == 0

        # One retry per turn, not per step.
        c, _ = seed(db, "cross-step-retry", [3000])
        count, executed = [], []
        def across(*_):
            count.append(1)
            if len(count) != 2:
                raise overflow()
            return AgentLLMResponse("", [AgentToolCall("write", "write", "{}")])
        rejects(lambda: execute(c, across, tools={"write": lambda: executed.append(1)}))
        assert len(count) == 3 and executed == [1] and c.metadata["context_recovery"]["retries"] == 1
    print("ALL CONTEXT RECOVERY AND INSPECTOR ACCEPTANCE TESTS PASSED")


if __name__ == "__main__":
    main()
