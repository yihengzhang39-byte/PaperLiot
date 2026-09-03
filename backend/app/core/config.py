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
CHAT_SESSIONS_DIR = STORAGE_DIR / "chat_sessions"

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
    CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
