# Self-hosted NotebookLM — Brief

## What / Why / Who
Self-hosted, open-source NotebookLM: notebooks of sources (PDF, URL, DOCX, TXT/MD), RAG chat with citations, to help people learn from their own material.
Why: data never leaves the user's own server. Who: people who self-host it; several user accounts per instance, private notebooks.
Size: medium (no compliance, no hosted data, no multi-region). Target: thin usable MVP in a few weeks.

## Non-goals (MVP)
Images/YouTube/audio sources · quizzes/flashcards · summaries · explainer video · fine-tuning · bundled models/Ollama · desktop app (maybe later) · hosted/cloud version.

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
5. Later: images/YouTube/audio sources, quizzes/flashcards, explainer video.

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
