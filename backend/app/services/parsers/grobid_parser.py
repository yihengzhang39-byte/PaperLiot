"""GROBID parser adapter."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element

from app.core.config import get_pdf_parser_config
from app.services.parsers.base import BasePDFParser
from app.services.parsers.schema import ParsedPaper, ParsedReference, ParsedSection


TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
ZH_SECTION_KEYWORDS = [
    "摘要",
    "关键词",
    "引言",
    "绪论",
    "研究背景",
    "相关工作",
    "材料与方法",
    "实验材料与方法",
    "方法",
    "模型与方法",
    "实验",
    "实验设计",
    "结果",
    "结果与分析",
    "分析与讨论",
    "讨论",
    "结论",
    "结语",
    "致谢",
    "参考文献",
]
ZH_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百\d]+章\s*|"
    r"[一二三四五六七八九十]+[、.．]\s*|"
    r"[（(][一二三四五六七八九十\d]+[)）]\s*|"
    r"\d+(?:\.\d+)*[、.．)）]?\s*|"
    r"\d+(?:\.\d+)*\s+"
    r")"
)


def _text_content(element: Element | None) -> str:
    """Extract normalized text from an XML element."""
    if element is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(element.itertext())).strip()


def _local_name(tag: str) -> str:
    """Return the local XML tag name without namespace."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _post_pdf_to_grobid(pdf_path: str, process_url: str, timeout: int) -> str:
    """Upload a PDF to GROBID and return TEI XML text."""
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests dependency is required for GROBIDParser.") from exc

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    session = requests.Session()
    session.trust_env = False

    with path.open("rb") as file_obj:
        response = session.post(
            process_url,
            files={"input": (path.name, file_obj, "application/pdf")},
            data={
                "consolidateHeader": "0",
                "consolidateCitations": "0",
                "includeRawCitations": "1",
                "includeRawAffiliations": "1",
            },
            headers={"Accept": "application/xml"},
            timeout=timeout,
        )

    if response.status_code == 200:
        return response.text
    if response.status_code == 204:
        raise RuntimeError("GROBID returned 204: no content extracted")
    if response.status_code == 503:
        raise RuntimeError("GROBID service unavailable or busy")

    preview = response.text[:500].replace("\n", "\\n")
    raise RuntimeError(f"GROBID returned HTTP {response.status_code}: {preview}")


def _extract_authors(root: Element, ns: dict[str, str]) -> list[str]:
    """Extract author names from TEI header/title metadata."""
    authors: list[str] = []
    candidates = root.findall(".//tei:sourceDesc//tei:author", ns)
    candidates.extend(root.findall(".//tei:titleStmt//tei:author", ns))

    for author in candidates:
        name_parts: list[str] = []
        forename_values = [_text_content(item) for item in author.findall(".//tei:forename", ns)]
        surname_values = [_text_content(item) for item in author.findall(".//tei:surname", ns)]

        if forename_values or surname_values:
            name_parts.extend(value for value in forename_values if value)
            name_parts.extend(value for value in surname_values if value)
            name = " ".join(name_parts).strip()
        else:
            name = _text_content(author)

        if name and name not in authors:
            authors.append(name)

    return authors


def _extract_keywords(root: Element, ns: dict[str, str]) -> list[str]:
    """Extract available TEI keyword terms without treating their absence as failure."""
    keywords: list[str] = []
    for term in root.findall(".//tei:profileDesc//tei:keywords//tei:term", ns):
        value = _text_content(term)
        if value and value not in keywords:
            keywords.append(value)
    return keywords


def _extract_references(root: Element, ns: dict[str, str]) -> list[ParsedReference]:
    """Extract bibliography entries that GROBID made available in TEI."""
    references: list[ParsedReference] = []
    for entry in root.findall(".//tei:listBibl//tei:biblStruct", ns):
        title = _text_content(entry.find(".//tei:analytic/tei:title", ns))
        if not title:
            title = _text_content(entry.find(".//tei:monogr/tei:title", ns))
        authors: list[str] = []
        for author in entry.findall(".//tei:author", ns):
            forenames = [_text_content(item) for item in author.findall(".//tei:forename", ns)]
            surnames = [_text_content(item) for item in author.findall(".//tei:surname", ns)]
            name = " ".join(value for value in [*forenames, *surnames] if value) or _text_content(author)
            if name and name not in authors:
                authors.append(name)
        date = entry.find(".//tei:date", ns)
        year = ""
        if date is not None:
            year = str(date.get("when", "") or _text_content(date))[:4]
        text = _text_content(entry)
        if text:
            references.append(ParsedReference(text=text, title=title, authors=authors, year=year or None))
    return references


def _extract_sections_from_tei_divs(root: Element, ns: dict[str, str]) -> list[dict[str, object]]:
    """Extract section title/content blocks from TEI body divs."""
    body = root.find(".//tei:text//tei:body", ns)
    if body is None:
        return []

    sections: list[dict[str, object]] = []
    for div in body.findall(".//tei:div", ns):
        title = _text_content(div.find("./tei:head", ns))
        paragraphs = [_text_content(paragraph) for paragraph in div.findall(".//tei:p", ns)]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        text = "\n".join(paragraphs).strip()
        if not title and not text:
            continue

        sections.append(
            {
                "title": title,
                "text": text,
                "level": 1,
                "source": "grobid_tei_div",
            }
        )

    return sections


def _extract_body_blocks(root: Element, ns: dict[str, str]) -> list[str]:
    """Extract TEI body head/p blocks in document order."""
    body = root.find(".//tei:text//tei:body", ns)
    if body is None:
        return []

    blocks: list[str] = []
    for element in body.iter():
        if _local_name(element.tag) not in {"head", "p"}:
            continue
        text = _text_content(element)
        if text:
            blocks.append(text)
    return blocks


def _strip_heading_prefix(text: str) -> tuple[str, bool]:
    """Remove common Chinese/numbered heading prefixes."""
    stripped = text.strip()
    had_prefix = False
    while True:
        match = ZH_NUMBER_PREFIX_RE.match(stripped)
        if not match:
            break
        had_prefix = True
        stripped = stripped[match.end() :].strip()
    return stripped.strip(" ：:　"), had_prefix


def _looks_like_zh_section_heading(text: str) -> bool:
    """Return whether a short text block looks like a Chinese section heading."""
    value = text.strip()
    if not value:
        return False
    if len(value) > 100:
        return False
    if value.endswith(("。", "；", ";")):
        return False
    if re.match(r"(?i)^\s*(?:图\s*\d+|表\s*\d+|fig\.\s*\d+|table\s*\d+)", value):
        return False
    if len(re.findall(r"[，,；;。.!！？?]", value)) >= 2:
        return False
    if re.match(r"^\s*(?:本文|实验结果表明|结果表明|研究表明|我们|该研究|为了|通过)", value):
        return False

    normalized, had_prefix = _strip_heading_prefix(value)
    normalized_no_space = re.sub(r"\s+", "", normalized)
    if not normalized_no_space:
        return False
    if normalized_no_space in ZH_SECTION_KEYWORDS:
        return True

    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", normalized_no_space))
    if had_prefix and has_chinese and len(normalized_no_space) <= 30:
        return True
    return False


def _is_reference_heading(title: str) -> bool:
    """Return whether a heading means references."""
    normalized, _ = _strip_heading_prefix(title)
    return re.sub(r"\s+", "", normalized) == "参考文献"


def _heading_level(title: str) -> int:
    """Infer a simple section level from a heading prefix."""
    value = title.strip()
    match = re.match(r"^\s*\d+((?:\.\d+)*)", value)
    if match and match.group(1):
        return min(match.group(1).count(".") + 1, 6)
    if re.match(r"^\s*[（(][一二三四五六七八九十\d]+[)）]", value):
        return 2
    return 1


def _extract_sections_from_zh_headings(blocks: list[str]) -> list[dict[str, object]]:
    """Split body blocks into sections with Chinese heading fallback rules."""
    sections: list[dict[str, object]] = []
    current_title = "正文"
    current_level = 1
    current_blocks: list[str] = []
    in_references = False

    def flush_current() -> None:
        text = "\n".join(current_blocks).strip()
        if text or current_title != "正文":
            sections.append(
                {
                    "title": current_title,
                    "text": text,
                    "level": current_level,
                    "source": "grobid_zh_heading_fallback",
                }
            )

    for block in blocks:
        if not in_references and _looks_like_zh_section_heading(block):
            flush_current()
            current_title = block.strip()
            current_level = _heading_level(current_title)
            current_blocks = []
            in_references = _is_reference_heading(current_title)
            continue
        current_blocks.append(block)

    flush_current()
    return [
        section
        for section in sections
        if str(section.get("title", "")).strip() or str(section.get("text", "")).strip()
    ]


def _sections_look_usable(sections: list[dict[str, object]]) -> bool:
    """Return whether TEI div/head sections look good enough to trust."""
    if len(sections) < 3:
        return False
    titled_count = sum(1 for section in sections if str(section.get("title", "")).strip())
    total_text_length = sum(len(str(section.get("text", "")).strip()) for section in sections)
    return titled_count >= 2 and total_text_length > 1000


def _body_text_fallback(root: Element, ns: dict[str, str]) -> list[dict[str, object]]:
    """Build a single fallback section from body text."""
    body = root.find(".//tei:text//tei:body", ns)
    text = _text_content(body)
    if not text:
        return []
    return [
        {
            "title": "正文",
            "text": text,
            "level": 1,
            "source": "grobid_body_text_fallback",
        }
    ]


def _extract_sections(root: Element, ns: dict[str, str]) -> tuple[list[dict[str, object]], str]:
    """Extract sections with TEI div/head first and Chinese heading fallback."""
    tei_sections = _extract_sections_from_tei_divs(root, ns)
    if _sections_look_usable(tei_sections):
        return tei_sections, "tei_div_head"

    blocks = _extract_body_blocks(root, ns)
    zh_sections = _extract_sections_from_zh_headings(blocks)
    zh_content_length = sum(
        len(str(section.get("title", ""))) + len(str(section.get("text", "")))
        for section in zh_sections
    )
    if zh_sections and zh_content_length > 0:
        return zh_sections, "zh_heading_fallback"

    if tei_sections:
        return tei_sections, "tei_div_head_unusable"
    body_sections = _body_text_fallback(root, ns)
    if body_sections:
        return body_sections, "body_text_fallback"
    return [], "xml_itertext_fallback"


def _build_raw_text_from_sections(sections: list[dict[str, object]]) -> str:
    """Build legacy-compatible raw_text from section dictionaries."""
    blocks: list[str] = []
    for section in sections:
        title = str(section.get("title", "")).strip()
        text = str(section.get("text", "")).strip()
        block = "\n".join(part for part in [title, text] if part).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks).strip()


def _parse_tei_xml(tei_xml: str) -> dict[str, object]:
    """Parse GROBID TEI XML into PaperPilot parser fields."""
    try:
        root = ET.fromstring(tei_xml.encode("utf-8"))
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse GROBID TEI XML: {exc}") from exc

    title = _text_content(root.find(".//tei:titleStmt/tei:title", TEI_NS))
    authors = _extract_authors(root, TEI_NS)
    keywords = _extract_keywords(root, TEI_NS)
    abstract = _text_content(root.find(".//tei:profileDesc//tei:abstract", TEI_NS))
    if not abstract:
        abstract = _text_content(root.find(".//tei:abstract", TEI_NS))

    sections, section_extraction_method = _extract_sections(root, TEI_NS)
    references = _extract_references(root, TEI_NS)
    raw_text = _build_raw_text_from_sections(sections)

    body = root.find(".//tei:text//tei:body", TEI_NS)
    if not raw_text and body is not None:
        raw_text = _text_content(body)
    if not raw_text:
        raw_text = _text_content(root)
    raw_text = raw_text.strip()
    if not raw_text:
        raise RuntimeError("GROBID extracted empty raw_text")

    parsed_sections: list[ParsedSection] = []
    grobid_sections: dict[str, dict[str, object]] = {}
    section_titles: list[str] = []
    cursor = 0
    for index, section in enumerate(sections):
        title_text = str(section.get("title", "")).strip()
        section_text = str(section.get("text", "")).strip()
        source = str(section.get("source", "grobid")).strip() or "grobid"
        block = "\n".join(part for part in [title_text, section_text] if part).strip()
        start_char = raw_text.find(block, cursor) if block else -1
        if start_char == -1:
            start_char = None
            end_char = None
        else:
            end_char = start_char + len(block)
            cursor = end_char

        parsed_sections.append(
            ParsedSection(
                title=title_text,
                text=section_text,
                level=int(section.get("level", 1)),
                source=source,
                start_char=start_char,
                end_char=end_char,
            )
        )
        if title_text:
            section_titles.append(title_text)
        grobid_sections[str(index)] = {
            "title": title_text,
            "start": start_char,
            "end": end_char,
            "text_length": len(section_text),
            "source": source,
        }

    return {
        "title": title,
        "authors": authors,
        "keywords": keywords,
        "abstract": abstract,
        "raw_text": raw_text,
        "sections": parsed_sections,
        "references": references,
        "grobid_sections": grobid_sections,
        "section_titles": section_titles,
        "section_extraction_method": section_extraction_method,
    }


class GROBIDParser(BasePDFParser):
    """Parser adapter for a local GROBID service."""

    parser_name = "grobid"

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Parse a PDF through GROBID's processFulltextDocument endpoint."""
        config = get_pdf_parser_config()
        process_url = f"{config.grobid_base_url}/api/processFulltextDocument"

        warnings = ["GROBID service reachable"]
        tei_xml = _post_pdf_to_grobid(pdf_path, process_url, config.timeout)
        warnings.append("POST /api/processFulltextDocument success")

        parsed = _parse_tei_xml(tei_xml)
        warnings.append("TEI XML parsed")
        warnings.append(f"section_extraction_method={parsed.get('section_extraction_method')}")
        warnings.append(f"section_count={len(parsed.get('sections', []))}")
        if parsed.get("section_extraction_method") == "zh_heading_fallback":
            warnings.append("TEI div/head sections unusable; using Chinese heading fallback")

        raw_text = str(parsed["raw_text"]).strip()
        warnings.append(f"extracted raw_text_length={len(raw_text)}")

        return ParsedPaper(
            parser_name=self.parser_name,
            raw_text=raw_text,
            title=str(parsed.get("title", "")),
            authors=list(parsed.get("authors", [])),
            abstract=str(parsed.get("abstract", "")),
            sections=list(parsed.get("sections", [])),
            references=list(parsed.get("references", [])),
            parser_warnings=warnings,
            parser_meta={
                "pdf_path": str(Path(pdf_path)),
                "grobid_base_url": config.grobid_base_url,
                "process_fulltext_url": process_url,
                "raw_text_length": len(raw_text),
                "tei_xml_length": len(tei_xml),
                "section_count": len(parsed.get("sections", [])),
                "keywords": parsed.get("keywords", []),
                "reference_count": len(parsed.get("references", [])),
                "section_titles": parsed.get("section_titles", []),
                "section_extraction_method": parsed.get("section_extraction_method", ""),
                "title": parsed.get("title", ""),
                "authors": parsed.get("authors", []),
                "abstract_length": len(str(parsed.get("abstract", ""))),
                "grobid_sections": parsed.get("grobid_sections", {}),
            },
        )
