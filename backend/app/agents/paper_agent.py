"""Paper-domain configuration for the generic Agent Loop."""

from typing import Any

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import AgentEventSink, AgentLoopResult, LLMCaller, LLMStreamCaller, run_agent
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import call_llm_with_tools
from app.tools.memory_tools import MEMORY_TOOL_SPECS, register_memory_tools
from app.tools.multi_paper_tools import MULTI_PAPER_TOOL_SPECS, register_multi_paper_tools
from app.tools.paper_tools import PAPER_TOOL_SPECS, register_paper_tools


PAPER_AGENT_SYSTEM_PROMPT = """You are PaperPilot's paper-reading agent.
Use the provided Paper Tools when an answer depends on paper content. Never claim
to have read a paper you have not queried, and never invent paper metadata,
sections, or methods. Use the current paper_id explicitly in Tool arguments.
If a Tool result is insufficient, you may use another available Tool; if it is
sufficient, answer directly. If a Tool fails, decide whether to retry, use another
Tool, or explain the limitation. Use retrieve_paper_context for paper-specific
details when metadata or section previews are insufficient. Only call the provided
tools. Only use save_research_memory when the user explicitly asks to remember a
stable research fact or a durable cross-paper finding; never save ordinary chat."""


PAPER_AGENT_TOOL_SPECS = [*PAPER_TOOL_SPECS, *MEMORY_TOOL_SPECS, *MULTI_PAPER_TOOL_SPECS]


def build_paper_tool_registry() -> ToolRegistry:
    """Create a registry containing only the currently supported Paper Tools."""
    registry = ToolRegistry()
    register_paper_tools(registry)
    register_memory_tools(registry)
    register_multi_paper_tools(registry)
    return registry


def _semantic_history(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Keep only prior user/final-assistant messages in an Agent run."""
    return [
        {"role": item["role"], "content": item["content"]}
        for item in history or []
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ]


def build_paper_agent_messages(
    message: str,
    paper_id: str | None,
    *,
    history: list[dict[str, Any]] | None = None,
    system_context: str | None = None,
    active_paper_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """Build one system prompt, prior semantic history, and the current user turn."""
    paper_context = (
        f"Current paper_id: {paper_id}."
        if paper_id
        else "No current paper is available; ask the user to upload or identify one before discussing paper-specific content."
    )
    system_parts = [PAPER_AGENT_SYSTEM_PROMPT]
    if system_context and system_context.strip():
        system_parts.append(system_context.strip())
    active_paper_ids = list(
        dict.fromkeys(
            value.strip() for value in active_paper_ids or [] if isinstance(value, str) and value.strip()
        )
    )
    if active_paper_ids:
        system_parts.append(f"Active paper_ids: {', '.join(active_paper_ids)}. Use get_multi_paper_context for comparison questions.")
    system_parts.append(paper_context)
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        *_semantic_history(history),
        {"role": "user", "content": message},
    ]


def run_paper_agent(
    message: str,
    *,
    paper_id: str | None = None,
    session_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    system_context: str | None = None,
    active_paper_ids: list[str] | None = None,
    max_steps: int = 8,
    llm_call: LLMCaller = call_llm_with_tools,
    llm_stream: LLMStreamCaller | None = None,
    event_sink: AgentEventSink | None = None,
) -> AgentLoopResult:
    """Run the generic loop with PaperPilot's prompt, tools, and schemas."""
    message = message.strip()
    if not message:
        raise ValueError("message cannot be empty")
    paper_id = paper_id.strip() if paper_id else None
    registry = build_paper_tool_registry()
    context = AgentContext(
        session_id=session_id,
        paper_id=paper_id,
        messages=build_paper_agent_messages(
            message,
            paper_id,
            history=history,
            system_context=system_context,
            active_paper_ids=active_paper_ids,
        ),
        max_steps=max_steps,
        metadata={
            "paper_agent": {
                "paper_id": paper_id,
                "active_paper_ids": active_paper_ids or [],
                "available_tools": list(registry.names()),
            }
        },
    )
    return run_agent(
        context,
        tool_registry=registry,
        tool_specs=PAPER_AGENT_TOOL_SPECS,
        llm_call=llm_call,
        llm_stream=llm_stream,
        event_sink=event_sink,
    )
