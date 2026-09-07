"""Application configuration and local storage paths."""

from dataclasses import dataclass
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - keeps config importable before deps install.
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parents[2]
STORAGE_DIR = BASE_DIR / "storage"
PAPERS_DIR = STORAGE_DIR / "papers"
NOTES_DIR = STORAGE_DIR / "notes"
PAPER_SECTION_JSON_DIR = STORAGE_DIR / "paper_section_json"
PAPER_METADATA_DIR = STORAGE_DIR / "paper_metadata"
PAPER_CHUNKS_DIR = STORAGE_DIR / "paper_chunks"
PAPER_PARSE_CACHE_DIR = STORAGE_DIR / "paper_parse_cache"
CHAT_SESSIONS_DIR = STORAGE_DIR / "chat_sessions"
PAPER_INDEX_PATH = STORAGE_DIR / "paper_index.json"
DATABASE_PATH = STORAGE_DIR / "paperpilot.db"

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class LLMConfig:
    """Runtime LLM provider configuration."""

    provider: str = "mock"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: int = 60
    temperature: float = 0.2


class ContextConfigError(ValueError):
    """Invalid or missing Agent model capacity (never inferred for real models)."""

    code = "context_config_invalid"


@dataclass(frozen=True)
class CompactionConfig:
    recent_ratio: float = 0.16
    target_tokens: int = 1200
    max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        if not 0 <= self.recent_ratio < 1:
            raise ContextConfigError("COMPACTION_RECENT_RATIO must be in [0, 1).")
        for name in ("target_tokens", "max_output_tokens"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ContextConfigError(f"Compaction {name} must be a positive integer.")
        if self.target_tokens >= self.max_output_tokens:
            raise ContextConfigError("COMPACTION_TARGET_TOKENS must be below COMPACTION_MAX_OUTPUT_TOKENS.")


def get_compaction_config() -> CompactionConfig:
    try:
        return CompactionConfig(_get_float_env("COMPACTION_RECENT_RATIO", 0.16), _get_int_env("COMPACTION_TARGET_TOKENS", 1200), _get_int_env("COMPACTION_MAX_OUTPUT_TOKENS", 4096))
    except ValueError as exc:
        raise ContextConfigError(f"Invalid compaction configuration: {exc}") from exc


@dataclass(frozen=True)
class ContextConfig:
    """Capacity of the configured Agent model; independent of LangGraph settings."""

    window: int
    max_output_tokens: int
    safety_tokens: int = 1024
    input_limit: int | None = None
    output_limit: int | None = None
    trigger_ratio: float = 0.8
    target_ratio: float = 0.6
    target_budget_ratio: float = 0.8
    output_token_parameter: str = "max_tokens"

    def __post_init__(self) -> None:
        for name in ("window", "max_output_tokens", "safety_tokens", "input_limit", "output_limit"):
            value = getattr(self, name)
            if value is None and name in {"input_limit", "output_limit"}:
                continue
            if type(value) is not int or value <= 0:
                raise ContextConfigError(f"Context {name} must be a positive integer.")
        if self.window - self.max_output_tokens - self.safety_tokens <= 0:
            raise ContextConfigError("Context input budget B = W - O - S must be positive.")
        if self.output_limit is not None and self.max_output_tokens > self.output_limit:
            raise ContextConfigError("Context max_output_tokens exceeds the model output_limit.")
        if self.input_limit is not None and self.input_limit <= self.safety_tokens:
            raise ContextConfigError("Context input_limit must exceed safety_tokens.")
        if not (0 < self.target_ratio < self.trigger_ratio < 1 and 0 < self.target_budget_ratio < 1):
            raise ContextConfigError("Context ratios must be in (0, 1), with target below trigger.")
        if self.output_token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ContextConfigError("Unsupported context output_token_parameter.")


def get_context_config(llm: LLMConfig) -> ContextConfig:
    """Read Agent-only limits lazily, so graph callers retain their existing policy."""
    if llm.provider not in {"mock", "deepseek", "openai_compatible"}:
        raise ContextConfigError("Unsupported context LLM provider.")
    if llm.provider != "mock" and not llm.model:
        raise ContextConfigError("LLM_MODEL is required for context budgeting.")
    try:
        config = ContextConfig(
            window=_get_int_env("AGENT_CONTEXT_WINDOW", 65536 if llm.provider == "mock" else 0),
            max_output_tokens=_get_int_env("AGENT_MAX_OUTPUT_TOKENS", 4096 if llm.provider == "mock" else 0),
            safety_tokens=_get_int_env("AGENT_CONTEXT_SAFETY_TOKENS", 1024),
            input_limit=int(os.environ["AGENT_MODEL_INPUT_LIMIT"]) if os.getenv("AGENT_MODEL_INPUT_LIMIT", "").strip() else None,
            output_limit=int(os.environ["AGENT_MODEL_OUTPUT_LIMIT"]) if os.getenv("AGENT_MODEL_OUTPUT_LIMIT", "").strip() else None,
            trigger_ratio=_get_float_env("AGENT_CONTEXT_TRIGGER_RATIO", 0.8),
            target_ratio=_get_float_env("AGENT_CONTEXT_TARGET_RATIO", 0.6),
            target_budget_ratio=_get_float_env("AGENT_CONTEXT_TARGET_BUDGET_RATIO", 0.8),
            output_token_parameter=os.getenv("AGENT_OUTPUT_TOKEN_PARAMETER", "max_tokens").strip(),
        )
    except (ValueError, TypeError) as exc:
        raise ContextConfigError(f"Invalid Agent context configuration (AGENT_CONTEXT_WINDOW / AGENT_MAX_OUTPUT_TOKENS and related limits): {exc}") from exc
    if llm.provider == "deepseek" and config.output_token_parameter != "max_tokens":
        raise ContextConfigError("DeepSeek requires AGENT_OUTPUT_TOKEN_PARAMETER=max_tokens.")
    return config


@dataclass(frozen=True)
class PDFParserConfig:
    """Runtime PDF parser configuration."""

    parser: str = "pymupdf"
    grobid_base_url: str = "http://localhost:8070"
    timeout: int = 30


@dataclass(frozen=True)
class PaperLookupConfig:
    """Runtime paper metadata lookup configuration."""

    enabled: bool = False
    timeout: int = 10
    max_results: int = 5
    providers: list[str] | None = None


@dataclass(frozen=True)
class SectionRepairConfig:
    """Runtime section repair configuration."""

    llm_enabled: bool = False
    max_rounds: int = 1


@dataclass(frozen=True)
class PlanAgentConfig:
    """Runtime plan agent configuration."""

    llm_enabled: bool = False
    max_input_chars: int = 3000
    confidence_threshold: float = 0.7


@dataclass(frozen=True)
class RAGConfig:
    """Local lexical-retrieval settings."""

    chunk_size: int = 1200
    chunk_overlap: int = 200
    top_k: int = 3


@dataclass(frozen=True)
class SessionConfig:
    """Bounded local-chat history settings."""

    max_history_messages: int = 20


@dataclass(frozen=True)
class ToolRuntimeConfig:
    """Bounded concurrency for safe Tool calls in one Agent Step."""

    max_parallel_tool_calls: int = 10


@dataclass(frozen=True)
class ToolResultConfig:
    """Serialized model-result ceiling, including all papers and metadata."""

    max_chars: int = 12000
    read_chars: int = 2000

    def __post_init__(self) -> None:
        if type(self.max_chars) is not int or not 4096 <= self.max_chars <= 64000:
            raise ContextConfigError("TOOL_RESULT_MAX_CHARS must be between 4096 and 64000.")
        if type(self.read_chars) is not int or not 1 <= self.read_chars <= min(4000, self.max_chars // 2):
            raise ContextConfigError("TOOL_READ_MAX_CHARS must be positive and <= min(4000, result budget / 2).")


def get_tool_result_config() -> ToolResultConfig:
    try:
        return ToolResultConfig(_get_int_env("TOOL_RESULT_MAX_CHARS", 12000), _get_int_env("TOOL_READ_MAX_CHARS", 2000))
    except ValueError as exc:
        raise ContextConfigError(f"Invalid tool result configuration: {exc}") from exc


@dataclass(frozen=True)
class HarnessDebugTraceConfig:
    """Local-only observability switch for provider and Tool execution facts."""

    enabled: bool = True


def _get_int_env(name: str, default: int) -> int:
    """Read an integer environment variable with a safe default."""
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return int(value)

def _get_float_env(name: str, default: float) -> float:
    """Read a float environment variable with a safe default."""
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return float(value)


def _get_bool_env(name: str, default: bool) -> bool:
    """Read a boolean environment variable with a safe default."""
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def get_llm_config() -> LLMConfig:
    """Read LLM configuration from environment variables."""
    provider = os.getenv("LLM_PROVIDER", "mock").strip().lower() or "mock"
    if provider not in {"mock", "deepseek", "openai_compatible"}:
        raise ValueError(
            "Invalid LLM_PROVIDER. Expected one of: mock, deepseek, openai_compatible."
        )

    return LLMConfig(
        provider=provider,
        api_key=os.getenv("LLM_API_KEY", "").strip(),
        base_url=os.getenv("LLM_BASE_URL", "").strip().rstrip("/"),
        model=os.getenv("LLM_MODEL", "").strip(),
        timeout=_get_int_env("LLM_TIMEOUT", 60),
        temperature=_get_float_env("LLM_TEMPERATURE", 0.2),
    )


def get_pdf_parser_config() -> PDFParserConfig:
    """Read PDF parser configuration from environment variables."""
    parser = os.getenv("PDF_PARSER", "pymupdf").strip().lower() or "pymupdf"
    return PDFParserConfig(
        parser=parser,
        grobid_base_url=os.getenv("GROBID_BASE_URL", "http://localhost:8070").strip().rstrip("/"),
        timeout=_get_int_env("PDF_PARSER_TIMEOUT", 30),
    )


def get_paper_lookup_config() -> PaperLookupConfig:
    """Read paper metadata lookup configuration from environment variables."""
    providers = [
        provider.strip().lower()
        for provider in os.getenv("PAPER_LOOKUP_PROVIDERS", "arxiv,crossref,openalex").split(",")
        if provider.strip()
    ]
    enabled = _get_bool_env(
        "PAPER_INFO_TOOL_AGENT_ENABLED",
        _get_bool_env("PAPER_INFO_WEB_ENRICH_ENABLED", True),
    )
    return PaperLookupConfig(
        enabled=enabled,
        timeout=_get_int_env("PAPER_LOOKUP_TIMEOUT", 10),
        max_results=_get_int_env("PAPER_LOOKUP_MAX_RESULTS", 5),
        providers=providers,
    )


def is_paper_info_profile_enabled() -> bool:
    """Return whether paper_info_node profiling is enabled."""
    return _get_bool_env("PAPER_INFO_PROFILE_ENABLED", False)


def get_section_repair_config() -> SectionRepairConfig:
    """Read section repair configuration from environment variables."""
    return SectionRepairConfig(
        llm_enabled=_get_bool_env("SECTION_REPAIR_LLM_ENABLED", False),
        max_rounds=_get_int_env("SECTION_REPAIR_MAX_ROUNDS", 1),
    )


def get_plan_agent_config() -> PlanAgentConfig:
    """Read plan agent configuration from environment variables."""
    return PlanAgentConfig(
        llm_enabled=_get_bool_env("PLAN_AGENT_LLM_ENABLED", False),
        max_input_chars=_get_int_env("PLAN_AGENT_MAX_INPUT_CHARS", 3000),
        confidence_threshold=_get_float_env("PLAN_AGENT_CONFIDENCE_THRESHOLD", 0.7),
    )


def get_rag_config() -> RAGConfig:
    """Read bounded local-retrieval settings from environment variables."""
    chunk_size = max(_get_int_env("RAG_CHUNK_SIZE", 1200), 100)
    chunk_overlap = min(max(_get_int_env("RAG_CHUNK_OVERLAP", 200), 0), chunk_size - 1)
    return RAGConfig(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=max(_get_int_env("RAG_TOP_K", 3), 1),
    )


def get_session_config() -> SessionConfig:
    """Read the number of semantic messages kept for each local session."""
    return SessionConfig(max_history_messages=max(_get_int_env("CHAT_HISTORY_MAX_MESSAGES", 20), 1))


def get_tool_runtime_config() -> ToolRuntimeConfig:
    """Read the Tool scheduler's safe-call concurrency limit."""
    return ToolRuntimeConfig(max_parallel_tool_calls=max(_get_int_env("TOOL_MAX_PARALLEL_CALLS", 10), 1))


def get_harness_debug_trace_config() -> HarnessDebugTraceConfig:
    """Read the single switch controlling debug-trace persistence."""
    return HarnessDebugTraceConfig(enabled=_get_bool_env("HARNESS_DEBUG_TRACE", True))


LLM_PROVIDER = get_llm_config().provider
LLM_API_KEY = get_llm_config().api_key
LLM_BASE_URL = get_llm_config().base_url
LLM_MODEL = get_llm_config().model
LLM_TIMEOUT = get_llm_config().timeout
LLM_TEMPERATURE = get_llm_config().temperature

PDF_PARSER = get_pdf_parser_config().parser
GROBID_BASE_URL = get_pdf_parser_config().grobid_base_url
PDF_PARSER_TIMEOUT = get_pdf_parser_config().timeout

PAPER_INFO_TOOL_AGENT_ENABLED = get_paper_lookup_config().enabled
PAPER_INFO_WEB_ENRICH_ENABLED = PAPER_INFO_TOOL_AGENT_ENABLED
PAPER_LOOKUP_TIMEOUT = get_paper_lookup_config().timeout
PAPER_LOOKUP_MAX_RESULTS = get_paper_lookup_config().max_results
PAPER_LOOKUP_PROVIDERS = get_paper_lookup_config().providers
PAPER_INFO_PROFILE_ENABLED = is_paper_info_profile_enabled()
SECTION_REPAIR_LLM_ENABLED = get_section_repair_config().llm_enabled
SECTION_REPAIR_MAX_ROUNDS = get_section_repair_config().max_rounds
PLAN_AGENT_LLM_ENABLED = get_plan_agent_config().llm_enabled
PLAN_AGENT_MAX_INPUT_CHARS = get_plan_agent_config().max_input_chars
PLAN_AGENT_CONFIDENCE_THRESHOLD = get_plan_agent_config().confidence_threshold
RAG_CHUNK_SIZE = get_rag_config().chunk_size
RAG_CHUNK_OVERLAP = get_rag_config().chunk_overlap
RAG_TOP_K = get_rag_config().top_k
CHAT_HISTORY_MAX_MESSAGES = get_session_config().max_history_messages
TOOL_MAX_PARALLEL_CALLS = get_tool_runtime_config().max_parallel_tool_calls
HARNESS_DEBUG_TRACE = get_harness_debug_trace_config().enabled


"""
    文件夹不存在则创建
"""
def ensure_storage_dirs() -> None:
    """Create local storage directories if they do not exist."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_SECTION_JSON_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_METADATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_PARSE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
