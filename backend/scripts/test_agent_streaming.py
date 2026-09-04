"""Pure-local checks for Agent runtime events and SSE chat streaming."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import AgentMaxStepsError, run_agent
from app.agents.paper_agent import run_paper_agent
from app.api.routes import chat
from app.services import llm_service, session_service
from app.services.llm_service import AgentLLMDelta, AgentLLMResponse, AgentToolCall, AgentToolCallDelta


def _context(max_steps: int = 8) -> AgentContext:
    return AgentContext(messages=[{"role": "user", "content": "hello"}], max_steps=max_steps)


def _stream_batches(batches: list[list[AgentLLMDelta]]):
    def call(*_args):
        return iter(batches.pop(0))

    return call


def _types(result) -> list[str]:
    return [event["type"] for event in result.events]


def _sse_events(chunks):
    return [json.loads(chunk.split("data: ", 1)[1]) for chunk in chunks]


def main() -> None:
    original_urlopen = llm_service.urlopen
    original_config = llm_service.get_llm_config

    class FakeSseResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"hello "}}]}\n',
                    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call","function":{"name":"echo","arguments":"{\\"text\\":\\""}}]}}]}\n',
                    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"world\\"}"}}]}}]}\n',
                    b'data: [DONE]\n',
                ]
            )

    try:
        llm_service.urlopen = lambda *_args, **_kwargs: FakeSseResponse()
        llm_service.get_llm_config = lambda: SimpleNamespace(
            provider="deepseek", api_key="test", base_url="http://local", model="test", timeout=1, temperature=0.2
        )
        provider_deltas = list(llm_service.call_llm_with_tools_stream([], []))
        assert provider_deltas[0].content == "hello "
        assert provider_deltas[1].tool_calls[0].arguments_delta == '{"text":"'
        assert provider_deltas[2].tool_calls[0].arguments_delta == 'world"}'
    finally:
        llm_service.urlopen = original_urlopen
        llm_service.get_llm_config = original_config

    direct = run_agent(
        _context(),
        llm_stream=_stream_batches([[AgentLLMDelta("hello "), AgentLLMDelta("world")]]),
    )
    assert direct.final_answer == "hello world"
    assert _types(direct) == [
        "user_message", "agent_start", "step_start", "llm_start", "llm_delta", "llm_delta", "llm_message", "assistant_trace", "final_start", "final_delta", "final_delta", "final_end", "step_end", "agent_done",
    ]
    assert direct.events[0] == {"type": "user_message", "content": "hello"}
    assert next(event for event in direct.events if event["type"] == "llm_message")["content"] == "hello world"
    assert next(event for event in direct.events if event["type"] == "assistant_trace") == {
        "type": "assistant_trace", "step": 1, "content": "hello world"
    }

    tool_final = run_agent(
        _context(),
        tools={"echo": lambda text: {"text": text}},
        llm_stream=_stream_batches(
            [
                [AgentLLMDelta("我需要调用工具。"), AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "echo-1", "echo", '{"text":"hello')]), AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, arguments_delta='"}')])],
                [AgentLLMDelta("tool final")],
            ]
        ),
    )
    assert tool_final.final_answer == "tool final"
    assert all(name in _types(tool_final) for name in ("tool_call", "tool_start", "tool_result"))
    assert next(event for event in tool_final.events if event["type"] == "tool_call")["arguments"] == {"text": "hello"}
    assert next(event for event in tool_final.events if event["type"] == "llm_message")["content"] == "我需要调用工具。"
    assert [event["type"] for event in tool_final.events].index("assistant_trace") < [
        event["type"] for event in tool_final.events
    ].index("tool_call")

    multi = run_agent(
        _context(),
        tools={"echo": lambda text: text},
        llm_stream=_stream_batches(
            [
                [AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "one", "echo", '{"text":"one"}'), AgentToolCallDelta(1, "two", "echo", '{"text":"two"}')])],
                [AgentLLMDelta("done")],
            ]
        ),
    )
    assert [event["tool_call_id"] for event in multi.events if event["type"] == "tool_start"] == ["one", "two"]
    assert not any(event["type"] == "assistant_trace" and event["step"] == 1 for event in multi.events)

    tool_error = run_agent(
        _context(),
        tools={"broken": lambda: (_ for _ in ()).throw(RuntimeError("hidden"))},
        llm_stream=_stream_batches(
            [[AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "broken", "broken", "{}")])], [AgentLLMDelta("recovered")]]
        ),
    )
    assert any(event["type"] == "tool_result" and event["status"] == "error" for event in tool_error.events)
    assert tool_error.final_answer == "recovered"

    invalid = run_agent(
        _context(),
        llm_stream=_stream_batches(
            [[AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "invalid", "missing", "not-json")])], [AgentLLMDelta("recovered")]]
        ),
    )
    unknown = run_agent(
        _context(),
        llm_stream=_stream_batches(
            [[AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "unknown", "missing", "{}")])], [AgentLLMDelta("recovered")]]
        ),
    )
    assert any(event["type"] == "tool_result" and event["status"] == "error" for event in invalid.events)
    assert any(event["type"] == "tool_result" and event["status"] == "error" for event in unknown.events)
    assert next(event for event in invalid.events if event["type"] == "tool_call")["arguments"] == {"raw": "not-json"}

    max_events: list[dict[str, object]] = []
    try:
        run_agent(
            _context(max_steps=1),
            tools={"echo": lambda: "ok"},
            llm_stream=_stream_batches([[AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "loop", "echo", "{}")])]]),
            event_sink=max_events.append,
        )
    except AgentMaxStepsError:
        assert any(event["type"] == "error" and event["code"] == "agent_max_steps" for event in max_events)
        assert max_events[-1] == {"type": "agent_done", "status": "error"}
    else:
        raise AssertionError("Expected AgentMaxStepsError")

    received: list[str] = []
    fragmented = run_agent(
        _context(),
        tools={"search": lambda query: received.append(query) or {"chunks": []}},
        llm_stream=_stream_batches(
            [
                [
                    AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, "search", "search", '{"query":"')]),
                    AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, arguments_delta="method")]),
                    AgentLLMDelta(tool_calls=[AgentToolCallDelta(0, arguments_delta=' section"}')]),
                ],
                [AgentLLMDelta("found")],
            ]
        ),
    )
    assert received == ["method section"] and fragmented.final_answer == "found"

    visible_responses = [
        AgentLLMResponse("我需要先查询论文信息。", [AgentToolCall("visible", "echo", "{}")]),
        AgentLLMResponse("已获得结果。", []),
    ]
    visible_tool_response = run_agent(
        _context(),
        tools={"echo": lambda: "ok"},
        llm_call=lambda *_args: visible_responses.pop(0),
    )
    assert next(event for event in visible_tool_response.events if event["type"] == "llm_message") == {
        "type": "llm_message", "step": 1, "content": "我需要先查询论文信息。"
    }
    original_runner = chat.run_paper_agent
    original_session_dir = session_service.CHAT_SESSIONS_DIR
    with tempfile.TemporaryDirectory() as directory:
        chat.SESSION_HISTORY.clear()
        session_service.CHAT_SESSIONS_DIR = Path(directory)

        def fake_runner(message, **kwargs):
            kwargs["llm_stream"] = _stream_batches([[AgentLLMDelta("streamed reply")]])
            return run_paper_agent(message, **kwargs)

        chat.run_paper_agent = fake_runner
        session = session_service.ChatSessionState(paper_id="paper-1", active_paper_ids=["paper-1"])
        events = _sse_events(
            chat._stream_agent_events(session_id="stream-session", message="question", session=session, paper_id="paper-1")
        )
        loaded = session_service.load_session("stream-session")
        assert events[-1]["type"] == "agent_done"
        assert any(event == {"type": "assistant_trace", "step": 1, "content": "streamed reply"} for event in events)
        assert loaded is not None and loaded.messages == [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "streamed reply"},
        ]
        assert all("tool" not in item for item in loaded.messages)

        cleanup = chat._stream_agent_events(
            session_id="stream-cleanup",
            message="question",
            session=session_service.ChatSessionState(),
            paper_id=None,
        )
        next(cleanup)
        cleanup.close()

    chat.run_paper_agent = original_runner
    session_service.CHAT_SESSIONS_DIR = original_session_dir
    print("ALL AGENT STREAMING TESTS PASSED")


if __name__ == "__main__":
    main()
