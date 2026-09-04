"""Pure-local checks for the standalone agent loop."""

from typing import Any

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import AgentMaxStepsError, run_agent
from app.services.llm_service import AgentLLMResponse, AgentToolCall


def _sequence_llm(responses: list[AgentLLMResponse]):
    calls: list[list[dict[str, Any]]] = []

    def call(messages: list[dict[str, Any]], _: list[dict[str, Any]]) -> AgentLLMResponse:
        calls.append(list(messages))
        return responses[len(calls) - 1]

    return call, calls


def _context(max_steps: int = 8) -> AgentContext:
    return AgentContext(messages=[{"role": "user", "content": "hello"}], max_steps=max_steps)


def main() -> None:
    direct_llm, direct_calls = _sequence_llm([AgentLLMResponse("direct answer", [])])
    direct = run_agent(_context(), llm_call=direct_llm)
    assert direct.final_answer == "direct answer" and len(direct_calls) == 1
    assert next(event for event in direct.events if event["type"] == "assistant_trace") == {
        "type": "assistant_trace", "step": 1, "content": "direct answer"
    }

    tool_llm, tool_calls = _sequence_llm(
        [
            AgentLLMResponse("准备调用工具", [AgentToolCall("call_1", "echo", '{"text":"hello"}')]),
            AgentLLMResponse("tool returned hello", []),
        ]
    )
    tool_result = run_agent(_context(), tools={"echo": lambda text: {"text": text}}, llm_call=tool_llm)
    assert tool_result.final_answer == "tool returned hello" and len(tool_calls) == 2
    assert tool_result.events[0] == {"type": "user_message", "content": "hello"}
    assert next(event for event in tool_result.events if event["type"] == "llm_message") == {
        "type": "llm_message", "step": 1, "content": "准备调用工具"
    }
    assert [event["type"] for event in tool_result.events].index("assistant_trace") < [
        event["type"] for event in tool_result.events
    ].index("tool_call")
    assistant_call = next(message for message in tool_result.context.messages if message["role"] == "assistant" and message["tool_calls"])
    tool_message = next(message for message in tool_result.context.messages if message["role"] == "tool")
    assert assistant_call["tool_calls"][0]["id"] == tool_message["tool_call_id"] == "call_1"

    unknown_llm, _ = _sequence_llm(
        [AgentLLMResponse("", [AgentToolCall("call_2", "missing", "{}")] ), AgentLLMResponse("recovered", [])]
    )
    unknown = run_agent(_context(), llm_call=unknown_llm)
    assert 'Unknown tool: missing' in next(message["content"] for message in unknown.context.messages if message["role"] == "tool")
    assert not any(event["type"] == "assistant_trace" and event["step"] == 1 for event in unknown.events)

    invalid_llm, _ = _sequence_llm(
        [AgentLLMResponse("", [AgentToolCall("call_3", "echo", "not-json")]), AgentLLMResponse("recovered", [])]
    )
    invalid = run_agent(_context(), tools={"echo": lambda text: text}, llm_call=invalid_llm)
    assert "Invalid arguments" in next(message["content"] for message in invalid.context.messages if message["role"] == "tool")

    looping_llm = lambda *_: AgentLLMResponse("", [AgentToolCall("call_loop", "echo", '{"text":"loop"}')])
    try:
        run_agent(_context(max_steps=2), tools={"echo": lambda text: text}, llm_call=looping_llm)
    except AgentMaxStepsError as exc:
        assert (exc.step, exc.max_steps) == (2, 2)
    else:
        raise AssertionError("Expected AgentMaxStepsError")


if __name__ == "__main__":
    main()
