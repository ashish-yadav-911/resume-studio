# Resume Studio

A conversational resume optimizer that **shows its work**. Specialist agents research a
company or role, match requirements, rewrite sections and localize a resume — but they never
write to your resume directly. Every suggestion arrives as an evidence-checked patch that you
review, approve or reject, and approved edits become immutable, comparable versions.

You can paste a job description, or simply name a company or a role (“optimize my resume for
Google”, “for a forward deployed engineer role”). Without a JD, the planner researches what
that company or role actually asks for, cites the sources it read, and tailors against those
requirements instead of guessing or asking you to paste something.

## Product tour

The workspace is a three-pane agent UI:

```
┌──────────────┬─────────────────────────────────┬───────────────────────────┐
│ Left menu    │ Chat                            │ Live resume viewer        │
│              │                                 │                           │
│ • Resumes    │ user + assistant messages       │ Current / Proposed toggle │
│ • Chats      │ streaming agent activity        │ Per-change diff cards     │
│ • Upload     │ follow-ups and clarification    │ Version history + compare │
│ • New chat   │ JD attachment panel             │ Undo / redo / restore     │
└──────────────┴─────────────────────────────────┴───────────────────────────┘
```

- **Chat with typing indicators and an agent-activity log.** Runs stream over resumable SSE from a
  persisted event log, so you see research, matching, enhancement and validation steps — and the
  stream still completes if the executor and the subscriber land on different instances.
- **Live proposed resume.** The viewer renders the canonical structured resume and highlights
  exactly which text would change *before* anything is saved.
- **Per-change approval.** Check individual changes, approve the selection, or reject the whole
  proposal. Unchecked changes are discarded.
- **Immutable history.** Every approval appends a version. Undo/redo are also append-only
  versions, so nothing is rewritten. Compare any two versions side by side.
- **Export.** Download the active or any historical version as DOCX or PDF.
- **Graceful degradation.** With no API keys the app runs deterministic local agents and says so
  in the UI. It never fakes research or invents LLM output.

## Stack

| Layer | Choice |
| --- | --- |
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4, custom design system |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 |
| Orchestration | LangGraph `StateGraph` + LangChain core (`MemorySaver` checkpointer) |
| Agents | Company Research, Role/Requirement Research, JD Matching, Translation & Localization, Section Enhancement |
| LLM | OpenAI-compatible Chat Completions via `httpx` (optional) |
| Web search | Provider-native search (OpenRouter `web` plugin) for role/company requirements; small model by default |
| Research fallbacks | Wikipedia REST, official company sites, Greenhouse, Lever, Exa when configured |
| Relational store | SQLite locally; SQLAlchemy models are Postgres-compatible (add a Postgres driver to switch) |
| Conversation mirror | Optional Firebase Firestore write-through (SQLite stays the source of truth) |
| Vector DB | Dependency-free hashed word/bigram index (`backend/app/vector_store.py`), swap-ready for pgvector/Chroma |
| Parsing | PyMuPDF (PDF), python-docx (DOCX), optional Tesseract OCR |
| Export | ReportLab (PDF), python-docx (DOCX) |
| Auth | Dev header (`X-Dev-User`) or Firebase ID token verification |

## Quick start

### 1. Backend

```bash
cd backend
python -m venv ../.venv
# Windows: ..\.venv\Scripts\activate
source ../.venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

- API docs: `http://localhost:8000/api/docs`
- Health: `http://localhost:8000/api/health`
- The app boots without keys. It logs a warning that deterministic local mode is active.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:2000`. The frontend talks to `http://localhost:8000` and sends
`X-Dev-User: demo-user` in development.

### 3. Configuration

Copy `backend/.env.example` to `backend/.env`. Every value is optional.

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | Enable LLM intent routing, research synthesis and rewrites. |
| `WEB_SEARCH_ENABLED` | Turn live web search for role/company requirements on or off (default on). |
| `WEB_SEARCH_MODEL` | Search-capable model id; defaults to `OPENAI_MODEL`. Keep it small — it only summarises postings. |
| `WEB_SEARCH_MAX_RESULTS` | Sources requested per research query (default 4). |
| `EXA_API_KEY` | Optional Exa research provider; the role/company research falls back to key-free sources without it. |
| `AUTH_MODE` | `dev` (header auth), `firebase` (Bearer ID token) or `auto`. |
| `FIREBASE_PROJECT_ID` / `FIREBASE_CREDENTIALS_PATH` | Enable Firebase auth and the optional Firestore mirror. |
| `FIREBASE_SYNC_ENABLED` | Turn Firestore write-through mirroring on/off. |
| `DATABASE_URL` | `sqlite:///./data/app.db` by default; point at Postgres for production. |
| `ALLOWED_ORIGINS` | CORS allow-list (defaults to the local frontend). |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | Per-identity request budget. |

Frontend environment (`frontend/.env.local`, optional):

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_DEV_USER=demo-user
```

### 4. Deploying to Vercel

Two projects, both from this repository:

| Project | Root directory | Notes |
| --- | --- | --- |
| `backend` | `backend/` | FastAPI via `api/index.py`; `vercel.json` sets `maxDuration: 300` so one run can finish in-request |
| `frontend` | `frontend/` | Next.js; `NEXT_PUBLIC_API_BASE_URL` must be the backend production URL |

Backend production variables: `DATABASE_URL` (Postgres — the Vercel filesystem is ephemeral, so
SQLite will not survive), `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `AUTH_MODE`,
`ALLOWED_ORIGINS` (must include the frontend domain), and optionally `WEB_SEARCH_*`,
`EXA_API_KEY`. Nothing else changes: the client drives `POST /api/runs/{id}/execute` and the SSE
endpoint replays the persisted event log.

## How a run works

1. Upload a PDF/DOCX. It is parsed into the canonical structured resume and stored as immutable
   version 1 with extraction warnings (scanned, encrypted, multi-column, low text).
2. Start a conversation tied to that resume.
3. Send a request such as “Tailor my resume to this JD” (attach a JD), “optimize my resume for
   Google”, or “optimize my resume for a forward deployed engineer role”.
4. **Plan.** LangGraph loads a bounded context (active version + facts + last messages + any
   attached JD) and the classifier proposes an intent. `validate_intent` then applies
   deterministic guardrails: it can add the agents the classifier forgot, drop a company the
   model invented, force every core section for JD/research-driven tailoring, and decide whether
   requirements must be pasted or researched. The resulting plan (`intent`, `agents`,
   `execution_strategy`, `target_sections`, `target_company`, `target_role`,
   `requirements_research`) is stored on the run and drives the graph.
5. **Acquire requirements** (`analyze`). One of three paths:
   - pasted JD → `job_matching` parses it into requirements and scores them against evidence;
   - no JD, but a company or role was named → requirement research searches current postings,
     builds a cited brief, and feeds it through the *same* matcher;
   - company + tailoring → `company_research` gathers official pages and published roles in
     parallel (with Exa when configured).
6. **Enhance** (`enhance`). One focused LLM comparison per target section runs concurrently; the
   results are merged, then skills are reconciled deterministically against the requirement
   ledger. Every proposed change is a patch (`replace`, `add`, `remove`), never a write.
7. **Validate and propose.** Patches are checked for verbatim evidence, invented metrics and
   credentials, synonym-only rewording, duplicate additions, sentence-shaped “skills”, and the
   review-size cap. A **pending proposal** is stored; the run pauses at `awaiting_approval`.
8. Approve any subset of changes → one atomic new version. Reject → the active version is
   untouched. Then compare, restore, undo/redo or export.

### Agentic planning at a glance

| Step | What decides it | Output |
| --- | --- | --- |
| Intent + agents | LLM classifier, then `validate_intent` guardrails | `agents[]`, `execution_strategy` |
| Requirements | JD parser, or web-research brief, or company research | requirement ledger + sources |
| Sections | explicit mentions > classifier targets > all core sections | `target_sections[]` |
| Changes | per-section LLM pass + deterministic skill reconciliation | patches with evidence |
| Safety | `_validate`, approval re-check | blocked/confirmed changes |

Only the agents a request needs are scheduled. A question gets analysis with no proposal; a
change request gets `section_enhancement`; anything JD- or research-driven also gets
`job_matching` so the skills ledger is always deterministic.

### Execution model (local and serverless)

- **Local / any long-lived host:** `POST /api/conversations/{id}/messages` queues the run and
  starts an in-process worker; the graph advances in the background while the client consumes
  SSE.
- **Serverless (Vercel):** an instance is frozen as soon as the response is sent, so background
  work would die mid-run. The backend therefore does not schedule a worker when `VERCEL` is set;
  the client immediately calls `POST /api/runs/{id}/execute`, which claims the run
  (`queued → running`, one atomic `UPDATE`) and awaits the graph **inside that request**
  (`maxDuration: 300` in `backend/vercel.json`).
- **Same code, one owner.** The claim makes both paths mutually exclusive, so a run can never be
  processed twice. Progress is written to `run_events`, and `GET /api/runs/{id}/events` replays
  that log (with 15s keep-alives and a status check), so the UI still finishes even when the SSE
  connection and the executor land on different instances.

### Trust boundary (short version)

- The canonical Pydantic resume is the only source of truth; agents return patches, not writes.
- JD scores are computed in Python from requirement statuses, never copied from the model.
- A requirement counts as `demonstrated` only with a verbatim quote from the resume.
- New metrics, employers, dates, degrees and certifications must be supported by stored facts.
- Research never invents requirements: if no source is retrieved, the run stops and asks for a JD
  instead of rewriting against assumptions. Every researched claim keeps its source URL.
- Skills additions must be short labels; requirement sentences (“Experience shipping production
  code in Python.”) are rejected. Anything not evidenced in the resume is proposed with
  `requires_user_confirmation`, and entirely new experience/project entries are flagged too.
- Translation must preserve numbers, dates and proper nouns.
- Approval re-checks paths, base text and proposal status; stale or duplicate patches return 409.
- Resume/JD text, research answers and fetched web pages are treated as untrusted data.
  Instruction-like content is flagged, never executed.

Details: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/me` | Identity and capability flags |
| `DELETE` | `/api/me` | Best-effort account/data deletion |
| `POST` | `/api/resumes/upload` | Upload and parse a resume |
| `GET` | `/api/resumes` | List owned resumes |
| `GET` | `/api/resumes/{id}` | Resume + active version |
| `GET` | `/api/resumes/{id}/versions` | Version history, undo/redo availability |
| `POST` | `/api/resumes/{id}/compare` | Structured diff between two versions |
| `POST` | `/api/resumes/{id}/restore` | Append a restored version |
| `POST` | `/api/resumes/{id}/undo` / `redo` | Append-only undo/redo |
| `GET` | `/api/resumes/{id}/export?format=docx\|pdf` | Export a version |
| `POST` | `/api/conversations` | Create a conversation for a resume |
| `GET` | `/api/conversations` / `{id}` | List or fetch a conversation |
| `GET` | `/api/conversations/{id}/messages` | Chat history |
| `POST` | `/api/conversations/{id}/messages` | Queue a run |
| `POST` | `/api/runs/{id}/execute` | Execute a queued run in-request (used on serverless) |
| `GET` | `/api/runs/{id}` | Run status, plan, proposal, agent outputs |
| `GET` | `/api/runs/{id}/events` | Resumable SSE event stream |
| `POST` | `/api/runs/{id}/approve` | Atomically create a version from selected changes |
| `POST` | `/api/runs/{id}/reject` | Reject a proposal |

## Tests

```bash
cd backend
python -m pytest -q
```

27 tests cover JD parsing, deterministic scoring, verbatim evidence validation, skill/metric
boundaries, add/remove patches, skills reconciliation when routing omits the section, the
"missing skills alone" path, role/company requirement research routing, conversation isolation,
nested patch paths, vector retrieval, prompt-injection detection, SSRF/NAT64 guarding, the full
enhancement → approval → idempotency → undo/redo flow, rejection, ownership isolation, DOCX/PDF
export and unsupported uploads. Tests use an isolated schema and synthetic data; nothing here
needs an API key.

```bash
cd frontend
npm run lint
npm run build
```

## Project layout

```
backend/
  app/
    agents/            company research, role/requirement research, JD matching,
                       section enhancement, translation
    routers/           resumes, conversations, runs, system
    orchestrator.py    LangGraph graph + intent validation + finalize
    resume_service.py  canonical paths, facts, patches, versions, undo/redo
    research.py        providers + SSRF guard
    vector_store.py    deterministic vector index
    firestore_sync.py  optional write-through mirror
    parsing.py         PDF/DOCX ingestion and warnings
    export.py          DOCX/PDF rendering
  tests/               unit + end-to-end API tests
  samples/             synthetic job description fixture
frontend/
  src/app/             landing page + conversation workspace
  src/components/      sidebar, resume renderer, markdown, icons
  src/lib/api.ts       typed API client and download helper
docs/                  responsible AI notes
AI_USAGE.md            AI assistance disclosure
DEMO.md                reproducible demo script
ARCHITECTURE.md        system, trust boundary, storage, security
```

## Docs

- [Architecture](ARCHITECTURE.md)
- [Responsible AI](docs/responsible_ai.md)
- [AI usage disclosure](AI_USAGE.md)
- [Demo script](DEMO.md)

## Known limitations

- Without `OPENAI_API_KEY`, routing, scoring and rewrites are deterministic heuristics. This is
  disclosed in the UI and in run notes.
- Role/company research needs a provider that exposes live search (the OpenRouter `web` plugin).
  With plain OpenAI or no key, research falls back to Wikipedia, official-site discovery and
  public ATS boards, and the run says so instead of asserting requirements.
- Firestore mirroring is untested without Firebase credentials; SQLite is always authoritative,
  and mirrored documents are not cascade-deleted by `DELETE /api/me`.
- LangGraph uses `MemorySaver`; a run must therefore finish within its executor's lifetime. On
  Vercel that means one request (`maxDuration: 300`), so a multi-minute research run will be cut
  off — split it or move the backend to a long-running host.
- Serverless runs are client-driven: closing the tab during `/execute` abandons that attempt
  (nothing is written to the resume, but the run stays `running` until retried).
- Postgres support is schema-level; install a Postgres driver (for example `psycopg[binary]`) and
  run migrations before pointing `DATABASE_URL` at it.
- No billing, team roles, email delivery or mobile app.

## Intent routing (10 intents)

The orchestrator classifies each user message into one of these intents and selects only the
agents required:

1. `GENERAL_CHAT`
2. `RESUME_ANALYSIS`
3. `COMPANY_OPTIMIZATION`
4. `JOB_MATCHING`
5. `SECTION_ENHANCEMENT`
6. `TRANSLATION`
7. `RESUME_EXPORT`
8. `VERSION_COMPARISON`
9. `UNDO`
10. `REDO`

Agents (4): `company_research`, `job_matching`, `section_enhancement`, `translation`.
Requirement research runs as a preparatory step of `job_matching`, so a researched brief and a
pasted JD go through exactly the same matcher.

When a job description is attached — or when requirements are researched from a company/role —
tailoring defaults to all core sections (experience, skills, professional summary, education)
unless the user explicitly names a section (for example "skills only").
