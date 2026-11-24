# AI Usage Disclosure

This project was built with AI assistance. This file records how AI was used, what was
reviewed by a human, and how the product itself uses AI at runtime — so reviewers can judge the
work honestly.

## 1. AI assistance during development

- **Code generation and refactoring** were AI-assisted: backend modules, LangGraph orchestration,
  React/Next.js UI, tests and documentation were drafted with an AI coding agent and then
  reviewed, run and corrected.
- **Debugging** was AI-assisted: failing tests, type errors, SSE handling, SSRF/NAT64 resolution
  behavior and Next.js build/lint issues were diagnosed with AI help.
- **Documentation** (README, architecture, responsible-AI notes, demo script) was drafted with AI
  assistance and edited for accuracy against the running code.
- **No real personal data** was used. Tests and demos rely on synthetic resumes, job descriptions
  and fictional companies.

All generated code was executed locally. Claims in this repository are limited to behavior that
was actually run: the backend test suite, the frontend production build and lint, and live API
calls in deterministic local mode.

## 2. AI at runtime

| Feature | With `OPENAI_API_KEY` | Without a key (local mode) |
| --- | --- | --- |
| Intent routing | LLM classification validated in code | Keyword/heuristic classifier |
| JD parsing | LLM extraction filtered against literal JD text | Heuristic section parser |
| Research synthesis | LLM summary constrained to retrieved sources | Deterministic source summary |
| Section enhancement | LLM rewrites validated against facts | Template-based, evidence-preserving rewrites |
| Translation | LLM localization with preservation checks | Deterministic fallback / unsupported notice |

The model never computes the JD score, never writes to the database, and never bypasses
validation. Numbers shown to users come from application code.

## 3. What is deterministic

- Requirement status and JD alignment scoring.
- Verbatim evidence checks and metric/entity preservation.
- Patch path resolution, candidate validation and atomic approval.
- Version numbering, undo/redo chaining and diffs.
- Rate limiting, ownership checks, SSRF filtering and prompt-injection detection.
- Document parsing and DOCX/PDF export.

## 4. Human review

A human reviewed the generated code, ran the tests, exercised the API and adjusted the
architecture where AI output was wrong or incomplete. AI output in this repository should be
treated as a starting point that has been tested, not as an authoritative source.

## 5. Third-party services

- **OpenAI-compatible API** — optional, only when configured.
- **Exa** — optional research provider, only when configured.
- **Wikipedia / official company sites / Greenhouse / Lever** — key-free research sources.
- **Firebase Auth / Firestore** — optional, only when configured.

No API keys are committed. `.env*` files are ignored except `.env.example`.

## 6. Honest limitations

- Firestore mirroring has not been exercised against a live Firebase project in this
  environment.
- Research coverage without Exa is best-effort.
- Local mode is intentionally simpler than the LLM path and is labelled as such in the UI.
