"""Local multi-paper context Tool built on the existing retrieval capability."""

from typing import Any

from app.runtime.tool_executor import ToolExecutionResult
from app.runtime.tool_registry import ToolRegistry
from app.tools.paper_tools import retrieve_paper_context


def get_multi_paper_context(
    paper_ids: list[str] | None = None,
    query: str = "",
    top_k: int | None = None,
) -> dict[str, object]:
    """Retrieve source-labeled context for each distinct requested paper."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query cannot be empty")
    if not isinstance(paper_ids, list) or not paper_ids:
        raise ValueError("paper_ids must be a non-empty list")
    unique_ids: list[str] = []
    for paper_id in paper_ids:
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise ValueError("paper_ids must contain non-empty strings")
        if paper_id.strip() not in unique_ids:
            unique_ids.append(paper_id.strip())

    papers = []
    for paper_id in unique_ids:
        try:
            result = retrieve_paper_context(paper_id=paper_id, query=query, top_k=top_k)
            papers.append(
                {
                    "paper_id": paper_id,
                    "chunks": result["chunks"],
                    "error": "" if result.get("success") else str(result.get("warning", "No cached parser result.")),
                }
            )
        except (FileNotFoundError, ValueError) as exc:
            papers.append({"paper_id": paper_id, "chunks": [], "error": str(exc)})
    return {"query": query.strip(), "papers": papers}


def project_multi_paper_history(_args: dict[str, Any], result: ToolExecutionResult) -> dict[str, Any]:
    import json
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not result.ok or not isinstance(payload, dict):
        return {"tool": "get_multi_paper_context", "status": "error", "summary": (result.error or "Tool execution failed.")[:300]}
    papers = payload.get("papers") if isinstance(payload.get("papers"), list) else []
    return {"tool": "get_multi_paper_context", "status": "success", "query": str(payload.get("query", ""))[:300], "paper_ids": [item.get("paper_id") for item in papers if isinstance(item, dict) and isinstance(item.get("paper_id"), str)][:20], "result_count": len(papers)}


MULTI_PAPER_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_multi_paper_context",
            "description": "Retrieve separately labeled context for comparing multiple uploaded papers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1},
                },
                "required": ["paper_ids", "query"],
            },
        },
    }
]


def register_multi_paper_tools(registry: ToolRegistry) -> None:
    """Register multi-paper retrieval without adding Runtime special cases."""
    registry.register("get_multi_paper_context", get_multi_paper_context, produces=("multi_paper_context",), project_history_result=project_multi_paper_history)
