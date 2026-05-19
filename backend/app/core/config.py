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


LLM_PROVIDER = get_llm_config().provider
LLM_API_KEY = get_llm_config().api_key
LLM_BASE_URL = get_llm_config().base_url
LLM_MODEL = get_llm_config().model
LLM_TIMEOUT = get_llm_config().timeout
LLM_TEMPERATURE = get_llm_config().temperature


"""
    文件夹不存在则创建
"""
def ensure_storage_dirs() -> None:
    """Create local storage directories if they do not exist."""
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_SECTION_JSON_DIR.mkdir(parents=True, exist_ok=True)
