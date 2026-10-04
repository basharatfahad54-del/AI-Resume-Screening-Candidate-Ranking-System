"""Resume and job-description document text extraction.

Supports the formats named in the PRD: ``.pdf``, ``.docx`` and ``.txt``.

Design notes
------------
* Extraction is *fail soft*: a document that yields nothing raises
  ``DocumentParseError`` so the caller can mark the candidate ``failed``
  instead of crashing the upload batch.
* Every extractor is page/section bounded. A hostile 10 000 page PDF must not
  be able to exhaust memory.
* Output is always normalised through :mod:`app.nlp.cleaning` so downstream
  parsing sees the same canonical text regardless of source format.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.nlp import cleaning
from app.utils.storage import StorageError, read_stored_file

logger = get_logger(__name__)

MAX_PDF_PAGES = 40
MAX_DOCX_PARAGRAPHS = 4000
MAX_CHARACTERS = 400_000

# Guard against zip bombs in .docx / OOXML payloads.
MAX_DOCX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024


class DocumentParseError(Exception):
    """Raised when a document cannot be converted to usable text."""


@dataclass(slots=True)
class ExtractedDocument:
    text: str
    filename: str
    extension: str
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.text.strip()) < 40


# --------------------------------------------------------------------------- #
# Format specific extractors
# --------------------------------------------------------------------------- #
def _extract_pdf(data: bytes) -> tuple[str, int, list[str]]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DocumentParseError("PDF support requires the 'pypdf' package") from exc

    warnings: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 - pypdf raises broadly
                raise DocumentParseError("PDF is password protected") from exc
        pages = list(reader.pages)
    except DocumentParseError:
        raise
    except PdfReadError as exc:
        raise DocumentParseError(f"Corrupt or unreadable PDF: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - never leak a library traceback to the API
        logger.warning("PDF extraction failed: %s", exc)
        raise DocumentParseError("Could not read the PDF file") from exc

    if len(pages) > MAX_PDF_PAGES:
        warnings.append(f"Only the first {MAX_PDF_PAGES} pages were parsed")
        pages = pages[:MAX_PDF_PAGES]

    chunks: list[str] = []
    for number, page in enumerate(pages, start=1):
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - a single bad page must not kill the parse
            logger.warning("Skipping unreadable page %s: %s", number, exc)
            warnings.append(f"Page {number} could not be read")
    return "\n".join(chunks), len(pages), warnings


def _extract_docx(data: bytes) -> tuple[str, int, list[str]]:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DocumentParseError("DOCX support requires the 'python-docx' package") from exc

    warnings: list[str] = []
    try:
        with docx.Document(io.BytesIO(data)) as document:
            paragraphs = list(document.paragraphs)
            if len(paragraphs) > MAX_DOCX_PARAGRAPHS:
                warnings.append(f"Only the first {MAX_DOCX_PARAGRAPHS} paragraphs were parsed")
                paragraphs = paragraphs[:MAX_DOCX_PARAGRAPHS]
            lines = [paragraph.text for paragraph in paragraphs]
            for table in document.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        lines.append(" | ".join(cells))
    except Exception as exc:  # noqa: BLE001
        logger.warning("DOCX extraction failed: %s", exc)
        raise DocumentParseError("Could not read the DOCX file") from exc

    return "\n".join(lines), 0, warnings


def _extract_txt(data: bytes) -> tuple[str, int, list[str]]:
    for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), 0, []
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), 0, ["File was not valid UTF-8; text may contain artefacts"]


_EXTRACTORS = {".pdf": _extract_pdf, ".docx": _extract_docx, ".txt": _extract_txt}


def extract_text(
    data: bytes,
    filename: str,
    *,
    extension: str | None = None,
    kind: str = "resumes",
) -> ExtractedDocument:
    """Convert raw file bytes into cleaned plain text."""
    suffix = (extension or Path(filename).suffix).lower()
    extractor = _EXTRACTORS.get(suffix)
    if extractor is None:
        raise DocumentParseError(f"Unsupported file type '{suffix or 'unknown'}'")

    if not data:
        raise DocumentParseError("Uploaded file is empty")

    try:
        raw, page_count, warnings = extractor(data)
    except StorageError:
        raise
    except DocumentParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - defensive boundary
        logger.exception("Unexpected extraction failure for %s", filename)
        raise DocumentParseError(f"Could not extract text from '{filename}'") from exc

    text = cleaning.clean_document_text(raw)
    if len(text) > MAX_CHARACTERS:
        warnings.append("Text was truncated to 400k characters")
        text = text[:MAX_CHARACTERS]

    document = ExtractedDocument(
        text=text,
        filename=filename,
        extension=suffix,
        page_count=page_count,
        warnings=warnings,
    )
    if document.is_empty:
        raise DocumentParseError(
            "No readable text found. If this is a scanned document, "
            "it must be OCR'd before upload."
        )
    return document


def extract_stored(relative_path: str, *, filename: str = "", kind: str = "resumes") -> ExtractedDocument:
    """Extract text from a document already written to secure storage."""
    data = read_stored_file(relative_path, kind=kind)
    return extract_text(data, filename or Path(relative_path).name, kind=kind)
