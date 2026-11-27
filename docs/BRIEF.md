# Self-hosted NotebookLM — Brief

## What / Why / Who
Self-hosted, open-source NotebookLM: notebooks of sources (PDF, URL, DOCX, TXT/MD), RAG chat with citations, to help people learn from their own material.
Why: data never leaves the user's own server. Who: people who self-host it; several user accounts per instance, private notebooks.
Size: medium (no compliance, no hosted data, no multi-region). Target: thin usable MVP in a few weeks.

## Non-goals (MVP)
Images/YouTube/audio sources · explainer video · fine-tuning · bundled models/Ollama · desktop app (maybe later) · hosted/cloud version.

## Assumptions (unconfirmed defaults)
- Scale: ~1–20 users, ≤500 sources (≤50 MB each) per instance.
- Perf: 100-page PDF indexed < 1 min; first token ≈ 3 s (provider-dependent).
- Security: hashed passwords, API keys encrypted at rest, strict per-user notebook isolation, no telemetry.
- Reliability: best-effort single deployment; one Docker volume = backup unit.
- Maintenance: user maintains it as an OSS project.

## Approach
Build on **Open Notebook** (lfnovo/open-notebook, MIT, ~42k stars): already has notebooks, cited RAG chat, vector+full-text search, many BYOK providers, Docker, podcasts.
Main gap = multi-user → implement it in our fork only. We never interact with upstream; we only pull its code updates read-only. Study features later on top.

## Next steps
1. Clone Open Notebook, run it via Docker, verify MVP features vs. this brief (thinnest slice).
2. Map its stack/data model; estimate multi-user (auth, per-user notebooks, admin keys + per-user key override).
3. Write `docs/plans/multi-user.md` (goal, acceptance criteria, slices) for the implementing agent.
4. Implement multi-user + key-override, TDD.
5. Study features (from `docs/plans/notebooklm-gap-analysis.md`): fix PPTX/DOCX/PDF ingestion + page/slide citations, then quiz → flashcards → study guide → mind map → NotebookLM-style podcast.
6. Later: images/YouTube/audio sources, explainer video.

## Decision Log
| Decision | Alternatives | Why |
|---|---|---|
| Product for others to self-host | Personal tool; small shared server | User choice |
| Thin MVP in weeks | 1–3 mo; 6+ mo full feature set | Get core loop right first |
| No compliance/hosted data | GDPR; cloud version; multi-region | Data stays local is the point |
| RAG with citations | Fine-tuning; both | Fast, normal hardware, verifiable |
| Docker Compose, browser UI | Desktop app; pip/CLI | Self-host standard; desktop "just maybe" later |
| BYOK only, no bundled models/Ollama | Ollama default | User: "we do no model things" |
| Separate embedding endpoint | Bundled embedder; BM25 only | Anthropic has no embeddings; fits BYOK |
| MVP sources: PDF/URL/DOCX/TXT | +images; everything | Reliable text parsing; rest later |
| MVP: chat + citations only | +quizzes; +summaries | Smallest MVP |
| Multi-user per instance | Single user; single now, ready later | User choice (families/classrooms) |
| Admin keys + per-user override | Admin only; per-user only | Shared key plus power-user BYOK |
| Build on Open Notebook | Kotaemon; from scratch | Covers MVP + podcasts, MIT; only gap multi-user |
| Fork-only, no upstream interaction | Upstream-first via #712 / Discussion #1254 | User choice; upstream design is post-v2 and undecided |
| Keep pulling upstream code read-only | Clean break | Free bug/security fixes; accept merge conflicts |
| Study features are in scope (2026-11-27) | Keep quizzes/summaries as non-goals | User choice; core use case is learning for tests from PPTX/PDF/DOCX |
| Fix ingestion first (C1–C4) | Study tools first | User choice; wrong extraction poisons every answer |
| Citations at page/slide level | Exact-quote highlighting | User: page or slide is enough |
| Study tool order: quiz → flashcards → study guide → mind map → podcast like NotebookLM | Other orders | User choice |
| Q6: OCR = Docling (default for scans) + optional vision model via OpenAI-compatible URL | Docling only; vision only; Tesseract | User picked recommendation: both already wired, vision covers handwriting/diagrams |
| Q3: notebook chat = hybrid (full text if it fits, else retrieved passages; never silent truncation) | Always retrieval; keep context stuffing | User picked recommendation |
| Q2: self-hosted models connect by address (OpenAI-compatible URL), not bundled | Bundle model container; cloud only | User picked recommendation; keeps BRIEF "no bundled models" |
| Podcast "like NotebookLM" = format presets (Deep Dive/Brief/Critique/Debate) + length + focus prompt | Interactive mode; simplified one-click UI | User choice |
| Legacy .ppt/.doc/.xls: reject with clear message, remove from upload accept list | Convert via LibreOffice (~300 MB+) | User picked recommendation: small image, no extra code |
| Docling stays opt-in (ADR-007); empty PDF extraction shows "scanned PDF – enable Docling" hint | Enable Docling in default image | User chose A: keep image small |
