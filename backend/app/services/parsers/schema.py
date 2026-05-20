"""Unified schema for PDF parser outputs."""

from typing import Any

from pydantic import BaseModel, Field


class ParsedSection(BaseModel):
    """A section parsed from a PDF."""

    title: str = ""
    text: str = ""
    level: int = 1
    source: str = ""
    page_start: int | None = None
    page_end: int | None = None
    start_char: int | None = None
    end_char: int | None = None


class ParsedReference(BaseModel):
    """A reference parsed from a PDF."""

    text: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: str | None = None


class ParsedPaper(BaseModel):
    """Unified PDF parser result."""

    parser_name: str
    raw_text: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    sections: list[ParsedSection] = Field(default_factory=list)
    references: list[ParsedReference] = Field(default_factory=list)
    markdown: str = ""
    parser_warnings: list[str] = Field(default_factory=list)
    parser_meta: dict[str, Any] = Field(default_factory=dict)
