"""PyMuPDF parser adapter."""

from pathlib import Path
import re
from statistics import median

import fitz

from app.services.parsers.base import BasePDFParser
from app.services.parsers.schema import ParsedPaper


ZH_TITLE_BLOCKLIST = [
    "摘要",
    "关键词",
    "abstract",
    "keywords",
    "引言",
    "基金项目",
    "作者简介",
    "doi",
    "中图分类号",
    "文献标识码",
    "收稿日期",
    "通讯作者",
    "email",
    "http",
    "www",
]

ZH_AUTHOR_UNIT_WORDS = [
    "大学",
    "学院",
    "实验室",
    "研究所",
    "公司",
    "省",
    "市",
    "邮编",
    "基金",
    "通讯作者",
    "医院",
    "中心",
    "单位",
]

ZH_STRONG_AFFILIATION_WORDS = [
    "大学",
    "学院",
    "实验室",
    "研究所",
    "公司",
    "医院",
    "邮编",
]

ZH_JOURNAL_HEADER_WORDS = [
    "科技",
    "学报",
    "杂志",
    "期刊",
    "研究",
    "进展",
    "中国",
    "china",
    "journal",
    "science",
    "technology",
]

ZH_BODY_SENTENCE_WORDS = [
    "本文",
    "本研究",
    "通过",
    "采用",
    "提出",
    "实现",
    "结果表明",
    "实验结果",
    "研究表明",
    "表明",
]


def _clean_line(text: str) -> str:
    """Normalize one extracted text line."""
    return re.sub(r"\s+", " ", text or "").strip()


def _first_page_lines(first_page_text: str) -> list[str]:
    """Return non-empty first-page lines."""
    return [_clean_line(line) for line in first_page_text.splitlines() if _clean_line(line)]


def _is_bad_zh_title_line(line: str) -> bool:
    """Return whether a line is clearly not a Chinese paper title."""
    lower = line.lower()
    if any(keyword in lower for keyword in ZH_TITLE_BLOCKLIST):
        return True
    if re.search(r"[@:/\\]|第\s*\d+\s*页", line):
        return True
    if len(re.findall(r"[，,；;。.!！?？：:]", line)) > 3:
        return True
    return False


def _looks_like_zh_title_line(line: str) -> bool:
    """Return whether a line looks like a Chinese title candidate."""
    cleaned = re.sub(r"\s+", "", line)
    zh_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    if zh_count < 6 or zh_count > 80:
        return False
    if len(cleaned) > 120:
        return False
    if _is_bad_zh_title_line(cleaned):
        return False
    return True


def _clamp_score(value: float) -> float:
    """Clamp confidence score to [0, 1]."""
    return round(max(0.0, min(1.0, value)), 4)


def _layout_lines_from_page(page) -> tuple[list[dict[str, object]], float]:
    """Extract visual text lines from a PyMuPDF page dict."""
    page_dict = page.get_text("dict")
    font_sizes: list[float] = []
    lines: list[dict[str, object]] = []
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = _clean_line("".join(str(span.get("text", "")) for span in spans))
            if not text:
                continue
            sizes = [float(span.get("size", 0.0) or 0.0) for span in spans if span.get("size")]
            if not sizes:
                continue
            font_sizes.extend(sizes)
            bbox = line.get("bbox") or block.get("bbox") or [0, 0, 0, 0]
            lines.append(
                {
                    "text": text,
                    "font_size": max(sizes),
                    "bbox": [float(value) for value in bbox],
                    "page_width": page_width,
                    "page_height": page_height,
                }
            )

    body_font_size = median(font_sizes) if font_sizes else 10.0
    return lines, float(body_font_size)


def _score_zh_title_layout_line(line: dict[str, object], body_font_size: float) -> dict[str, object] | None:
    """Score one visual line as a Chinese title candidate."""
    text = re.sub(r"\s+", "", str(line.get("text", "") or ""))
    if not text:
        return None
    bbox = list(line.get("bbox", [0, 0, 0, 0]))
    page_width = float(line.get("page_width", 1.0) or 1.0)
    page_height = float(line.get("page_height", 1.0) or 1.0)
    x0, y0, x1, y1 = [float(value) for value in bbox]
    y_center_ratio = ((y0 + y1) / 2) / page_height
    center_offset = abs(((x0 + x1) / 2) - (page_width / 2)) / max(page_width / 2, 1.0)
    font_size = float(line.get("font_size", 0.0) or 0.0)
    zh_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    visible_length = len(text)
    zh_ratio = zh_count / max(visible_length, 1)
    reasons: list[str] = []
    score = 0.0

    if zh_count < 6:
        reasons.append("too_few_chinese_chars")
        return None
    if visible_length > 120 or zh_count > 80:
        reasons.append("too_long")
        return None
    if re.fullmatch(r"[\d\s.\-—–]+", text):
        reasons.append("pure_number")
        return None
    if _is_bad_zh_title_line(text):
        reasons.append("contains_non_title_keyword")
        score -= 0.45
    if "。" in text or text.endswith(("，", ",", "；", ";")):
        reasons.append("looks_like_sentence")
        score -= 0.25
    if any(word in text for word in ZH_BODY_SENTENCE_WORDS):
        reasons.append("contains_body_sentence_word")
        score -= 0.25
    if any(word in text for word in ZH_AUTHOR_UNIT_WORDS):
        reasons.append("contains_unit_word")
        score -= 0.12
    if any(word in text for word in ZH_STRONG_AFFILIATION_WORDS) and re.search(r"[()（）]|\d{5,}", text):
        reasons.append("looks_like_affiliation")
        score -= 0.45
    if y_center_ratio < 0.035 or y_center_ratio > 0.55:
        reasons.append("outside_main_title_region")
        score -= 0.25
    elif y_center_ratio <= 0.45:
        reasons.append("upper_page")
        score += 0.24
    if font_size >= body_font_size + 2:
        reasons.append("larger_than_body")
        score += 0.28
    elif font_size >= body_font_size + 0.8:
        reasons.append("slightly_larger_than_body")
        score += 0.16
    else:
        reasons.append("body_sized_text")
        score -= 0.22
    if center_offset <= 0.18:
        reasons.append("centered")
        score += 0.2
    elif center_offset <= 0.32:
        reasons.append("near_center")
        score += 0.1
    else:
        score -= 0.12
    if 10 <= zh_count <= 45:
        reasons.append("good_length")
        score += 0.18
    elif 6 <= zh_count < 10 or 45 < zh_count <= 80:
        score += 0.08
    if zh_ratio >= 0.55:
        reasons.append("high_chinese_ratio")
        score += 0.14
    if y_center_ratio < 0.08 and any(word in text.lower() for word in ZH_JOURNAL_HEADER_WORDS):
        reasons.append("possible_journal_header")
        score -= 0.25

    return {
        "text": text[:120],
        "score": _clamp_score(score),
        "font_size": round(font_size, 2),
        "bbox": [round(float(value), 2) for value in bbox],
        "page_width": round(page_width, 2),
        "page_height": round(page_height, 2),
        "center_offset": round(center_offset, 4),
        "reasons": reasons,
    }


def _merge_zh_title_candidate_lines(
    first: dict[str, object],
    second: dict[str, object],
    body_font_size: float,
) -> dict[str, object] | None:
    """Merge two adjacent visual lines into one title candidate when plausible."""
    text = f"{first.get('text', '')}{second.get('text', '')}"
    if not _looks_like_zh_title_line(text):
        return None
    first_bbox = list(first.get("bbox", [0, 0, 0, 0]))
    second_bbox = list(second.get("bbox", [0, 0, 0, 0]))
    first_font = float(first.get("font_size", 0.0) or 0.0)
    second_font = float(second.get("font_size", 0.0) or 0.0)
    if abs(first_font - second_font) > max(1.8, first_font * 0.18):
        return None
    vertical_gap = float(second_bbox[1]) - float(first_bbox[3])
    if vertical_gap < -2 or vertical_gap > max(first_font, second_font) * 1.8:
        return None
    page_width = float(first.get("page_width", 1.0) or 1.0)
    center_1 = (float(first_bbox[0]) + float(first_bbox[2])) / 2
    center_2 = (float(second_bbox[0]) + float(second_bbox[2])) / 2
    if abs(center_1 - center_2) / max(page_width, 1.0) > 0.18:
        return None

    bbox = [
        min(float(first_bbox[0]), float(second_bbox[0])),
        min(float(first_bbox[1]), float(second_bbox[1])),
        max(float(first_bbox[2]), float(second_bbox[2])),
        max(float(first_bbox[3]), float(second_bbox[3])),
    ]
    merged_line = {
        "text": text,
        "font_size": max(first_font, second_font),
        "bbox": bbox,
        "page_width": first.get("page_width", 1.0),
        "page_height": first.get("page_height", 1.0),
    }
    candidate = _score_zh_title_layout_line(merged_line, body_font_size)
    if candidate is None:
        return None
    candidate["score"] = _clamp_score(float(candidate["score"]) + 0.08)
    candidate["reasons"] = list(candidate.get("reasons", [])) + ["merged_two_lines"]
    return candidate


def _merge_zh_title_candidate_line_group(
    group: list[dict[str, object]],
    body_font_size: float,
) -> dict[str, object] | None:
    """Merge two or three adjacent visual lines into one title candidate."""
    if len(group) < 2:
        return None
    text = "".join(str(item.get("text", "")) for item in group)
    if not _looks_like_zh_title_line(text):
        return None

    bboxes = [list(item.get("bbox", [0, 0, 0, 0])) for item in group]
    fonts = [float(item.get("font_size", 0.0) or 0.0) for item in group]
    page_width = float(group[0].get("page_width", 1.0) or 1.0)
    page_height = float(group[0].get("page_height", 1.0) or 1.0)
    centers = [(float(bbox[0]) + float(bbox[2])) / 2 for bbox in bboxes]
    if max(centers) - min(centers) > page_width * 0.2:
        return None
    if max(fonts) < body_font_size + 2:
        return None
    for index in range(len(bboxes) - 1):
        vertical_gap = float(bboxes[index + 1][1]) - float(bboxes[index][3])
        if vertical_gap < -2 or vertical_gap > max(fonts) * 1.9:
            return None

    bbox = [
        min(float(bbox[0]) for bbox in bboxes),
        min(float(bbox[1]) for bbox in bboxes),
        max(float(bbox[2]) for bbox in bboxes),
        max(float(bbox[3]) for bbox in bboxes),
    ]
    merged_line = {
        "text": text,
        "font_size": max(fonts),
        "bbox": bbox,
        "page_width": page_width,
        "page_height": page_height,
    }
    candidate = _score_zh_title_layout_line(merged_line, body_font_size)
    if candidate is None:
        return None
    boost = 0.08 if len(group) == 2 else 0.14
    candidate["score"] = _clamp_score(float(candidate["score"]) + boost)
    candidate["reasons"] = list(candidate.get("reasons", [])) + [f"merged_{len(group)}_lines"]
    return candidate


def _extract_zh_title_candidates_from_layout(page) -> list[dict[str, object]]:
    """Extract Chinese title candidates from first-page visual layout."""
    try:
        lines, body_font_size = _layout_lines_from_page(page)
    except Exception:
        return []

    scored: list[dict[str, object]] = []
    visual_lines = sorted(lines, key=lambda item: (float(item["bbox"][1]), float(item["bbox"][0])))
    for line in visual_lines:
        candidate = _score_zh_title_layout_line(line, body_font_size)
        if candidate is not None:
            scored.append(candidate)

    for group_size in (2, 3):
        for index in range(len(visual_lines) - group_size + 1):
            merged = _merge_zh_title_candidate_line_group(visual_lines[index : index + group_size], body_font_size)
            if merged is not None:
                scored.append(merged)

    deduped: dict[str, dict[str, object]] = {}
    for candidate in scored:
        text = str(candidate.get("text", ""))
        if not text:
            continue
        existing = deduped.get(text)
        if existing is None or float(candidate.get("score", 0.0)) > float(existing.get("score", 0.0)):
            deduped[text] = candidate

    return sorted(
        deduped.values(),
        key=lambda item: (float(item.get("score", 0.0)), len(str(item.get("text", "")))),
        reverse=True,
    )[:5]


def _split_author_names(line: str) -> list[str]:
    """Split a Chinese author line into names."""
    normalized = re.sub(r"[\d*＊#（）()]+", " ", line)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized)
    parts = re.split(r"[\s,，、;；]+", normalized)
    authors = []
    for part in parts:
        name = part.strip()
        if re.fullmatch(r"[\u4e00-\u9fff]{2,5}", name):
            authors.append(name)
    return authors


def _extract_zh_authors_from_first_page(first_page_text: str, title: str) -> list[str]:
    """Extract Chinese author candidates from first-page text."""
    lines = _first_page_lines(first_page_text)
    if not lines:
        return []

    abstract_index = next(
        (index for index, line in enumerate(lines) if re.match(r"^\s*摘\s*要\s*[:：]?", line)),
        min(len(lines), 30),
    )
    title_index = 0
    if title:
        compact_title = re.sub(r"\s+", "", title)
        for index, line in enumerate(lines[:abstract_index]):
            if compact_title in re.sub(r"\s+", "", line) or re.sub(r"\s+", "", line) in compact_title:
                title_index = index
                break

    for line in lines[title_index + 1 : abstract_index]:
        if any(word in line for word in ZH_AUTHOR_UNIT_WORDS):
            continue
        authors = _split_author_names(line)
        if 1 <= len(authors) <= 12 and len("".join(authors)) >= 2:
            return authors
    return []


def _extract_zh_abstract_and_keywords(raw_text: str) -> tuple[str, list[str]]:
    """Extract Chinese abstract and keywords from raw text."""
    text = re.sub(r"\r\n?", "\n", raw_text or "")
    abstract_match = re.search(
        r"摘\s*要\s*[:：]?\s*(.*?)(?=\n?\s*(?:关\s*键\s*词|关键词|Abstract|ABSTRACT|引言|1\s*引言|一[、.．]\s*引言))",
        text,
        re.DOTALL,
    )
    abstract = ""
    if abstract_match:
        abstract = re.sub(r"\s+", " ", abstract_match.group(1)).strip()
        if len(abstract) < 30:
            abstract = ""
        elif len(abstract) > 1500:
            abstract = abstract[:1500]

    keywords: list[str] = []
    keyword_match = re.search(
        r"关\s*键\s*词\s*[:：;；]?\s*(.*?)(?=\n?\s*(?:Abstract|ABSTRACT|引言|1\s*引言|一[、.．]\s*引言|中图分类号|文献标识码))",
        text,
        re.DOTALL,
    )
    if keyword_match:
        keyword_text = keyword_match.group(1).splitlines()[0].strip()
        keywords = [
            item.strip()
            for item in re.split(r"[；;，,\s]+", keyword_text)
            if item.strip()
            and len(item.strip()) <= 30
            and (re.search(r"[\u4e00-\u9fff]", item) or "-" in item)
        ][:12]
    return abstract, keywords


def _extract_zh_metadata(
    first_page_text: str,
    raw_text: str,
    title_candidates: list[dict[str, object]],
) -> dict[str, object]:
    """Extract Chinese metadata candidates using conservative local rules."""
    best_title = title_candidates[0] if title_candidates else {}
    title = str(best_title.get("text", "") or "")
    title_confidence = float(best_title.get("score", 0.0) or 0.0)
    authors = _extract_zh_authors_from_first_page(first_page_text, title)
    abstract, keywords = _extract_zh_abstract_and_keywords(raw_text)
    return {
        "candidate_title": title,
        "candidate_title_confidence": title_confidence,
        "candidate_title_candidates": title_candidates[:5],
        "candidate_authors": authors,
        "candidate_abstract": abstract,
        "candidate_keywords": keywords,
        "candidate_title_source": "pymupdf_first_page_layout" if title else "",
        "candidate_authors_source": "pymupdf_zh_first_page_rule" if authors else "",
        "candidate_abstract_source": "pymupdf_zh_abstract_rule" if abstract else "",
        "first_page_text": first_page_text[:3000],
        "zh_metadata_extract_debug": {
            "first_page_length": len(first_page_text),
            "title_found": bool(title),
            "title_candidate_count": len(title_candidates),
            "title_confidence": title_confidence,
            "authors_found": bool(authors),
            "abstract_found": bool(abstract),
            "keyword_count": len(keywords),
        },
    }


class PyMuPDFParser(BasePDFParser):
    """Parse PDFs with PyMuPDF while preserving legacy raw_text behavior."""

    parser_name = "pymupdf"

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Extract raw text page by page with PyMuPDF."""
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        try:
            pages: list[str] = []
            first_page_text = ""
            title_candidates: list[dict[str, object]] = []
            with fitz.open(path) as doc:
                page_count = doc.page_count
                for page_index, page in enumerate(doc, start=1):
                    text = page.get_text("text")
                    if page_index == 1:
                        first_page_text = text or ""
                        title_candidates = _extract_zh_title_candidates_from_layout(page)
                    pages.append(f"\n\n--- Page {page_index} ---\n\n{text}")

            raw_text = "".join(pages).strip()
            zh_meta = _extract_zh_metadata(first_page_text, raw_text, title_candidates)
            return ParsedPaper(
                parser_name=self.parser_name,
                raw_text=raw_text,
                parser_meta={
                    "page_count": page_count,
                    "raw_text_length": len(raw_text),
                    **zh_meta,
                },
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to parse PDF with PyMuPDF '{pdf_path}': {exc}") from exc
