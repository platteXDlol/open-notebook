# NotebookLM gap analysis — studying for tests from PPTX / PDF / DOCX

Status: **partly decided** (see Decision Log). Open: Q2, Q3, Q6, podcast scope.
Base: `fork/main` @ `81abb42` (multi-user slices 1–3 merged). Date: 2026-11-27.

Use case being measured:
> As a student, I want to drop my lecture slides (PPTX), scripts (PDF) and handouts (DOCX) into a notebook,
> so that I can ask questions with trustworthy citations and get study material (summaries, quizzes, flashcards) for my test.

Legend: ✅ on par · 🟡 exists but weaker · ❌ missing · 🔀 works fundamentally differently

## 1. Summary table

| Area | NotebookLM | Open Notebook (fork) | Rating |
|---|---|---|---|
| PDF ingestion | Text + OCR, page-aware | pdfplumber text + tables; **no OCR, no page markers** (Docling opt-in fixes OCR) | 🟡 |
| PPTX ingestion | Slides incl. text in tables/groups/notes | **Only titles + plain text boxes; tables, grouped shapes and speaker notes are dropped** (verified) | ❌ |
| DOCX ingestion | Full text incl. tables | **Paragraphs + headings only; tables dropped** (verified) | ❌ |
| Legacy .ppt/.doc/.xls | Via Google conversion | UI accepts them, content-core has **no handler** → fails | ❌ |
| Images / handwriting in slides | OCR / multimodal | Only with Docling + vision (opt-in, heavy) | 🟡 |
| Chat over sources | RAG over all selected sources | **Context stuffing**: per source "full content" / "insights" / off, truncated to a token budget | 🔀 |
| Citations | Numbered inline, hover shows exact quote, click jumps to passage | `[source:id]` link opens the whole source; no quote, no passage, no page | 🟡 |
| Source viewer | Original document view with highlighted passage | Extracted text + download only | 🟡 |
| Source guide (auto summary + topics on upload) | Automatic | "Simple Summary" transformation is `apply_default: True`; no key topics / suggested questions | 🟡 |
| Suggested questions / follow-ups | Yes | No | ❌ |
| Save answer to note | Yes | Yes (notes column) | ✅ |
| Study guide / Briefing / FAQ / Timeline (notebook-wide) | Studio "Reports" across all sources | Transformations run **per source** only (Dense Summary, Key Insights, Reflection Questions, ToC…) | 🔀 |
| Quiz | Yes (MC, explanations, difficulty) | No | ❌ |
| Flashcards | Yes (flip cards, review) | No | ❌ |
| Mind map | Yes (clickable → asks chat) | No | ❌ |
| Learning / tutor chat mode | "Learning guide" Socratic mode, chat goal/style settings | Single "study assistant" system prompt; no mode switch | 🟡 |
| Audio overview (podcast) | Deep dive/brief/critique/debate, length, language, interactive "join" | Configurable episode/speaker profiles, 1–4 speakers, language, any TTS provider; no interactive mode | ✅/🟡 (more flexible, not interactive) |
| Video overview | Yes | No | ❌ (BRIEF non-goal) |
| Discover sources (web search) | Yes | No | ❌ |
| Global search across notebooks | No | Yes (vector + full-text, "Ask") | ➕ better |
| Model choice / BYOK / self-host | Gemini only, Google cloud | Any provider, self-hosted, multi-user (in progress) | ➕ better |
| Custom prompts / transformations | Limited | Fully custom transformations & prompts | ➕ better |
| Sharing a notebook | Yes | No (multi-user notebooks are private) | ❌ |

## 2. Findings in detail

### 2.1 Ingestion — the biggest problem for your use case
Default document engine is content-core "simple" (Docling is an opt-in runtime, ADR-007).

- **PPTX** (`content_core/processors/document/pptx.py`): iterates `slide.shapes` and keeps only shapes with `.text`.
  Verified with a test deck: title survived; **table cells, text inside a group shape and speaker notes were all lost**.
  Lecture slides put a lot of content in exactly those places.
- **DOCX** (`processors/document/docx.py`): verified — **tables are dropped**.
- **PDF** (`processors/document/pdf.py`): pdfplumber text + tables. No OCR → scanned scripts give empty text.
  Page boundaries are not kept, so citations can never say "page 12".
- **.ppt / .doc / .xls**: accepted by the upload dialog (`frontend/src/components/sources/steps/SourceTypeStep.tsx:253`) but no MIME handler exists → processing error.
- Docling (opt-in) handles OCR, tables and PPTX better but is a large runtime (models, RAM).

### 2.2 Chat works differently (🔀)
`open_notebook/graphs/chat.py` + `utils/context_builder.py`: the selected sources are pasted into the system prompt
(full text or insights), truncated to a token budget. There is no retrieval step per question.
- Pro: whole-document reasoning on small notebooks.
- Con: with a semester of slides the context overflows → silent truncation, higher cost, worse answers.
- NotebookLM retrieves relevant passages per question. Open Notebook *has* retrieval (`graphs/ask.py`, chunk embeddings, 400-token chunks) but only in the separate Search/"Ask" page, not in notebook chat.

### 2.3 Citations are document-level
Prompt asks for `[source:xyz]`; the UI turns it into a link that opens the source dialog.
No chunk id, no quoted passage, no page/slide number, no highlight. For exam prep you want "Slide 14 says …" you can verify in one click.

### 2.4 Study tools are missing
No quiz, flashcards, mind map, notebook-wide study guide/FAQ/timeline. Transformations are per source, so
"study guide for the whole exam" is not possible without copy-pasting. BRIEF.md lists quizzes/flashcards and summaries as **MVP non-goals ("later")** — this is a known, deliberate gap.

### 2.5 What is better than NotebookLM
Self-hosted, any model, global cross-notebook search, custom transformations/prompts, flexible podcast profiles, REST API, multi-user (in progress).

## 3. Candidate changes (to be decided — nothing is planned yet)

| # | Change | Effort (rough) | Where |
|---|---|---|---|
| C1 | Fix PPTX extraction: tables, group shapes, speaker notes, slide markers | S | Our own extractor wrapper in `open_notebook/` (content-core is external, ADR-002) |
| C2 | Fix DOCX tables (and headers/footers?) | S | same |
| C3 | Keep page/slide markers in extracted text | S–M | extractor + chunking |
| C4 | .ppt/.doc support (LibreOffice convert) or remove from upload accept list | S (remove) / M (convert) | Dockerfile + extractor |
| C5 | OCR for scanned PDFs / images in slides (Docling default? tesseract? vision model?) | M–L | runtime choice |
| C6 | RAG retrieval in notebook chat (hybrid: full context when it fits, retrieval when not) | M–L | `graphs/chat.py`, `context_builder.py` |
| C7 | Passage-level citations (chunk id + quote + page, click jumps/highlights) | M–L | prompt, chunk model, frontend |
| C8 | Quiz generator (MC + explanations, from notebook or selected sources) | M | new graph + UI |
| C9 | Flashcards (+ maybe spaced repetition) | M (L with SRS) | new graph + UI |
| C10 | Notebook-wide study guide / FAQ / timeline / briefing | S–M | multi-source transformations |
| C11 | Source guide: summary + key topics + suggested questions on upload | S | transformation + UI |
| C12 | Suggested follow-up questions in chat | S | prompt + UI |
| C13 | Learning/tutor chat mode | S | prompt + toggle |
| C14 | Mind map | M | new graph + UI |
| C15 | Original document viewer (PDF render / slide view) | M | frontend |

## 4. Open questions for the user
1. Which of C1–C15 do we build, and in what order? (Suggested: C1–C4 first — garbage in, garbage out.)
2. Ingestion fix: own extractor in our fork vs make Docling the default runtime?
3. Chat: add RAG to notebook chat (hybrid) or keep context stuffing?
4. Citations: is page/slide-level enough, or do we need exact-quote highlighting?
5. Study tools: which first (quiz, flashcards, study guide, mind map)? Need spaced repetition?
6. OCR for scanned PDFs / handwritten slides: needed? Which engine (cost vs. size)?
7. This changes the BRIEF non-goals (quizzes, summaries) — update BRIEF.md and Decision Log accordingly?

## Decision Log
| Decision | Alternatives | Why |
|---|---|---|
| Q1: ingestion fixes C1–C4 first | Study tools first | User: yes (2026-11-27) |
| Q4: page/slide-level citations | Exact-quote highlight | User: page or slide is enough |
| Q5: quiz → flashcards → study guide → mind map → NotebookLM-like podcast | Other orders | User choice |
| Q7: update BRIEF non-goals + Decision Log | Leave BRIEF | User: yes |
