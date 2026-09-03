"""Runtime state for the standalone agent loop."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    """Keep conversation and control state separate from PaperState."""

    session_id: str | None = None
    paper_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0
    max_steps: int = 8
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1.")
