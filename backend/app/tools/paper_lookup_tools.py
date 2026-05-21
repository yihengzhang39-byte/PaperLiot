"""Paper metadata lookup tools.

These functions are wrapped with @tool so they can be reused by future
LLM-driven ToolNode workflows or the current paper_info_node tool agent.
"""

from html import unescape
import re
import xml.etree.ElementTree as ET

from langchain_core.tools import tool
import requests

from app.core.config import get_paper_lookup_config


def _empty_result(provider: str, query: str, error: str = "") -> dict[str, object]:
    """Return the standard tool result shape."""
    return {
        "provider": provider,
        "query": query,
        "candidates": [],
        "error": error,
    }


def _clean_text(value: object) -> str:
    """Normalize text returned by metadata providers."""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if item)
    text = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _first_year(item: dict[str, object]) -> str:
    """Extract year from common Crossref date fields."""
    for key in ["published-print", "published-online", "issued"]:
        parts = (item.get(key) or {}).get("date-parts") if isinstance(item.get(key), dict) else None
        if parts and parts[0]:
            return str(parts[0][0])
    return ""


def _crossref_authors(item: dict[str, object]) -> list[str]:
    """Extract Crossref author names."""
    authors = []
    for author in item.get("author", []) or []:
        given = str(author.get("given", "")).strip()
        family = str(author.get("family", "")).strip()
        name = " ".join(part for part in [given, family] if part).strip()
        if name:
            authors.append(name)
    return authors


def _openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Restore OpenAlex abstract_inverted_index to plain text."""
    if not inverted_index:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        positioned.extend((position, word) for position in positions)
    return " ".join(word for _, word in sorted(positioned)).strip()


@tool
def search_crossref_paper(
    title: str = "",
    first_author: str = "",
    year: str = "",
    doi: str = "",
) -> dict[str, object]:
    """Search Crossref for paper metadata candidates."""
    config = get_paper_lookup_config()
    query = doi or title or " ".join(part for part in [first_author, year] if part)
    if not query:
        return _empty_result("crossref", query, "empty query")

    try:
        if doi:
            response = requests.get(
                f"https://api.crossref.org/works/{doi}",
                timeout=config.timeout,
            )
            response.raise_for_status()
            items = [response.json().get("message", {})]
        else:
            response = requests.get(
                "https://api.crossref.org/works",
                params={"query.title": title or query, "rows": config.max_results},
                timeout=config.timeout,
            )
            response.raise_for_status()
            items = response.json().get("message", {}).get("items", [])
        candidates = []
        for item in items[: config.max_results]:
            candidates.append(
                {
                    "title": _clean_text(item.get("title", "")),
                    "authors": _crossref_authors(item),
                    "year": _first_year(item),
                    "venue": _clean_text(item.get("container-title", "")),
                    "abstract": _clean_text(item.get("abstract", "")),
                    "doi": str(item.get("DOI", "") or ""),
                    "url": str(item.get("URL", "") or ""),
                    "source": "crossref",
                    "confidence": 0.0,
                }
            )
        return {"provider": "crossref", "query": query, "candidates": candidates, "error": ""}
    except Exception as exc:
        return _empty_result("crossref", query, str(exc))


@tool
def search_openalex_paper(
    title: str = "",
    first_author: str = "",
    year: str = "",
    doi: str = "",
) -> dict[str, object]:
    """Search OpenAlex for paper metadata candidates."""
    config = get_paper_lookup_config()
    query = doi or title or " ".join(part for part in [first_author, year] if part)
    if not query:
        return _empty_result("openalex", query, "empty query")

    try:
        params = {"per-page": config.max_results}
        if doi:
            params["filter"] = f"doi:{doi}"
        else:
            params["search"] = query
        response = requests.get(
            "https://api.openalex.org/works",
            params=params,
            timeout=config.timeout,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        candidates = []
        for item in results[: config.max_results]:
            source = ((item.get("primary_location") or {}).get("source") or {}).get("display_name", "")
            if not source:
                source = (item.get("host_venue") or {}).get("display_name", "")
            candidates.append(
                {
                    "title": _clean_text(item.get("title", "")),
                    "authors": [
                        (authorship.get("author") or {}).get("display_name", "")
                        for authorship in item.get("authorships", []) or []
                        if (authorship.get("author") or {}).get("display_name")
                    ],
                    "year": str(item.get("publication_year", "") or ""),
                    "venue": _clean_text(source),
                    "abstract": _openalex_abstract(item.get("abstract_inverted_index")),
                    "doi": str(item.get("doi", "") or "").replace("https://doi.org/", ""),
                    "url": str(item.get("id", "") or ""),
                    "source": "openalex",
                    "confidence": 0.0,
                }
            )
        return {"provider": "openalex", "query": query, "candidates": candidates, "error": ""}
    except Exception as exc:
        return _empty_result("openalex", query, str(exc))


@tool
def search_arxiv_paper(title: str = "", arxiv_id: str = "") -> dict[str, object]:
    """Search arXiv for paper metadata candidates."""
    config = get_paper_lookup_config()
    if arxiv_id:
        params = {"id_list": arxiv_id, "max_results": config.max_results}
        query = arxiv_id
    elif title:
        params = {"search_query": f'ti:"{title}"', "max_results": config.max_results}
        query = title
    else:
        return _empty_result("arxiv", "", "empty query")

    try:
        response = requests.get(
            "http://export.arxiv.org/api/query",
            params=params,
            timeout=config.timeout,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
        candidates = []
        for entry in root.findall("atom:entry", ns)[: config.max_results]:
            published = _clean_text(entry.findtext("atom:published", default="", namespaces=ns))
            doi = _clean_text(entry.findtext("arxiv:doi", default="", namespaces=ns))
            candidates.append(
                {
                    "title": _clean_text(entry.findtext("atom:title", default="", namespaces=ns)),
                    "authors": [
                        _clean_text(author.findtext("atom:name", default="", namespaces=ns))
                        for author in entry.findall("atom:author", ns)
                    ],
                    "year": published[:4],
                    "venue": "arXiv",
                    "abstract": _clean_text(entry.findtext("atom:summary", default="", namespaces=ns)),
                    "doi": doi,
                    "url": _clean_text(entry.findtext("atom:id", default="", namespaces=ns)),
                    "source": "arxiv",
                    "confidence": 0.0,
                }
            )
        return {"provider": "arxiv", "query": query, "candidates": candidates, "error": ""}
    except Exception as exc:
        return _empty_result("arxiv", query, str(exc))

