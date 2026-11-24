# Resume Studio

A conversational resume optimizer that **shows its work**. Specialist agents research a
company, match a job description, rewrite sections and localize a resume — but they never
write to your resume directly. Every suggestion arrives as an evidence-checked patch that you
review, approve or reject, and approved edits become immutable, comparable versions.

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

- **Chat with typing indicators and an agent-activity log.** Runs stream over SSE, so you see
  research, matching, enhancement and validation steps as they happen.
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
| Agents | Company Research, JD Matching, Translation & Localization, Section Enhancement |
| LLM | OpenAI-compatible Chat Completions via `httpx` (optional) |
| Research | Exa (optional), Wikipedia REST, official company sites, Greenhouse, Lever |
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
| `EXA_API_KEY` | Enable Exa web research (Wikipedia/official sites work without it). |
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

## How a run works

1. Upload a PDF/DOCX. It is parsed into the canonical structured resume and stored as immutable
   version 1 with extraction warnings (scanned, encrypted, multi-column, low text).
2. Start a conversation tied to that resume.
3. Send a request, such as “Tailor my resume to this JD”, optionally attaching a job description.
4. LangGraph loads a bounded context (active version + facts + last messages + JD), classifies
   intent, runs the needed agents, validates patches and creates a **pending proposal**.
5. The run pauses at `awaiting_approval`. SSE events keep the chat and viewer live.
6. Approve any subset of changes → one atomic new version. Reject → the active version is
   untouched. Then compare, restore, undo/redo or export.

### Trust boundary (short version)

- The canonical Pydantic resume is the only source of truth; agents return patches, not writes.
- JD scores are computed in Python from requirement statuses, never copied from the model.
- A requirement counts as `demonstrated` only with a verbatim quote from the resume.
- New metrics, employers, dates, degrees and certifications must be supported by stored facts.
- Translation must preserve numbers, dates and proper nouns.
- Approval re-checks paths, base text and proposal status; stale or duplicate patches return 409.
- Resume/JD text and fetched web pages are treated as untrusted data. Instruction-like content is
  flagged, never executed.

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
| `GET` | `/api/runs/{id}` | Run status, proposal, agent outputs |
| `GET` | `/api/runs/{id}/events` | Resumable SSE event stream |
| `POST` | `/api/runs/{id}/approve` | Atomically create a version from selected changes |
| `POST` | `/api/runs/{id}/reject` | Reject a proposal |

## Tests

```bash
cd backend
python -m pytest -q
```

18 tests cover JD parsing, deterministic scoring, verbatim evidence validation, skill/metric
boundaries, nested patch paths, vector retrieval, prompt-injection detection, SSRF/NAT64
guarding, the full enhancement → approval → idempotency → undo/redo flow, rejection,
ownership isolation, DOCX/PDF export and unsupported uploads. Tests use an isolated schema and
synthetic data.

```bash
cd frontend
npm run lint
npm run build
```

## Project layout

```
backend/
  app/
    agents/            research, JD matching, section enhancement, translation
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
- Without `EXA_API_KEY`, research relies on Wikipedia, conventional official-site discovery and
  public ATS boards; coverage is best-effort and clearly labelled.
- Firestore mirroring is untested without Firebase credentials; SQLite is always authoritative,
  and mirrored documents are not cascade-deleted by `DELETE /api/me`.
- LangGraph uses `MemorySaver` locally. Use a durable checkpointer before multi-node deployment.
- Postgres support is schema-level; install a Postgres driver (for example `psycopg[binary]`) and
  run migrations before pointing `DATABASE_URL` at it.
- No billing, team roles, email delivery or mobile app.
