"""Pure-local checks for Tool runtime-state contracts."""

import json

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import run_agent
from app.agents.paper_agent import PAPER_AGENT_SYSTEM_PROMPT, build_paper_tool_registry
from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentLLMResponse, AgentToolCall


def main() -> None:
    paper_registry = build_paper_tool_registry()
    assert paper_registry.definition("get_paper_info").requires == ("paper_id",)
    assert paper_registry.definition("extract_sections").requires == ("paper_id", "parsed_pdf")
    assert paper_registry.definition("retrieve_paper_context").produces == ("retrieved_paper_context",)

    called: list[str] = []
    registry = ToolRegistry()
    registry.register("needs_paper", lambda: called.append("ran") or {"success": True}, requires=("paper_id",))
    registry.register("needs_parse", lambda: called.append("parse") or {"success": True}, requires=("parsed_pdf",))
    registry.register("parse", lambda: {"success": True}, produces=("parsed_pdf",))
    registry.register("failed_parse", lambda: {"success": False}, produces=("parsed_pdf",))

    context = AgentContext(paper_id="paper_1")
    assert execute_tool_call(AgentToolCall("ok", "needs_paper", "{}"), registry, context=context).ok
    assert called == ["ran"]

    blocked = execute_tool_call(AgentToolCall("blocked", "needs_parse", "{}"), registry, context=context)
    assert not blocked.ok and "Missing required state: parsed_pdf" in blocked.content and called == ["ran"]

    assert execute_tool_call(AgentToolCall("parse", "parse", "{}"), registry, context=context).ok
    assert context.state["parsed_pdf"] is True

    failed_context = AgentContext(paper_id="paper_1")
    failed = execute_tool_call(AgentToolCall("failed", "failed_parse", "{}"), registry, context=failed_context)
    assert failed.ok and json.loads(failed.content)["success"] is False and "parsed_pdf" not in failed_context.state

    responses = iter(
        [
            AgentLLMResponse("", [AgentToolCall("echo", "echo", '{"text":"hello"}')]),
            AgentLLMResponse("done", []),
        ]
    )
    result = run_agent(
        AgentContext(messages=[{"role": "user", "content": "hello"}]),
        tools={"echo": lambda text: {"text": text}},
        llm_call=lambda *_: next(responses),
    )
    assert result.final_answer == "done"
    assert "do not call\nboth Tools in the same assistant response" in PAPER_AGENT_SYSTEM_PROMPT
    assert "only in the next Agent Step" in PAPER_AGENT_SYSTEM_PROMPT
    print("ALL TOOL CONTRACT TESTS PASSED")


if __name__ == "__main__":
    main()
