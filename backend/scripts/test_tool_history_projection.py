"""Pure-local checks for deterministic, bounded future Tool history."""

from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall
from app.tools.memory_tools import project_save_research_memory_history, project_save_user_profile_history
from app.tools.paper_tools import project_paper_history


def main() -> None:
    registry = ToolRegistry()
    huge_chunks = [{"section": "Method", "text": "x" * 20_000} for _ in range(5)]
    registry.register("retrieve_paper_context", lambda **_args: {"success": True, "paper_id": "p1", "query": "method", "chunks": huge_chunks}, project_history_result=project_paper_history)
    result = execute_tool_call(AgentToolCall("retrieve", "retrieve_paper_context", '{"paper_id":"p1","query":"method"}'), registry)
    assert "x" * 100 in result.content
    assert result.history_result == {"tool": "retrieve_paper_context", "status": "success", "paper_id": "p1", "query": "method", "chunk_count": 5, "sections": ["Method"]}

    registry.register("parse_pdf_with_grobid", lambda **_args: {"success": True, "paper_id": "p1", "parser": "grobid", "cached": False, "sections": [{"text": "x" * 20_000}]}, project_history_result=project_paper_history)
    parsed = execute_tool_call(AgentToolCall("parse", "parse_pdf_with_grobid", '{"paper_id":"p1"}'), registry)
    assert parsed.history_result == {"tool": "parse_pdf_with_grobid", "status": "success", "paper_id": "p1", "parser": "grobid", "cache_created": True}

    registry.register("save_user_profile", lambda **_args: {"saved": True, "updated_fields": {"identity": "zyh（人工智能方向本科生）"}}, project_history_result=project_save_user_profile_history)
    profile = execute_tool_call(AgentToolCall("profile", "save_user_profile", '{"identity":"zyh"}'), registry)
    assert profile.history_result == {"tool": "save_user_profile", "status": "success", "saved": True, "updated": {"identity": "zyh（人工智能方向本科生）"}}

    registry.register("save_research_memory", lambda **_args: {"saved": True, "category": "paper", "entry": "DEIM"}, project_history_result=project_save_research_memory_history)
    memory = execute_tool_call(AgentToolCall("memory", "save_research_memory", '{"category":"paper","entry":"DEIM"}'), registry)
    assert memory.history_result["updated"] == {"paper": "DEIM"}

    registry.register("profile_fail", lambda **_args: {"success": False, "error": "rejected"}, project_history_result=project_save_user_profile_history)
    failed = execute_tool_call(AgentToolCall("failed", "profile_fail", "{}"), registry)
    assert failed.history_result["status"] == "error" and "updated" not in failed.history_result

    fallback = execute_tool_call(AgentToolCall("fallback", "echo", '{"token":"secret"}'), ToolRegistry({"echo": lambda **_args: {"large": "x" * 20_000}}))
    assert fallback.history_result == {"tool": "echo", "status": "success", "summary": "echo completed"}
    print("ALL TOOL HISTORY PROJECTION TESTS PASSED")


if __name__ == "__main__":
    main()
