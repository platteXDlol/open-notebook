"""Own PPTX/DOCX/PDF extraction (docs/plans/ingestion-fixes.md, AC1–AC5)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import docx
import pytest
from pptx import Presentation
from pptx.util import Inches

from open_notebook.utils.document_extraction import (
    extract_docx_text,
    extract_pdf_text,
    extract_pptx_text,
)


def _make_pptx(path: Path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Photosynthesis"
    slide.notes_slide.notes_text_frame.text = "chlorophyll absorbs light"
    table = slide.shapes.add_table(
        2, 2, Inches(1), Inches(2), Inches(4), Inches(1)
    ).table
    table.cell(0, 0).text = "Stage"
    table.cell(1, 0).text = "light reaction"
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(
        Inches(1), Inches(4), Inches(2), Inches(1)
    ).text = "text inside group"
    second = prs.slides.add_slide(prs.slide_layouts[1])
    second.shapes.title.text = "Calvin cycle"
    prs.save(str(path))


def _make_pdf(path: Path, pages: list[str]) -> None:
    """Write a minimal PDF; an empty string gives a page with no text layer."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    font_id = 3
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else ""
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_pptx_keeps_tables_groups_notes_and_slide_markers(tmp_path):
    path = tmp_path / "deck.pptx"
    _make_pptx(path)

    text = extract_pptx_text(str(path))

    for expected in (
        "[Slide 1]",
        "Photosynthesis",
        "light reaction",
        "text inside group",
        "chlorophyll absorbs light",
        "[Slide 2]",
        "Calvin cycle",
    ):
        assert expected in text
    assert (
        text.index("[Slide 1]") < text.index("Photosynthesis") < text.index("[Slide 2]")
    )


def test_docx_keeps_tables_in_order(tmp_path):
    path = tmp_path / "handout.docx"
    d = docx.Document()
    d.add_heading("Cell biology", 1)
    d.add_paragraph("before table")
    d.add_table(rows=1, cols=2).cell(0, 0).text = "ATP synthase"
    d.add_paragraph("after table")
    d.save(str(path))

    text = extract_docx_text(str(path))

    assert "# Cell biology" in text
    assert (
        text.index("before table")
        < text.index("ATP synthase")
        < text.index("after table")
    )


def test_pdf_marks_every_page(tmp_path):
    path = tmp_path / "script.pdf"
    _make_pdf(path, ["first page text", "second page text"])

    text = extract_pdf_text(str(path))

    assert text.index("[Page 1]") < text.index("first page text")
    assert text.index("[Page 2]") < text.index("second page text")


def test_pdf_without_text_layer_returns_empty(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_pdf(path, ["", ""])

    assert extract_pdf_text(str(path)).strip() == ""


# --- content_process integration -------------------------------------------


def _settings(engine: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        youtube_preferred_languages=None,
        default_content_processing_engine_url=None,
        default_content_processing_engine_doc=engine,
        docling_ocr=None,
        docling_formulas=None,
        docling_vision=None,
    )


async def _process(file_path: str, *, engine=None, docling=False):
    from open_notebook.graphs import source as source_graph

    with (
        patch.object(
            source_graph.ContentSettings,
            "get_instance",
            new=AsyncMock(return_value=_settings(engine)),
        ),
        patch.object(
            source_graph,
            "engine_runtime_missing",
            side_effect=lambda e: None
            if docling or e != "docling"
            else "OPEN_NOTEBOOK_ENABLE_DOCLING",
        ),
        patch.object(
            source_graph.ModelManager,
            "get_defaults",
            new=AsyncMock(
                return_value=SimpleNamespace(default_speech_to_text_model=None)
            ),
        ),
        patch.object(
            source_graph,
            "extract_content",
            new=AsyncMock(return_value=SimpleNamespace(title="cc", content="cc text")),
        ) as cc_extract,
        patch.object(source_graph, "UPLOADS_FOLDER", str(Path(file_path).parent)),
    ):
        result = await source_graph.content_process(
            {"content_state": {"file_path": file_path}}  # type: ignore[typeddict-item]
        )
    return result, cc_extract


@pytest.mark.asyncio
async def test_content_process_uses_own_pptx_extractor_without_docling(tmp_path):
    path = tmp_path / "deck.pptx"
    _make_pptx(path)

    result, cc_extract = await _process(str(path))

    cc_extract.assert_not_called()
    assert "chlorophyll absorbs light" in result["extraction"].content
    assert result["extraction"].title == "deck"


@pytest.mark.asyncio
async def test_content_process_keeps_content_core_docling_path(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_pdf(path, [""])

    result, cc_extract = await _process(str(path), docling=True)

    cc_extract.assert_awaited_once()
    assert result["extraction"].content == "cc text"


@pytest.mark.asyncio
async def test_scanned_pdf_without_docling_explains_how_to_enable_it(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_pdf(path, [""])

    with pytest.raises(ValueError, match="OPEN_NOTEBOOK_ENABLE_DOCLING"):
        await _process(str(path))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ext,modern", [(".ppt", ".pptx"), (".doc", ".docx"), (".xls", ".xlsx")]
)
async def test_legacy_office_formats_are_rejected_clearly(tmp_path, ext, modern):
    path = tmp_path / f"old{ext}"
    path.write_bytes(b"\xd0\xcf\x11\xe0")

    with pytest.raises(ValueError, match=modern):
        await _process(str(path))
