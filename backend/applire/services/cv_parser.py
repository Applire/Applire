# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""CV parsing layer — format detection, text extraction (ADR 014, iter 12).

Supported formats: PDF (text + OCR fallback), DOCX, image (JPEG/PNG/TIFF), plain text.
PDF text extraction uses pypdf, which produces compact logical-order text similar to
pdftotext — avoiding the spatial whitespace that pymupdf adds for multi-column layouts
(e.g. FlowCV, LinkedIn PDF exports).  pymupdf is retained solely for the OCR fallback
path where it renders scanned pages to PNG for the OCR extractor.
"""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from applire.ocr.base import CVImageExtractor

try:
    import fitz  # pymupdf — used only for OCR page rendering; tests can patch this
except ImportError:
    fitz = None  # type: ignore[assignment]

CVFormat = Literal["pdf", "docx", "image", "text"]

_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "image/webp",
}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}
_DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_DOCX_EXTENSIONS = {".docx", ".doc"}

_MIN_PDF_TEXT_CHARS = 50  # below this threshold → treat as scanned, use OCR


def detect_format(filename: str, content_type: str) -> CVFormat:
    """Determine the CV format from filename extension and/or MIME type."""
    name_lower = filename.lower()
    ext = "." + name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
    mime = content_type.lower().split(";")[0].strip()

    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in _DOCX_EXTENSIONS or mime in _DOCX_MIME_TYPES:
        return "docx"
    if ext in _IMAGE_EXTENSIONS or mime in _IMAGE_MIME_TYPES:
        return "image"
    if mime.startswith("text/") or ext in {".txt", ".md"}:
        return "text"
    # Unknown extension — let content sniffing decide at extraction time
    return "text"


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF using pypdf.

    pypdf produces compact, logical-order text (similar to pdftotext) without the
    spatial whitespace padding that pymupdf adds for multi-column layouts.  This keeps
    token counts low for the LLM extraction step.

    Returns an empty string if the PDF has no text layer (scanned).
    Caller is responsible for invoking OCR fallback when the result is empty.
    """
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(file_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from a DOCX/DOC file, tables and text boxes included.

    Delegates to the E057/ADR-079 document-order walker
    (``office_export.extract.extract_docx_text``) rather than keeping a second
    reader — ADR-066, one logical operation, one implementation.

    This function previously read ``document.paragraphs`` alone. Word table
    cells are not paragraphs of the document body, so a *tabellarischer
    Lebenslauf* — the standard DACH layout, which puts Berufserfahrung and
    Ausbildung in tables — lost its entire employment and education history
    before the extraction LLM was ever called (**#640**: 9 of 9 facts, while the
    section headings survived and made the result look structured). Text boxes
    were invisible to that reader for the same reason.

    The blank-line normalisation below is the paragraphs reader's own, kept
    deliberately: the walker newline-terminates every paragraph including empty
    ones, and this path feeds the extraction prompts, so the whitespace shape
    stays exactly what it was. Measured delta on a table-free document: a
    literal tab becomes one space. See
    ``tests/unit/test_cv_parser_docx_640.py``.
    """
    try:
        from applire.services.office_export.extract import extract_docx_text
    except ImportError as exc:
        raise RuntimeError(
            "DOCX extraction requires 'python-docx'. Add it to requirements.txt."
        ) from exc

    lines = extract_docx_text(file_bytes).split("\n")
    return "\n".join(line for line in lines if line.strip()).strip()


async def extract_text(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    ocr_extractor: CVImageExtractor,
) -> str:
    """Top-level dispatcher: detect format and return extracted text.

    For PDFs with a text layer, pymupdf is used directly.
    For scanned PDFs (< _MIN_PDF_TEXT_CHARS), OCR fallback is invoked.
    """
    if not file_bytes:
        raise ValueError("Empty file — nothing to extract")

    fmt = detect_format(filename, content_type)

    if fmt == "pdf":
        text = extract_text_from_pdf(file_bytes)
        if len(text) >= _MIN_PDF_TEXT_CHARS:
            return text
        # Scanned PDF — render first page as image and run OCR
        text = await _ocr_pdf(file_bytes, ocr_extractor)
        if not text:
            raise ValueError("Could not extract text from PDF (no text layer, OCR yielded nothing)")
        return text

    if fmt == "docx":
        text = extract_text_from_docx(file_bytes)
        if not text:
            raise ValueError("Could not extract text from DOCX file")
        return text

    if fmt == "image":
        text = await ocr_extractor.extract(file_bytes, content_type)
        if not text:
            raise ValueError("OCR returned no text from image")
        return text

    # Plain text — decode as UTF-8, fall back to latin-1
    try:
        return file_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1").strip()


async def _ocr_pdf(file_bytes: bytes, ocr_extractor: CVImageExtractor) -> str:
    """Render a scanned PDF to PNG images and run OCR on each page."""
    if fitz is None:
        raise RuntimeError("pymupdf required for scanned PDF OCR")

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    texts: list[str] = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        png_bytes = pix.tobytes("png")
        page_text = await ocr_extractor.extract(png_bytes, "image/png")
        if page_text:
            texts.append(page_text)
    doc.close()
    return "\n".join(texts).strip()
