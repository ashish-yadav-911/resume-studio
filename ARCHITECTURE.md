# Resume Studio — Architecture

## 1. System overview

```
Browser (Next.js 16 / React 19)
  │  X-Dev-User (local) or Firebase ID token
  ▼
FastAPI
  ├── /api/resumes         upload, versions, diff, restore, undo/redo, export
  ├── /api/conversations   chat history + run scheduling
  ├── /api/runs            SSE events, approve, reject
  └── /api/system          health / capabilities
        │
        ▼
LangGraph StateGraph (MemorySaver)
  load_context → classify → [clarify | analyze → enhance → propose | special] → finalize
        │
        ├── Company Research Agent ── Exa / Wikipedia / Greenhouse / Lever / official site
        ├── JD Matching Agent ─────── heuristic + LLM extraction, deterministic scoring
        ├── Section Enhancement ───── evidence-validated patches
        └── Translation Agent ─────── locale-aware rewrite with metric preservation
        │
        ▼
SQLAlchemy (SQLite local; Postgres-ready)
  users, resumes, resume_versions, conversations, messages,
  job_descriptions, runs, run_events, proposals, rate_buckets
        │
        ├── optional Firestore mirror (conversations, messages, versions)
        └── in-process hashed-embedding vector index (evidence retrieval)
```

The relational database is the transactional source of truth. The Firestore mirror is
write-through and best-effort; it is never read by core application logic, so a Firebase
outage cannot corrupt or block local state.

## 2. Conversational orchestration

`backend/app/orchestrator.py` builds a `StateGraph` with these nodes:

| Node | Responsibility |
| --- | --- |
| `load_context` | Load base resume version, facts, bounded chat context, JD. |
| `classify` | Heuristic or LLM intent classification + deterministic validation. |
| `clarify` | Ask one precise question when a required input is missing. |
| `analyze` | Run research and/or JD matching concurrently. |
| `enhance` | Build candidate patches (or translation) from agent outputs. |
| `propose` | Validate every patch, build candidate content, create a pending proposal. |
| `special` | Undo/redo/export/compare/general chat without rewriting the resume. |
| `finalize` | Persist run status, assistant message, events, and audit notes. |

Graph state carries only bounded context: the active version content, derived facts, the
last 12 messages (truncated), the current JD, and agent outputs. It never carries full
version history or the entire conversation.

### Request lifecycle

1. `POST /api/conversations/{id}/messages` stores the user message and a queued run.
2. A background task executes the LangGraph run.
3. The frontend subscribes to `GET /api/runs/{id}/events` (resumable SSE with sequence IDs).
4. Agents emit `agent.started` / `agent.completed` / `validation.completed`.
5. If changes exist, the run stops at `awaiting_approval` with a **pending proposal**.
6. `POST /api/runs/{id}/approve` atomically creates a new immutable version.
7. `POST /api/runs/{id}/reject` keeps the active version unchanged.

## 3. Canonical resume and patch model

The canonical resume is a validated Pydantic structure
(`personal_information`, `professional_summary`, `experience`, `skills`, `projects`,
`education`, `certifications`, `languages`, `custom_sections`).

Agents never write to the database. They return `ProposedChange` objects:

```
field_path: "experience.0.bullets.1"
before:     "Worked on backend services"
after:      "Built FastAPI services handling 2M requests/day"
reason:     "Adds a concrete action verb and preserves the existing metric"
agent:      "section_enhancement"
status:     "proposed" | "blocked"
```

`resume_service.apply_changes` applies only the accepted patch IDs. Path traversal supports
nested objects and list indices, and every path is checked before approval. The candidate
must re-validate as `ResumeContent` before it can become a version.

## 4. Deterministic trust boundary

LLM output is treated as untrusted.

| Control | Where | What it guarantees |
| --- | --- | --- |
| Intent validation | `validate_intent` | Agents cannot be selected without a resume/JD/company. |
| JD skill filtering | `JobMatchingAgent.parse_job_description` | Skills not literally in the JD are removed. |
| Evidence validation | `JobMatchingAgent.analyze` + `quote_in_resume` | A requirement can only be `demonstrated` when a verbatim resume quote supports it. |
| Score computation | `compute_match_score` | Weights and arithmetic live in application code; the model's number is ignored. |
| Patch validation | `section_enhancement` + `propose` | New metrics, employers, dates, degrees and certifications must be supported by facts. |
| Translation preservation | `translation_agent` | Numbers, dates and proper nouns must survive translation. |
| Path/state checks | `approve_run` | Stale proposals, duplicate paths and changed base text return 409. |

### JD score

Category weights (re-normalized over categories that have requirements):

| Category | Weight |
| --- | --- |
| Required technical skills | 35% |
| Responsibility alignment | 25% |
| Relevant experience/domain | 20% |
| Preferred skills | 10% |
| Demonstrated impact | 10% |

Each requirement contributes 1.0 (`demonstrated`), 0.5 (`partial`) or 0.0
(`not_demonstrated`). The result is explicitly labelled resume-to-JD alignment — never a
hiring probability or vendor ATS score.

## 5. Versioning, undo/redo, export

- Versions are append-only and immutable.
- Approval creates a new version with `parent_version_id` pointing at the proposal base.
- Partial approval applies only selected patches; the proposal status becomes
  `partially_approved`.
- Undo creates a **new** version whose content restores an earlier revision
  (`kind="undo"`, `undo_of_version_id`, `undone_version_id`). Redo follows the same pattern.
- Restore is an explicit append-only operation, not a destructive rollback.
- DOCX/PDF exports always render a selected immutable version and never mutate it.

## 6. Document ingestion

`backend/app/parsing.py` validates uploads before parsing:

- 10 MB limit, PDF/DOCX signature checks, DOCX zip-entry and compression-ratio checks.
- Macro-enabled, encrypted, corrupt and scanned documents produce typed errors/warnings.
- PyMuPDF extracts PDF text; python-docx extracts DOCX paragraphs and tables.
- Multi-column detection and low-text/scanned detection add explicit warnings.
- OCR is optional and only used when a provider is available; otherwise the app asks for a
  text-based export rather than inventing text.
- Every extracted line is treated as data; instruction-like text is flagged, not executed.

## 7. Security

- Ownership checks on every resume, conversation and run (`auth.py`).
- Dev auth (`X-Dev-User`) is explicit; Firebase Bearer verification is supported.
- Rate limiting is applied per identity and path.
- SSRF guard rejects private/loopback/link-local addresses and validates redirects; it
  understands NAT64 prefixes.
- Fetched web content is truncated and never executed.
- Logs contain request metadata and hashed identities, not resume content or PII.
- CORS is restricted to configured origins; security headers are set centrally.
- Prompt-injection patterns generate warnings and an SSE security event; they never change
  routing or tool behavior.

## 8. Research providers

Priority: Exa (when `EXA_API_KEY` is set) → Wikipedia REST → official company site
(conventional `<slug>.com` hosts and careers/about pages) → Greenhouse/Lever public boards.
Every source is a structured `Source` with URL, domain, kind, retrieval timestamp and an
`official` flag. The synthesis agent may only cite URLs that were actually retrieved.

Without an Exa key, research still returns Wikipedia and official-site pages, and job
boards when public. If nothing is found, the agent says so instead of inventing company
culture or priorities.

## 9. Vector store choice

`backend/app/vector_store.py` implements a dependency-free, deterministic vector index:
hashed word/bigram embeddings (384 dimensions), L2 normalization and cosine search.
It is used to retrieve the most relevant verified resume facts before lexical evidence
checks. The interface (`add`, `search`) is intentionally small so it can be replaced by
pgvector/Chroma/Firestore vector search without changing agents.

## 10. Firebase

- Firebase Auth is optional (`AUTH_MODE=firebase|auto`).
- Firestore write-through mirroring is optional (`FIREBASE_PROJECT_ID` or
  `FIREBASE_CREDENTIALS_PATH`): conversations, messages, resumes and immutable versions are
  written in daemon threads after successful commits.
- SQLite remains the local transactional store. For production, use Postgres for relational
  state and Firestore/pgvector for the scale-out read models.

## 11. Known limitations

- Without `OPENAI_API_KEY`, intent routing, scoring and rewrites use deterministic local
  heuristics, disclosed in the UI and run notes.
- Without `EXA_API_KEY`, official-page coverage is best-effort.
- The custom vector index is exact-search and in-process; it is not a distributed vector DB.
- LangGraph uses `MemorySaver` by default; configure a durable checkpointer for multi-node
  deployments.
- No billing, team roles, email delivery or mobile app.
