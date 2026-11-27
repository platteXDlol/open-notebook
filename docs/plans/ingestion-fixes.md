# Ingestion fixes (C1–C5 of the NotebookLM gap analysis)

Decisions: `docs/BRIEF.md` Decision Log and `docs/plans/notebooklm-gap-analysis.md`.

**Goal:** As a student, I want my PPTX, DOCX and PDF files read completely and with page/slide numbers,
so that answers and citations are based on everything in my material.

## Acceptance criteria
1. Given a PPTX with tables, grouped text boxes and speaker notes, when it's processed, then all that text is in the source, each slide marked `[Slide N]`.
2. Given a DOCX with tables, when it's processed, then the table contents are in the source.
3. Given a multi-page PDF, when it's processed, then every page is marked `[Page N]`, and every chunk created for search carries the marker of the page/slide it comes from.
4. Given a `.ppt`, `.doc` or `.xls` file, when it's uploaded, then it is rejected with a clear message ("save it as .pptx/.docx/.xlsx"); the upload dialog no longer offers these types.
5. Given a scanned PDF (no text layer), when it's processed without Docling, then processing fails with a message telling the operator to enable Docling (`OPEN_NOTEBOOK_ENABLE_DOCLING=true`, ADR-007). With Docling enabled and the engine on "auto"/"docling", content-core's Docling path (OCR) is used unchanged.

## Design
- `open_notebook/utils/document_extraction.py`: own extractors for `.pptx`, `.docx`, `.pdf` (python-pptx, python-docx, pdfplumber — already installed via content-core, now declared directly).
- `graphs/source.py::content_process` uses them when the effective document engine is `simple`, or `auto` while Docling is unavailable. Otherwise content-core runs as before.
- Legacy formats are rejected in `content_process` (covers every entry point) and removed from the upload `accept` list.
- `utils/chunking.py::chunk_text` prefixes each chunk that does not start with a `[Page N]`/`[Slide N]` marker with the last marker seen before it.
