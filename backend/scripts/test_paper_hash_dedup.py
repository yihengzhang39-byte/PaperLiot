"""Pure-local checks for SHA-256 PDF deduplication and stable paper ids."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import tempfile
from pathlib import Path

from app.repositories.paper_repository import PaperRepository
from app.services import file_service
from app.services import sqlite_service


class _Upload:
    def __init__(self, content: bytes, filename: str) -> None:
        self.filename = filename
        self.file = io.BytesIO(content)
        self.read_sizes: list[int] = []
        original_read = self.file.read

        def read(size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return original_read(size)

        self.file.read = read  # type: ignore[method-assign]


def _configure_storage(root: Path) -> None:
    paths = {
        "PAPERS_DIR": root / "papers",
        "NOTES_DIR": root / "notes",
        "PAPER_METADATA_DIR": root / "metadata",
        "PAPER_CHUNKS_DIR": root / "chunks",
        "PAPER_PARSE_CACHE_DIR": root / "parse_cache",
        "PAPER_SECTION_JSON_DIR": root / "sections",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        setattr(file_service, name, path)
    file_service.PAPER_INDEX_PATH = root / "paper_index.json"
    sqlite_service.DATABASE_PATH = root / "paperpilot.db"


def main() -> None:
    originals = {
        name: getattr(file_service, name)
        for name in (
            "PAPERS_DIR",
            "NOTES_DIR",
            "PAPER_METADATA_DIR",
            "PAPER_CHUNKS_DIR",
            "PAPER_PARSE_CACHE_DIR",
            "PAPER_SECTION_JSON_DIR",
            "PAPER_INDEX_PATH",
            "ensure_storage_dirs",
        )
    }
    original_database_path = sqlite_service.DATABASE_PATH
    file_service.ensure_storage_dirs = lambda: None
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _configure_storage(root)
            content_a, content_b = b"same-pdf-bytes", b"different-pdf-bytes"

            first = file_service.save_upload_pdf(_Upload(content_a, "foo.pdf"))
            paper_id = first["paper_id"]
            content_hash = hashlib.sha256(content_a).hexdigest()
            assert not first["reused"] and PaperRepository().find_by_hash(content_hash)["paper_id"] == paper_id

            duplicate = file_service.save_upload_pdf(_Upload(content_a, "bar.pdf"))
            assert duplicate["reused"] and duplicate["paper_id"] == paper_id
            assert len(list(file_service.PAPERS_DIR.glob("*.pdf"))) == 1

            different = file_service.save_upload_pdf(_Upload(content_b, "foo.pdf"))
            assert not different["reused"] and different["paper_id"] != paper_id

            file_service.save_paper_parse_cache(str(paper_id), "grobid", {"parser_name": "grobid"})
            persisted = file_service.save_upload_pdf(_Upload(content_a, "renamed.pdf"))
            assert persisted["reused"] and persisted["paper_id"] == paper_id
            assert file_service.read_paper_parse_cache(str(paper_id), "grobid") == {"parser_name": "grobid"}

            large_upload = _Upload(b"x" * (file_service.UPLOAD_CHUNK_SIZE + 1), "large.pdf")
            file_service.save_upload_pdf(large_upload)
            assert large_upload.read_sizes and all(size == file_service.UPLOAD_CHUNK_SIZE for size in large_upload.read_sizes)

            concurrent_content = b"concurrent-pdf"
            with ThreadPoolExecutor(max_workers=2) as pool:
                concurrent = list(
                    pool.map(lambda _: file_service.save_upload_pdf(_Upload(concurrent_content, "same.pdf")), range(2))
                )
            assert concurrent[0]["paper_id"] == concurrent[1]["paper_id"]
            assert sorted(item["reused"] for item in concurrent) == [False, True]

            _configure_storage(root / "backfill")
            old_id = "a" * 32
            (file_service.PAPERS_DIR / f"{old_id}_legacy.pdf").write_bytes(content_a)
            backfilled = file_service.save_upload_pdf(_Upload(content_a, "new-name.pdf"))
            assert backfilled["reused"] and backfilled["paper_id"] == old_id

            _configure_storage(root / "stale")
            stale_id = "b" * 32
            PaperRepository().create(stale_id, content_hash, "gone.pdf", "/missing/gone.pdf")
            recovered = file_service.save_upload_pdf(_Upload(content_a, "foo.pdf"))
            assert not recovered["reused"] and recovered["paper_id"] != stale_id

            _configure_storage(root / "failure")
            original_metadata_saver = file_service.save_paper_metadata
            file_service.save_paper_metadata = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("metadata failed"))
            try:
                try:
                    file_service.save_upload_pdf(_Upload(content_a, "failure.pdf"))
                except OSError:
                    pass
                else:
                    raise AssertionError("Expected metadata failure")
                assert not list(file_service.PAPERS_DIR.glob(".upload-*.tmp")) and not list(file_service.PAPERS_DIR.glob("*.pdf"))
            finally:
                file_service.save_paper_metadata = original_metadata_saver

            html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
            assert "if (attachment.reused)" in html
    finally:
        for name, value in originals.items():
            setattr(file_service, name, value)
        sqlite_service.DATABASE_PATH = original_database_path

    print("ALL PAPER HASH DEDUP TESTS PASSED")


if __name__ == "__main__":
    main()
