"""Offline budget/LLM checks: python -m scripts.test_context_budget."""

import io
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import run_agent
from app.agents.paper_agent import build_paper_agent_messages
from app.core.config import ContextConfig, ContextConfigError, LLMConfig, get_context_config
from app.repositories.debug_trace_repository import DebugTraceRepository
from app.services import llm_service as llm
from app.services.context_service import ContextBudgetError, check_request, measure_input
from app.services.debug_trace_service import DebugTraceService


def raises(error_type, call):
    try:
        call()
    except error_type as error:
        return error
    raise AssertionError(f"Expected {error_type.__name__}")


def context():
    return AgentContext(messages=[{"role": "user", "content": "中文问题"}])


def test_measurement():
    messages = build_paper_agent_messages("当前用户", None, history=[
        {"role": "user", "content": "历史问题"},
        {"role": "assistant", "content": "历史回答"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "function": {"name": "read", "arguments": '{"路径":"中文.json"}'}}]},
        {"role": "tool", "name": "read", "tool_call_id": "c", "content": '{"结果":"中文正文"}'},
    ], system_context="注入的 memory")
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object", "properties": {"路径": {"type": "string"}}}}}]
    count, method = measure_input(messages, tools, model="unknown-model")
    assert method == "utf8_bytes:json_envelope"
    assert count > len(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False))
    for index in range(len(messages)):
        assert measure_input(messages[:index] + messages[index + 1:], tools, model="unknown-model")[0] < count
    assert measure_input(messages, [], model="unknown-model")[0] < count
    for field in ("arguments", "name"):
        changed = json.loads(json.dumps(messages))
        changed[-3]["tool_calls"][0]["function"][field] = ""
        assert measure_input(changed, tools, model="unknown-model")[0] < count
    fake_encoding = SimpleNamespace(name="matched", encode=lambda text, **kwargs: list(text))
    with patch.dict("sys.modules", {"tiktoken": SimpleNamespace(encoding_for_model=lambda model: fake_encoding)}):
        assert measure_input(messages, tools, model="matched-model")[1] == "tiktoken:matched:json_envelope"
    budget = check_request(messages, tools, ContextConfig(10000, 3000, 100))
    assert budget.B == budget.T == 6900 < 0.8 * budget.W
    assert budget.G == 5520 < budget.T and budget.measurement_kind == "estimated"


def test_boundaries_and_loop():
    count = measure_input(context().messages, [], model="")[0]
    for streaming in (False, True):
        for offset in (-1, 0, 1):
            config = ContextConfig(count + 2 + offset, 1, 1)
            calls = []
            def fake(messages, tools):
                calls.append(messages)
                return iter([llm.AgentLLMDelta("ok", finish_reason="stop")]) if streaming else llm.AgentLLMResponse("ok", [], finish_reason="stop")
            state = context()
            events = []
            with patch("app.agents.agent_loop.get_context_config", return_value=config):
                run = lambda: run_agent(state, event_sink=events.append, **{("llm_stream" if streaming else "llm_call"): fake})
                if offset <= 0:
                    raises(ContextBudgetError, run)
                    assert not calls and events[-2]["code"] == "context_budget_exceeded"
                    assert len(state.messages) == 1
                else:
                    assert run().llm_response.finish_reason == "stop" and len(calls) == 1
                    assert state.metadata["context_budgets"][0]["trigger_reached"]

        calls = []
        config = ContextConfig(2000, 100, 10)
        def growing(messages, tools):
            calls.append(1)
            if streaming:
                return iter([llm.AgentLLMDelta(tool_calls=[llm.AgentToolCallDelta(0, "c", "grow", "{}")], finish_reason="tool_calls")])
            return llm.AgentLLMResponse("", [llm.AgentToolCall("c", "grow", "{}")], finish_reason="tool_calls")
        state = context()
        with patch("app.agents.agent_loop.get_context_config", return_value=config):
            raises(ContextBudgetError, lambda: run_agent(state, tools={"grow": lambda: "中" * 1000}, **{("llm_stream" if streaming else "llm_call"): growing}))
        assert len(calls) == 1 and len(state.metadata["context_budgets"]) == 2
        assert not state.metadata["context_budgets"][0]["trigger_reached"]
        assert state.metadata["context_budgets"][1]["hard_budget_exceeded"]
        assert state.messages[-1]["role"] == "tool" and "中" * 1000 in state.messages[-1]["content"]

    limited = check_request(context().messages, [], ContextConfig(10000, 100, 10, input_limit=count + 9))
    assert not limited.hard_budget_exceeded and limited.input_limit_exceeded
    raises(ContextBudgetError, limited.require_sendable)
    check_request(context().messages, [], ContextConfig(10000, 100, 10, input_limit=count + 10)).require_sendable()


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()

    def __iter__(self):
        return iter([*(f"data: {json.dumps(item)}\n".encode() for item in self.payload), b"data: [DONE]\n"])


def test_provider_and_trace():
    usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15, "prompt_tokens_details": {"cached_tokens": 2}}
    for provider, parameter in (("deepseek", "max_tokens"), ("openai_compatible", "max_completion_tokens")):
        config = LLMConfig(provider=provider, model="fake", api_key="fake", base_url="https://fake.invalid")
        for streaming in (False, True):
            for reason in ("stop", "length", "tool_calls"):
                captured = []
                call = {"id": "c", "type": "function", "function": {"name": "act", "arguments": "{}"}}
                message = {"content": "文本", "tool_calls": [call] if reason in {"tool_calls", "length"} else []}
                payload = [
                    {"choices": [{"delta": message}]},
                    {"choices": []},
                    {"choices": [{"delta": {}, "finish_reason": reason}]},
                    {"choices": [], "usage": usage},
                ] if streaming else {"choices": [{"message": message, "finish_reason": reason}], "usage": usage}
                def transport(request, **kwargs):
                    captured.append(json.loads(request.data))
                    return FakeHTTP(payload)
                with tempfile.TemporaryDirectory() as directory, patch.object(llm, "urlopen", side_effect=transport), patch.object(llm, "get_llm_config", return_value=config), patch("app.agents.agent_loop.get_context_config", return_value=ContextConfig(10000, 123, 10, output_token_parameter=parameter)):
                    trace = DebugTraceService("s", "t", database_path=Path(directory) / "trace.db", enabled=True)
                    state = context()
                    executed = []
                    options = {"llm_stream": llm.call_llm_with_tools_stream} if streaming else {"llm_call": llm.call_llm_with_tools}
                    if reason == "tool_calls":
                        # Inspect the provider response without starting an unbounded fake Tool loop.
                        if streaming:
                            from app.agents.agent_loop import _merge_stream_tool_calls
                            response, _ = _merge_stream_tool_calls(llm.call_llm_with_tools_stream(state.messages, [], max_output_tokens=123, output_token_parameter=parameter), lambda text: None)
                        else:
                            response = llm.call_llm_with_tools(state.messages, [], max_output_tokens=123, output_token_parameter=parameter)
                        assert response.finish_reason == "tool_calls" and response.tool_calls
                        assert response.usage == usage
                    else:
                        run = lambda: run_agent(state, debug_trace=trace, tools={"act": lambda: executed.append(1)}, **options)
                        if reason == "length":
                            assert raises(llm.LLMOutputTruncatedError, run).response.usage == usage
                            assert not executed and len(state.messages) == 1
                        else:
                            assert run().llm_response.usage == usage
                        records = DebugTraceRepository(Path(directory) / "trace.db").list_by_turn("s", "t")
                        budget = next(row["data"] for row in records if row["event_type"] == "llm/context_budget")
                        output = next(row["data"] for row in records if row["event_type"] == "llm/output")
                        assert set("WIOSBTG") <= budget.keys() and budget["O"] == 123
                        assert output["usage"] == usage and output["finish_reason"] == reason
                assert len(captured) == 1 and captured[0][parameter] == 123
                if streaming:
                    assert captured[0]["stream_options"] == {"include_usage": True}

        for streaming in (False, True):
            for status, code, message, overflow in [
                (400, "context_length_exceeded", "capacity", True),
                (400, None, "This model's maximum context length is 100 tokens. You requested 110 tokens.", True),
                (400, "invalid_request_error", "bad schema", False),
                (401, "context_length_exceeded", "bad key", False),
                (403, "authentication_error", "denied", False),
                (429, "rate_limit", "slow down", False),
            ]:
                body = json.dumps({"error": {"code": code, "type": "invalid_request_error", "param": "messages", "message": message}})
                with patch.object(llm, "urlopen", side_effect=HTTPError("https://fake.invalid", status, "fake", {}, io.BytesIO(body.encode()))):
                    error = raises(llm.LLMProviderError, lambda: list(llm._stream_chat_completion({}, config)) if streaming else llm._request_chat_completion({}, config))
                assert error.status_code == status and error.error_code == code and error.param == "messages"
                assert error.is_context_overflow is overflow and error.body == body
            with patch.object(llm, "urlopen", side_effect=URLError("offline")):
                error = raises(RuntimeError, lambda: list(llm._stream_chat_completion({}, config)) if streaming else llm._request_chat_completion({}, config))
                assert not isinstance(error, llm.LLMProviderError)
        with patch.object(llm, "urlopen", return_value=FakeHTTP([{"error": {"code": "context_length_exceeded"}}])):
            error = raises(llm.LLMProviderError, lambda: list(llm._stream_chat_completion({}, config)))
            assert error.is_context_overflow and error.status_code is None
        with patch.object(llm, "urlopen", return_value=FakeHTTP([{"usage": usage}, {"choices": [{"finish_reason": "stop"}]}])):
            deltas = list(llm._stream_chat_completion({}, config))
            assert deltas[0].usage == usage and deltas[1].finish_reason == "stop"

    # Graph text/JSON paths keep their string/dict contracts and omit Agent output caps.
    captured = []
    def graph_transport(request, **kwargs):
        captured.append(json.loads(request.data))
        return FakeHTTP({"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}], "usage": usage})
    with patch.object(llm, "get_llm_config", return_value=config), patch.object(llm, "urlopen", side_effect=graph_transport):
        assert llm.call_llm_text("system", "user") == '{"ok": true}'
        assert llm.call_llm_json("system", "user") == {"ok": True}
        llm.call_llm_with_tools([], [])  # Existing two-argument callers remain valid.
    assert all("max_tokens" not in body and "max_completion_tokens" not in body for body in captured)


def test_config():
    for args in ((0, 10), (100, 0), (100, 90, 10), (100, 10, 0), (True, 10), (100.5, 10)):
        raises(ContextConfigError, lambda: ContextConfig(*args))
    for kwargs in ({"trigger_ratio": 1}, {"target_ratio": 0.9}, {"target_ratio": float("nan")}, {"target_budget_ratio": 1}, {"output_limit": 9}, {"input_limit": 10}, {"output_token_parameter": "unknown"}):
        raises(ContextConfigError, lambda: ContextConfig(100, 10, 10, **kwargs))
    with patch.dict(os.environ, {}, clear=True):
        assert get_context_config(LLMConfig()).window == 65536
        raises(ContextConfigError, lambda: get_context_config(LLMConfig(provider="unsupported")))
        raises(ContextConfigError, lambda: get_context_config(LLMConfig(provider="deepseek", model="unknown")))
        os.environ.update(AGENT_CONTEXT_WINDOW="10000", AGENT_MAX_OUTPUT_TOKENS="100")
        assert get_context_config(LLMConfig(provider="deepseek", model="explicit-custom-model")).window == 10000
        os.environ["AGENT_CONTEXT_WINDOW"] = "invalid"
        raises(ContextConfigError, lambda: get_context_config(LLMConfig()))


def test_chat_errors():
    from app.api.routes import chat
    from app.services import session_service, sqlite_service
    with tempfile.TemporaryDirectory() as directory, patch.object(sqlite_service, "DATABASE_PATH", Path(directory) / "chat.db"), patch.object(session_service, "CHAT_SESSIONS_DIR", Path(directory)), patch.object(chat, "SESSION_HISTORY", {}), patch.object(chat, "_load_memory_context", return_value="local memory"):
        for invalid in (False, True):
            config_patch = patch("app.agents.agent_loop.get_context_config", side_effect=ContextConfigError("Missing AGENT_CONTEXT_WINDOW")) if invalid else patch("app.agents.agent_loop.get_context_config", return_value=ContextConfig(100, 20, 10))
            code = "context_config_invalid" if invalid else "context_budget_exceeded"
            with config_patch:
                error = raises(chat.HTTPException, lambda: chat.chat(chat.ChatRequest(session_id=f"normal-{invalid}", message="问题")))
                assert error.status_code == (500 if invalid else 413) and error.detail["code"] == code
                events = [json.loads(chunk.split("data: ", 1)[1]) for chunk in chat._stream_agent_events(session_id=f"stream-{invalid}", message="问题", session=session_service.ChatSessionState(), paper_id=None)]
                assert next(event for event in events if event["type"] == "error")["code"] == code
                assert events[-1] == {"type": "agent_done", "status": "error"}
                assert not any(event["type"] == "llm_start" for event in events)
                assert not list(Path(directory).glob("*.json"))


def main():
    with patch.dict(os.environ, {"LLM_PROVIDER": "mock", "AGENT_CONTEXT_WINDOW": "1000000", "AGENT_MAX_OUTPUT_TOKENS": "4096"}):
        # No network is allowed; individual transport tests replace this with fake HTTP.
        with patch.object(llm, "urlopen", side_effect=AssertionError("Unexpected network call")), patch.dict("sys.modules", {"tiktoken": None}):
            test_measurement()
            test_boundaries_and_loop()
            test_provider_and_trace()
            test_config()
            test_chat_errors()
    print("ALL CONTEXT BUDGET AND LLM FOUNDATION TESTS PASSED")


if __name__ == "__main__":
    main()
