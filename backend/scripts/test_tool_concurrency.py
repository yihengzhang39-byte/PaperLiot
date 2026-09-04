"""Pure-local checks for bounded Tool scheduling."""

import threading
import time

from app.agents.agent_context import AgentContext
from app.runtime.tool_scheduler import execute_scheduled_tool_calls
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall


def _call(identifier: str, name: str, arguments: str = "{}") -> AgentToolCall:
    return AgentToolCall(identifier, name, arguments)


def _tracked_tool(label: str, events: list[str], active: dict[str, int], lock: threading.Lock, delay: float = 0.03):
    def tool() -> dict[str, object]:
        with lock:
            active["current"] += 1
            active["peak"] = max(active["peak"], active["current"])
            events.append(f"{label}:start")
        time.sleep(delay)
        with lock:
            events.append(f"{label}:done")
            active["current"] -= 1
        return {"label": label}

    return tool


def main() -> None:
    lock = threading.Lock()

    events: list[str] = []
    active = {"current": 0, "peak": 0}
    registry = ToolRegistry()
    for name in ("a", "b"):
        registry.register(name, _tracked_tool(name, events, active, lock), is_concurrency_safe=lambda _args: True)
    results = execute_scheduled_tool_calls([_call("a", "a"), _call("b", "b")], registry, max_parallel_tool_calls=2)
    assert active["peak"] == 2 and [result.tool_call_id for result in results] == ["a", "b"]

    events = []
    active = {"current": 0, "peak": 0}
    registry = ToolRegistry()
    for name in ("a", "b"):
        registry.register(name, _tracked_tool(name, events, active, lock))
    execute_scheduled_tool_calls([_call("a", "a"), _call("b", "b")], registry, max_parallel_tool_calls=2)
    assert active["peak"] == 1

    events = []
    active = {"current": 0, "peak": 0}
    registry = ToolRegistry()
    for name in ("a", "b", "d", "e"):
        registry.register(name, _tracked_tool(name, events, active, lock), is_concurrency_safe=lambda _args: True)
    registry.register("c", _tracked_tool("c", events, active, lock))
    execute_scheduled_tool_calls(
        [_call(name, name) for name in ("a", "b", "c", "d", "e")], registry, max_parallel_tool_calls=2
    )
    assert max(events.index("a:done"), events.index("b:done")) < events.index("c:start") < events.index("c:done")
    assert events.index("c:done") < min(events.index("d:start"), events.index("e:start"))

    events = []
    active = {"current": 0, "peak": 0}
    registry = ToolRegistry()
    registry.register("false", _tracked_tool("false", events, active, lock), is_concurrency_safe=lambda _args: False)
    registry.register("raises", _tracked_tool("raises", events, active, lock), is_concurrency_safe=lambda _args: (_ for _ in ()).throw(ValueError()))
    execute_scheduled_tool_calls([_call("false", "false"), _call("raises", "raises")], registry, max_parallel_tool_calls=2)
    assert active["peak"] == 1

    safety_checks: list[dict[str, object]] = []
    registry = ToolRegistry()
    registry.register("validated", lambda value: value, is_concurrency_safe=lambda args: safety_checks.append(args) or True)
    invalid = execute_scheduled_tool_calls([_call("invalid", "validated", "not-json")], registry, max_parallel_tool_calls=2)
    assert not invalid[0].ok and safety_checks == []

    events = []
    active = {"current": 0, "peak": 0}
    registry = ToolRegistry()
    for name in ("a", "b"):
        registry.register(name, _tracked_tool(name, events, active, lock), is_concurrency_safe=lambda _args: True)
    execute_scheduled_tool_calls([_call("a", "a"), _call("b", "b")], registry, max_parallel_tool_calls=1)
    assert active["peak"] == 1

    events = []
    active = {"current": 0, "peak": 0}
    registry = ToolRegistry()
    for index in range(5):
        registry.register(str(index), _tracked_tool(str(index), events, active, lock), is_concurrency_safe=lambda _args: True)
    execute_scheduled_tool_calls([_call(str(index), str(index)) for index in range(5)], registry, max_parallel_tool_calls=2)
    assert active["peak"] == 2

    completion: list[str] = []
    registry = ToolRegistry()
    registry.register("slow", lambda: time.sleep(0.05) or completion.append("slow") or {"ok": True}, is_concurrency_safe=lambda _args: True)
    registry.register("fast", lambda: time.sleep(0.01) or completion.append("fast") or {"ok": True}, is_concurrency_safe=lambda _args: True)
    trace: list[dict[str, object]] = []
    results = execute_scheduled_tool_calls([_call("slow", "slow"), _call("fast", "fast")], registry, max_parallel_tool_calls=2, trace=trace)
    assert completion == ["fast", "slow"] and [result.tool_call_id for result in results] == ["slow", "fast"]
    assert [event["tool_call_id"] for event in trace if event["event"] == "tool_succeeded"] == ["slow", "fast"]

    registry = ToolRegistry()
    registry.register("ok", lambda: {"ok": True}, is_concurrency_safe=lambda _args: True)
    registry.register("broken", lambda: (_ for _ in ()).throw(RuntimeError()), is_concurrency_safe=lambda _args: True)
    results = execute_scheduled_tool_calls(
        [_call("one", "ok"), _call("two", "broken"), _call("three", "ok")], registry, max_parallel_tool_calls=3
    )
    assert [result.tool_call_id for result in results] == ["one", "two", "three"] and [result.ok for result in results] == [True, False, True]

    context = AgentContext(paper_id="paper_1")
    registry = ToolRegistry()
    registry.register("produces", lambda: {"success": True}, produces=("parsed_pdf",), is_concurrency_safe=lambda _args: True)
    registry.register("fails", lambda: {"success": False}, produces=("sections",), is_concurrency_safe=lambda _args: True)
    execute_scheduled_tool_calls([_call("good", "produces"), _call("bad", "fails")], registry, context=context, max_parallel_tool_calls=2)
    assert context.state["parsed_pdf"] is True and "sections" not in context.state
    print("ALL TOOL CONCURRENCY TESTS PASSED")


if __name__ == "__main__":
    main()
