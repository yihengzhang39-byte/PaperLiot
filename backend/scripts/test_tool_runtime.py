"""Pure-local checks for the standalone tool runtime."""

from app.runtime.tool_executor import dispatch, execute_tool_call, execute_tool_calls
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall


def main() -> None:
    calls: list[str] = []
    registry = ToolRegistry()
    registry.register("echo", lambda text: {"text": text})
    registry.register("record", lambda value: calls.append(value) or value)
    registry.register("broken", lambda: (_ for _ in ()).throw(ValueError("boom")))

    assert registry.has("echo") and registry.get("echo") is not None
    assert registry.get("missing") is None and registry.names() == ("echo", "record", "broken")
    assert dispatch("echo", {"text": "hello"}, registry) == {"text": "hello"}

    success = execute_tool_call(AgentToolCall("call_123", "echo", '{"text":"hello"}'), registry)
    assert success.ok and success.tool_call_id == "call_123" and success.content == '{"text": "hello"}'

    invalid = execute_tool_call(AgentToolCall("call_bad", "echo", "not-json"), registry)
    assert not invalid.ok and invalid.tool_call_id == "call_bad" and "Invalid arguments" in invalid.content

    unknown = execute_tool_call(AgentToolCall("call_missing", "missing", "{}"), registry)
    assert not unknown.ok and "Unknown tool: missing" in unknown.content

    broken = execute_tool_call(AgentToolCall("call_broken", "broken", "{}"), registry)
    assert not broken.ok and "Tool execution failed" in broken.content

    results = execute_tool_calls(
        [
            AgentToolCall("call_1", "record", '{"value":"first"}'),
            AgentToolCall("call_2", "record", '{"value":"second"}'),
        ],
        registry,
    )
    assert [result.tool_call_id for result in results] == ["call_1", "call_2"]
    assert calls == ["first", "second"]

    assert execute_tool_call(AgentToolCall("call_object", "record", '{"value":"ok"}'), registry).content == "ok"
    print("ALL TOOL RUNTIME TESTS PASSED")


if __name__ == "__main__":
    main()
