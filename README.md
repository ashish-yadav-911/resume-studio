# Resume Studio

AI-powered resume optimization platform with conversational orchestration, evidence-grounded patches, human approval, and immutable version history.

## Stack

- Frontend: Next.js (App Router), TypeScript, Tailwind CSS
- Backend: Python, FastAPI, SQLAlchemy, LangGraph/LangChain
- Agents: Company Research, Job Description Matching, Translation & Localization, Section Enhancement
- LLM: OpenAI API (optional; deterministic local fallbacks are available)
- Database: SQLite by default (Firebase-compatible auth/config available)
- Research: Exa, Greenhouse, Lever, Wikipedia, official company sites
- Parsing: PyMuPDF, python-docx
- Export: DOCX, PDF

## Quick Start (Backend)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open API docs at `http://localhost:8000/api/docs`.

## Configuration

Copy `.env.example` (or create `.env`) in `backend/` and set:

- `OPENAI_API_KEY` (optional)
- `EXA_API_KEY` (optional)
- `AUTH_MODE=dev` for local development
- `DATABASE_URL` (defaults to SQLite)

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on `http://localhost:2000` and expects the backend on `http://localhost:8000`.

## Tests

```bash
cd backend
pytest -q
```

## Core Workflow

1. Upload a resume (PDF/DOCX).
2. Create a conversation linked to the resume.
3. Send messages to analyze, match, or tailor.
4. Review proposed changes and approve/reject.
5. Export or restore versions.

## Responsible Use of AI

See `docs/responsible_ai.md`. All resume changes require human approval, metrics are never invented, and every claim must be grounded in resume evidence or verified research.