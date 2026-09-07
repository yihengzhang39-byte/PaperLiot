"""Local multi-paper context Tool built on the existing retrieval capability."""

from typing import Any

from app.runtime.tool_executor import ToolExecutionResult
from app.runtime.tool_registry import ToolRegistry
from app.services.retrieval_service import MAX_PAPERS, MAX_TOP_K, ToolInputError, validate_paper_id, validate_top_k
from app.tools.paper_tools import retrieve_paper_context


def get_multi_paper_context(
    paper_ids: list[str] | None = None,
    query: str = "",
    top_k: int | None = None,
) -> dict[str, object]:
    """Retrieve source-labeled context for each distinct requested paper."""
    top_k = validate_top_k(top_k)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise ToolInputError("query must contain 1 to 2000 characters")
    if not isinstance(paper_ids, list) or not 1 <= len(paper_ids) <= MAX_PAPERS:
        raise ToolInputError(f"paper_ids must contain 1 to {MAX_PAPERS} identifiers")
    unique_ids: list[str] = []
    for paper_id in paper_ids:
        validate_paper_id(paper_id)
        if paper_id.strip() not in unique_ids:
            unique_ids.append(paper_id.strip())

    papers = []
    for paper_id in unique_ids:
        try:
            result = retrieve_paper_context(paper_id=paper_id, query=query, top_k=top_k)
            papers.append(
                {
                    "paper_id": paper_id,
                    "success": result.get("success", False),
                    "chunks": result["chunks"],
                    "sources": result.get("sources", []),
                    "error": "" if result.get("success") else str(result.get("warning", "No cached parser result.")),
                }
            )
        except (FileNotFoundError, ValueError) as exc:
            papers.append({"paper_id": paper_id, "success": False, "chunks": [], "error": str(exc)})
    successes = sum(paper["success"] is True for paper in papers)
    return {"query": query.strip(), "success": successes > 0, "status": "success" if successes == len(papers) else "partial" if successes else "error", "papers": papers}


def project_multi_paper_history(_args: dict[str, Any], result: ToolExecutionResult) -> dict[str, Any]:
    import json
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not result.ok or not isinstance(payload, dict):
        return {"tool": "get_multi_paper_context", "status": "error", "summary": (result.error or "Tool execution failed.")[:300]}
    papers = payload.get("papers") if isinstance(payload.get("papers"), list) else []
    return {"tool": "get_multi_paper_context", "status": payload.get("status", "success"), "query": str(payload.get("query", ""))[:300], "paper_ids": [item.get("paper_id") for item in papers if isinstance(item, dict) and isinstance(item.get("paper_id"), str)][:20], "result_count": len(papers), "papers": [{"paper_id": paper.get("paper_id"), "success": paper.get("success"), "error": str(paper.get("error", ""))[:300]} for paper in papers]}


MULTI_PAPER_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_multi_paper_context",
            "description": "Retrieve actual versioned evidence for up to 8 papers under one total output budget. Inspect each paper's coverage/omitted_chunks/errors before comparing; omitted papers or text were not read. Use read_paper_chunk for a shown source reference, or retrieve_paper_context to target missing evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_PAPERS},
                    "query": {"type": "string", "maxLength": 2000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_TOP_K},
                },
                "required": ["paper_ids", "query"],
            },
        },
    }
]


def register_multi_paper_tools(registry: ToolRegistry) -> None:
    """Register multi-paper retrieval without adding Runtime special cases."""
    registry.register("get_multi_paper_context", get_multi_paper_context, produces=("multi_paper_context",), project_history_result=project_multi_paper_history)
