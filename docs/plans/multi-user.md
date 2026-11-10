# Plan: Multi-user accounts

Status: **approved design, not started**. Implementing agent: read this file, root `AGENTS.md`, `open_notebook/AGENTS.md`, `frontend/AGENTS.md`. Do one slice per branch/PR, in order. TDD: failing test first. Do not redesign; if something here is wrong or impossible, stop and ask the user.

## Goal
As the **admin of an instance**, I want to create several user accounts, so that a family or class can share one instance, each with private notebooks, using the admin's API keys/models or their own.

## Acceptance criteria (each needs ≥1 test)
- **AC1** Given a fresh instance (no user), when it starts, then every page redirects to `/setup`, where the first admin account is created; all pre-existing data gets this admin as owner.
- **AC2** Given I am admin, when I create a user, then that user can log in with username+password. No self-registration.
- **AC3** Given users A and B, when A creates notebooks/sources/notes/chats/podcasts/insights, then B cannot list, read, change or delete them — via API, search (text + vector), chat context or background jobs. The admin can't either.
- **AC4** Given admin-configured credentials/models, when a user with no own credential for that provider uses AI, then the admin's is used; when the user has added their own credential for that provider, then theirs is used.
- **AC5** Given a background job (source processing, embedding, insight, transformation, podcast), when it runs, then its results are owned by the submitting user and it resolves models/keys as that user.
- **AC6** Given the admin deletes a user (with confirmation), then the account, its sessions and **all** its owned records (incl. embeddings, insights, chats, episodes + audio files, own credentials/models/settings) are deleted.
- **AC7** Users see admin-global models, transformations, episode/speaker profiles read-only plus their own; they can create their own and override their default models.

## Decisions (do not revisit)
| Decision | Why |
|---|---|
| Local username+password, hashed with `hashlib.scrypt` (stdlib) with per-user salt, `secrets.compare_digest` | No new dependency; ADR-009 already uses stdlib KDFs |
| Session = random token in HttpOnly, `SameSite=Lax` cookie (`Secure` when request is https); server-side `session` table storing **sha256 of token**, 30-day expiry | Logout/user-delete revoke instantly; no JWT in localStorage |
| `user.auth_provider` field (`"local"`) | SSO/OIDC later without migration |
| Roles: `admin` / `user`. Admin manages accounts only, **cannot see others' content** | Privacy in the app (operator can still read the DB — document it) |
| First admin via `/setup` page while `user` table is empty | User choice; docs must warn "first visitor becomes admin" |
| Two layers: global (`owner = NONE`, admin-managed) + per-user (`owner = user`) | Admin provides defaults, users override anything |
| Per-user embedding model allowed | Users only search their own data; changing it re-embeds only their records (reuse `rebuild_embeddings`) |
| Drop `OPEN_NOTEBOOK_PASSWORD` / `PasswordAuthMiddleware` | Replaced by accounts; mention in CHANGELOG `### Removed` |
| Current user travels in a `contextvars.ContextVar` (`open_notebook/utils/current_user.py`): set by auth middleware per request, and by each command from `input.user_id` | Domain code can filter without threading `user` through every call |
| Ownership is enforced in the domain/query layer, not only routers | Defense in depth; search functions and jobs bypass routers |

Out of scope: sharing notebooks, groups, SSO/OIDC, self-registration, quotas, encrypting user data from the operator.

## Codebase facts (verified)
- Auth today: `api/auth.py` `PasswordAuthMiddleware` (Bearer = env password); `api/routers/auth.py` has only `GET /auth/status`. Frontend: `frontend/src/lib/stores/auth-store.ts` (token in localStorage `auth-storage`), `lib/auth-token.ts`, `lib/api/client.ts` (adds `Authorization: Bearer`, `withCredentials: false`), `components/auth/LoginForm.tsx`, `app/(auth)/login/page.tsx`, `lib/hooks/use-auth.ts`, `app/(dashboard)/layout.tsx`, `src/proxy.ts`. Next rewrites `/api/*` → API (same origin, so cookies work).
- DB: SurrealDB, migrations `open_notebook/database/migrations/N.surrealql` + `N_down.surrealql` (latest 26), run on API startup. ADR-006: migration granularity — read it.
- Content tables: `notebook`, `source`, `note`, `chat_session`, `source_embedding`, `source_insight`, `episode`, `artifact`, edges `reference` (source→notebook), `refers_to`.
- Global-or-user tables: `model`, `credential`, `transformation`, `episode_profile`, `speaker_profile`.
- Singletons (`RecordModel`, `record_id = "open_notebook:…"`): `DefaultModels` (`open_notebook/ai/models.py:165`), `ProviderConfig` (`domain/provider_config.py:188`, legacy credential store — check whether still read; if only migration code, leave global), `DefaultPrompts` (`domain/transformation.py`), `ContentSettings` (`domain/content_settings.py`).
- Base: `ObjectModel.get_all/get/save/delete` in `open_notebook/domain/base.py`; ~92 raw `repo_query(` calls across `api/`, `open_notebook/`, `commands/` — **every one must be reviewed** (grep list in slice 3).
- Search: `fn::vector_search` / `fn::text_search` defined in migration `26.surrealql` (take `$notebook_ids`); need an `$owner` filter.
- Keys: `ModelManager.get_model` (`open_notebook/ai/models.py`) uses `model.credential` else `key_provider.provision_provider_keys(provider)` → `Credential.get_by_provider(provider)`.
- Jobs: `surreal_commands` `@command` in `commands/{source,embedding,podcast}_commands.py`, inputs subclass `CommandInput`.

## Slices (one PR each into `fork/main`, branch `feat/multi-user-<n>-<name>`)

### 1 — Users, password hashing, sessions (backend only)
- Migration 27: `user` (SCHEMAFULL: `username` unique index, `password_hash`, `role`, `auth_provider` default `"local"`, `created`, `updated`), `session` (`token_hash` unique index, `user: record<user>`, `expires`).
- `open_notebook/domain/user.py`: `User` model, `hash_password`/`verify_password` (scrypt, format `scrypt$n$r$p$salt$hash`), `Session.create(user) -> raw_token`, `Session.resolve(raw_token) -> User | None` (rejects expired), `Session.revoke`.
- `open_notebook/utils/current_user.py`: `current_user: ContextVar[User | None]`, `require_user()`.
- Tests: hash/verify roundtrip + wrong password; unique username; expired session rejected; token stored hashed.

### 2 — Auth API + middleware, setup flow (AC1 partial, AC2)
- Replace `PasswordAuthMiddleware` with `SessionAuthMiddleware`: reads cookie `on_session`, sets `current_user`; 401 for `/api/*` except `/api/auth/status`, `/api/auth/login`, `/api/auth/setup`, `/health`, docs. While no user exists, all other `/api/*` return 409 `{"detail":"setup_required"}`.
- Endpoints (`api/routers/auth.py`): `GET /auth/status` → `{setup_required, user|null}`; `POST /auth/setup` (only when no user; creates admin; logs in); `POST /auth/login`; `POST /auth/logout`; `GET /auth/me`; `PUT /auth/me/password`.
- Admin endpoints (`api/routers/users.py`, admin only, 403 otherwise): list, create, reset password, delete (delete body filled in slice 7; until then refuse with 501).
- Login rate limit: simple in-memory 10 failures / 5 min per username+IP → 429.
- Remove `OPEN_NOTEBOOK_PASSWORD` handling; update `tests/test_auth.py`, ADR-014 CORS note if it depends on password; CORS must allow credentials only for configured origins.
- Tests: setup only once; login/logout cookie flags; 401 without cookie; 403 non-admin on `/users`; rate limit.

### 3 — Ownership of content (AC1 migration, AC3)
- Migration 28: `owner: option<record<user>>` + index on `notebook, source, note, chat_session, source_embedding, source_insight, episode, artifact`; same field on `model, credential, transformation, episode_profile, speaker_profile` (NONE = global). Update `fn::vector_search`/`fn::text_search` (`DEFINE FUNCTION OVERWRITE`) with `$owner` filter; down migration restores old versions.
- On `POST /auth/setup`: `UPDATE <each content table> SET owner = $admin WHERE owner = NONE` (content tables only, not global tables).
- `ObjectModel`: class flag `owned: ClassVar[bool]`; `save()` sets `owner = require_user()` on create for owned classes; `get_all/get` filter by owner (get of a foreign id → same 404 as missing, per ADR-013).
- Review every `repo_query(` (`grep -rn "repo_query(" api open_notebook commands`) and add `owner = $owner` / pass `$owner`. Child records (embeddings, insights, episodes) inherit owner from parent source/notebook.
- Tests: a two-user fixture (`tests/conftest.py`) and an isolation test per router (list, get, update, delete, search, chat context, notebook↔source linking of foreign ids) → 404.

### 4 — Background jobs carry the user (AC5)
- Add `user_id: str` to every `CommandInput` subclass; every `submit_command` call passes `require_user().id`; each `@command` sets `current_user` from it at start (helper `run_as(user_id)` context manager). Missing/unknown user → job fails, no write.
- Tests: job submitted by A writes records owned by A; job with bogus user writes nothing.

### 5 — Keys and models per user (AC4, AC7 models)
- `Credential`/`Model`: list = global + own; users may create/edit/delete only own; admin manages global (`owner = NONE`) in Settings.
- Resolution in `ModelManager.get_model` / `key_provider`: if current user owns a credential for `model.provider` → use it; else `model.credential`; else global credential for provider; else env fallback (unchanged).
- `DefaultModels`: new table `user_default_models` (`owner` unique, same optional fields). Effective default = user value ?? global. Changing the user's embedding default triggers `rebuild_embeddings` scoped to that user.
- Tests: AC4 both branches; user can't edit global or foreign credential (403/404); effective defaults merge.

### 6 — Transformations, profiles, prompts per user (AC7)
- `transformation`, `episode_profile`, `speaker_profile`: list = global + own; global read-only for users; `POST …/{id}/copy` creates an owned copy. Admin edits globals.
- `DefaultPrompts`, `ContentSettings`: same override pattern as slice 5 (`user_*` table, user ?? global).
- Tests: user sees global+own, not foreign; can't edit global; copy works.

### 7 — User deletion (AC6)
- `DELETE /users/{id}` (admin, not self): revoke sessions, delete all records with `owner = user` across all tables (children first), delete episode audio files from disk (respect existing path containment, see `tests/test_podcast_audio_containment.py`), then the user. Must be idempotent if interrupted.
- Tests: after delete no record with that owner remains; files gone; other user untouched; admin can't delete self.

### 8 — Frontend
- `client.ts`: `withCredentials: true`, drop Bearer; remove token from `auth-store.ts`/`auth-token.ts`; SSE proxy routes forward the cookie.
- Pages: `/setup` (when status says `setup_required`; `proxy.ts`/dashboard layout redirect), `/login` with username+password, user menu (change password, logout) in `AppSidebar`, admin page `Settings → Users` (list, create, reset password, delete with typed confirmation "delete <username>").
- Settings for models/credentials/transformations/profiles: show "Global" badge, read-only for non-admins, "Copy" action, own items editable. i18n keys in every locale file (see `frontend/AGENTS.md`).
- Tests (Vitest): setup redirect, login form, admin-only nav item, delete confirmation, global badge read-only.

### 9 — Docs and cleanup
- Update `docs/` install/config pages (remove `OPEN_NOTEBOOK_PASSWORD`, document setup page + "first visitor becomes admin" warning + "operator can read the DB"), new decision record `docs/7-DEVELOPMENT/decisions/PDR-003-multi-user.md` superseding PDR-001 in this fork, CHANGELOG entries (Added/Removed/Security).

## Definition of done (every slice)
Full local check set from `AGENTS.md` passes (ruff check, ruff format --check, mypy, pytest, frontend lint/test:coverage/build); migration has a working down file; PR into `fork/main` lists which ACs its tests cover.
