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
    return PaperLookupConfig(
        enabled=_get_bool_env("PAPER_INFO_WEB_ENRICH_ENABLED", False),
        timeout=_get_int_env("PAPER_LOOKUP_TIMEOUT", 10),
        max_results=_get_int_env("PAPER_LOOKUP_MAX_RESULTS", 5),
        providers=providers,
    )


LLM_PROVIDER = get_llm_config().provider
LLM_API_KEY = get_llm_config().api_key
LLM_BASE_URL = get_llm_config().base_url
LLM_MODEL = get_llm_config().model
LLM_TIMEOUT = get_llm_config().timeout
LLM_TEMPERATURE = get_llm_config().temperature

PDF_PARSER = get_pdf_parser_config().parser
GROBID_BASE_URL = get_pdf_parser_config().grobid_base_url
PDF_PARSER_TIMEOUT = get_pdf_parser_config().timeout

PAPER_INFO_WEB_ENRICH_ENABLED = get_paper_lookup_config().enabled
PAPER_LOOKUP_TIMEOUT = get_paper_lookup_config().timeout
PAPER_LOOKUP_MAX_RESULTS = get_paper_lookup_config().max_results
PAPER_LOOKUP_PROVIDERS = get_paper_lookup_config().providers


"""
    文件夹不存在则创建
"""
def ensure_storage_dirs() -> None:
    """Create local storage directories if they do not exist."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_SECTION_JSON_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_METADATA_DIR.mkdir(parents=True, exist_ok=True)
