"""Experiment analysis node."""

from app.agents.paper_state import PaperState
from app.services.llm_service import analyze_experiment


def experiment_analyze_node(state: PaperState) -> dict[str, str]:
    """Analyze experiment design and results."""
    return analyze_experiment(state.get("experiments", "")[:20000])
