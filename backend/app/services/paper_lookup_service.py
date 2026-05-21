"""Manual paper metadata lookup service using reusable Agent tools."""

from difflib import SequenceMatcher
import re
from typing import Any

from app.core.config import get_paper_lookup_config
from app.tools.paper_lookup_tools import (
    search_arxiv_paper,
    search_crossref_paper,
    search_openalex_paper,
)


def _extract_doi(text: str) -> str:
    """Extract a DOI from text if present."""
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", text or "", re.IGNORECASE)
    return match.group(0) if match else ""

def _extract_arxiv_id(text: str) -> str:
    """Extract an arXiv id from text if present."""
    match = re.search(r"(?:arXiv[:\s]*)?(\d{4}\.\d{4,5}(?:v\d+)?)", text or "", re.IGNORECASE)
    if match:
        return match.group(1)
    old_match = re.search(r"(?:arXiv[:\s]*)?([a-z-]+(?:\.[A-Z]{2})?/\d{7})", text or "", re.IGNORECASE)
    return old_match.group(1) if old_match else ""


def _get_first_author(authors: list[str] | None) -> str:
    """Return the first author name if available."""
    return authors[0] if authors else ""


def _candidate_title_from_raw_text(raw_text: str) -> str:
    """
    截取论文开头文字，过滤空行、页码、
    太长太短句子，找出最符合标题特征的一行返回，没找到就返回空。
    """
    """Guess a title from the beginning of raw text without sending full text."""
    for line in (raw_text or "")[:2000].splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("--- Page"):
            continue
        if len(cleaned) < 8 or len(cleaned) > 220:
            continue
        return cleaned
    return ""


def _build_lookup_queries(
    title: str = "",
    authors: list[str] | None = None,
    raw_text: str = "",
) -> dict[str, str]:
    """
        这个函数专门整理搜索论文的线索，不做搜索，只做打包；
        标题无效就从原文猜标题，同时从文本里抠出论文身份证（DOI/arXiv）；
        最终输出标题、第一作者、DOI、arXiv 号4 个线索，让后面的搜索工具精准查论文。
    """
    """Build safe lookup query terms."""
    guessed_title = title if title and title != "未明确提及" else _candidate_title_from_raw_text(raw_text)
    text_for_ids = "\n".join(part for part in [title, raw_text[:2000]] if part)
    return {
        "title": guessed_title,
        "first_author": _get_first_author(authors),
        "doi": _extract_doi(text_for_ids),
        "arxiv_id": _extract_arxiv_id(text_for_ids),
    }


def _similarity(left: str, right: str) -> float:
    """Compute normalized title similarity."""
    """
        给两个论文标题「去干扰、统一格式」，然后计算它们的相似度
    """
    left = re.sub(r"\s+", " ", (left or "").lower()).strip()
    right = re.sub(r"\s+", " ", (right or "").lower()).strip()
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()



def _score_candidate(
    candidate: dict[str, Any],
    title: str,
    authors: list[str] | None,
    year: str,
    doi: str,
    arxiv_id: str,
) -> float:
    """Score a metadata candidate against the current paper metadata."""
    """
        该函数先通过 DOI/arXiv 编号精准判定，再按标题、作者、年份加权计分，
        最后封顶 0.99 分，快速选出最匹配的论文。
    """
    candidate_doi = str(candidate.get("doi", "") or "")
    candidate_url = str(candidate.get("url", "") or "")
    if doi and candidate_doi and doi.lower() == candidate_doi.lower():
        return 0.98
    if arxiv_id and arxiv_id.lower() in candidate_url.lower():
        return 0.97

    score = _similarity(title, str(candidate.get("title", "") or "")) * 0.75
    first_author = _get_first_author(authors).lower()
    candidate_authors = [str(author).lower() for author in candidate.get("authors", []) or []]
    if first_author and any(first_author in author or author in first_author for author in candidate_authors):
        score += 0.12
    if year and str(candidate.get("year", "") or "") == str(year):
        score += 0.08
    if candidate.get("source") == "arxiv" and arxiv_id:
        score += 0.1
    return min(score, 0.99)


def _pick_best_match(
    candidates: list[dict[str, Any]],
    title: str,
    authors: list[str] | None,
    year: str,
    doi: str,
    arxiv_id: str,
) -> dict[str, Any]:
    """Pick the highest-confidence candidate."""
    best: dict[str, Any] = {}
    best_score = 0.0
    for candidate in candidates:
        score = _score_candidate(candidate, title, authors, year, doi, arxiv_id)
        candidate["confidence"] = round(score, 4)
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _invoke_tool(tool_obj: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Invoke a LangChain tool object with structured input."""
    result = tool_obj.invoke(payload)
    return result if isinstance(result, dict) else {"candidates": [], "error": str(result)}


def lookup_paper_metadata(
    title: str = "",
    authors: list[str] | None = None,
    raw_text: str = "",
    paper_language: str = "zh",
    year: str = "",
) -> dict[str, object]:
    """Look up paper metadata by manually invoking reusable lookup tools."""
    """
        根据你给的论文碎片信息（标题 / 作者 / 原文），
        自动去学术平台搜索论文 → 收集所有结果 → 选出最匹配的那一篇 → 打包返回给系统。
    """
    config = get_paper_lookup_config()
    queries = _build_lookup_queries(title=title, authors=authors, raw_text=raw_text)
    lookup_title = queries["title"]
    first_author = queries["first_author"]
    doi = queries["doi"]
    arxiv_id = queries["arxiv_id"]

    debug: dict[str, object] = {
        "enabled": True,
        "queries": [],
        "providers_tried": [],
        "errors": [],
        "best_confidence": 0.0,
    }
    candidates: list[dict[str, Any]] = []

    if not lookup_title and not arxiv_id:
        debug["errors"].append("No safe title or arXiv id query could be built.")
        return {"best_match": {}, "candidates": [], "debug": debug}

    providers = config.providers or ["arxiv", "crossref", "openalex"]

    def collect(provider: str, result: dict[str, Any]) -> None:
        debug["providers_tried"].append(provider)
        debug["queries"].append(result.get("query", ""))
        if result.get("error"):
            debug["errors"].append(f"{provider}: {result['error']}")
        candidates.extend(result.get("candidates", []) or [])

    if arxiv_id and "arxiv" in providers:
        collect("arxiv", _invoke_tool(search_arxiv_paper, {"arxiv_id": arxiv_id, "title": ""}))

    if lookup_title:
        if "crossref" in providers:
            collect(
                "crossref",
                _invoke_tool(
                    search_crossref_paper,
                    {"title": lookup_title, "first_author": first_author, "year": year, "doi": doi},
                ),
            )
        if "openalex" in providers:
            collect(
                "openalex",
                _invoke_tool(
                    search_openalex_paper,
                    {"title": lookup_title, "first_author": first_author, "year": year, "doi": doi},
                ),
            )
        if paper_language == "en" and "arxiv" in providers and not arxiv_id:
            collect("arxiv", _invoke_tool(search_arxiv_paper, {"title": lookup_title, "arxiv_id": ""}))

    best_match = _pick_best_match(candidates, lookup_title, authors, year, doi, arxiv_id)
    debug["best_confidence"] = float(best_match.get("confidence", 0.0) or 0.0)
    return {
        "best_match": best_match,
        "candidates": candidates,
        "debug": debug,
    }
