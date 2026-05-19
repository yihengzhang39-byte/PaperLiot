"""Method analysis node."""

from app.agents.paper_state import PaperState
from app.services.llm_service import analyze_method


def method_analyze_node(state: PaperState) -> dict[str, object]:
    """Analyze the paper's problem, motivation, method, and limitations."""
    text = "\n\n".join(
        [
            state.get("abstract", ""),
            state.get("introduction", ""),
            state.get("related_work", ""),
            state.get("method", ""),
        ]
    )[:20000]
    return analyze_method(text)
