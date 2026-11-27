"""Own text extraction for PPTX, DOCX and PDF (docs/plans/ingestion-fixes.md).

content-core's "simple" extractors drop PPTX tables, grouped shapes and speaker
notes, DOCX tables, and PDF page boundaries. These replacements keep all text and
mark every slide/page (``[Slide N]`` / ``[Page N]``) so chunks and citations can
point at a position. Docling (opt-in, ADR-007) still takes over when available.
"""

from pathlib import Path
from typing import Callable, Optional

import docx
import pdfplumber
from content_core.processors.document.pdf import convert_table_to_markdown
from docx.table import Table
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

LEGACY_OFFICE_FORMATS = {".ppt": ".pptx", ".doc": ".docx", ".xls": ".xlsx"}

SCANNED_PDF_MESSAGE = (
    "This PDF has no text layer — it is probably a scan. To read scanned pages, "
    "the server operator must enable Docling (OPEN_NOTEBOOK_ENABLE_DOCLING=true, "
    "see ADR-007) and keep the document engine on 'auto' or 'docling'."
)


def legacy_format_message(file_path: str) -> Optional[str]:
    """User-facing rejection message for old binary Office files, else None."""
    ext = Path(file_path).suffix.lower()
    modern = LEGACY_OFFICE_FORMATS.get(ext)
    if not modern:
        return None
    return (
        f"Old {ext} files are not supported. Open the file and save it as "
        f"{modern}, then upload it again."
    )


def _markdown_table(rows: list[list[str]]) -> str:
    return convert_table_to_markdown(rows) if rows else ""


def _pptx_shape_texts(shapes) -> list[str]:
    texts: list[str] = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            texts.extend(_pptx_shape_texts(shape.shapes))
        elif getattr(shape, "has_table", False) and shape.has_table:
            rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
            texts.append(_markdown_table(rows))
        elif getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            texts.append(shape.text_frame.text.strip())
    return texts


def extract_pptx_text(file_path: str) -> str:
    parts: list[str] = []
    for number, slide in enumerate(Presentation(file_path).slides, 1):
        parts.append(f"[Slide {number}]")
        title = slide.shapes.title
        if title is not None and title.text.strip():
            parts.append(f"## {title.text.strip()}")
        others = [
            s for s in slide.shapes if title is None or s.shape_id != title.shape_id
        ]
        parts.extend(t for t in _pptx_shape_texts(others) if t)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                parts.append(f"Speaker notes: {notes.text.strip()}")
    return "\n\n".join(parts)


def extract_docx_text(file_path: str) -> str:
    parts: list[str] = []
    for block in docx.Document(file_path).iter_inner_content():
        if isinstance(block, Table):
            rows = [[cell.text for cell in row.cells] for row in block.rows]
            parts.append(_markdown_table(rows))
            continue
        text = block.text.strip()
        if not text:
            continue
        style = (block.style.name if block.style is not None else "") or ""
        level = style.removeprefix("Heading ").strip()
        parts.append(f"{'#' * int(level)} {text}" if level.isdigit() else text)
    return "\n\n".join(p for p in parts if p)


def extract_pdf_text(file_path: str) -> str:
    """Page-marked text plus tables; empty string when no page has a text layer."""
    pages: list[str] = []
    has_text = False
    with pdfplumber.open(file_path) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            body = (page.extract_text() or "").strip()
            tables = [
                _markdown_table([[str(c or "") for c in row] for row in t if row])
                for t in page.extract_tables()
            ]
            body = "\n\n".join([body, *tables]).strip()
            has_text = has_text or bool(body)
            pages.append(f"[Page {number}]\n\n{body}")
    return "\n\n".join(pages) if has_text else ""


EXTRACTORS: dict[str, Callable[[str], str]] = {
    ".pptx": extract_pptx_text,
    ".docx": extract_docx_text,
    ".pdf": extract_pdf_text,
}
