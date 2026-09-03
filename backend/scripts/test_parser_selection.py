"""Pure-local checks that parser selection has no language router or fallback."""

from app.agents.nodes import pdf_parse_node
from app.services import parser_service
from app.services.parsers.grobid_parser import _parse_tei_xml
from app.services.parsers.schema import ParsedPaper


def main() -> None:
    tei = """<TEI xmlns=\"http://www.tei-c.org/ns/1.0\"><teiHeader><fileDesc><titleStmt><title>Test Paper</title><author><forename>Ada</forename><surname>Lovelace</surname></author></titleStmt></fileDesc><profileDesc><abstract>Abstract text</abstract><textClass><keywords><term>agents</term></keywords></textClass></profileDesc></teiHeader><text><body><div><head>1 Method</head><p>Method text</p></div></body><back><listBibl><biblStruct><analytic><title>Reference Title</title><author><forename>Grace</forename><surname>Hopper</surname></author></analytic><imprint><date when=\"2024\"/></imprint></biblStruct></listBibl></back></text></TEI>"""
    tei_result = _parse_tei_xml(tei)
    assert tei_result["title"] == "Test Paper" and tei_result["keywords"] == ["agents"]
    assert tei_result["references"][0].title == "Reference Title"

    original_grobid = parser_service.PARSER_REGISTRY["grobid"]

    class BrokenGROBID:
        def parse(self, _pdf_path: str):
            raise RuntimeError("GROBID unavailable")

    parser_service.PARSER_REGISTRY["grobid"] = BrokenGROBID
    try:
        try:
            parser_service.parse_pdf("unused.pdf", parser_name="grobid")
        except RuntimeError as exc:
            assert "GROBID unavailable" in str(exc)
        else:
            raise AssertionError("GROBID failure must not fall back to PyMuPDF")
    finally:
        parser_service.PARSER_REGISTRY["grobid"] = original_grobid

    original_parse = pdf_parse_node.parse_pdf
    calls: list[str] = []
    pdf_parse_node.parse_pdf = lambda path: calls.append(path) or ParsedPaper(
        parser_name="configured_parser",
        raw_text="local text",
        parser_meta={"requested_parser": "configured_parser"},
    )
    try:
        result = pdf_parse_node.pdf_parse_node({"pdf_path": "local.pdf", "paper_language": "en"})
        assert calls == ["local.pdf"]
        assert result["paper_language"] == "en"
        assert result["requested_parser"] == "configured_parser"
    finally:
        pdf_parse_node.parse_pdf = original_parse

    print("ALL PARSER SELECTION TESTS PASSED")


if __name__ == "__main__":
    main()
