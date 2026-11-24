# Responsible Use of AI

Resume Studio is designed around a simple rule: **AI proposes, the person decides.** This
document explains the guarantees behind that rule, where they are enforced, and what the
system deliberately does not do.

## 1. Human control

- Every resume change is a **proposal**, never a direct write.
- A new immutable version exists only after an explicit approval request.
- Approval is granular: each change can be accepted or rejected independently.
- Rejecting a proposal leaves the active version byte-for-byte unchanged.
- Undo, redo and restore are append-only operations. History is never rewritten or deleted in
  place, so a user can always inspect what happened and when.

## 2. Evidence, not invention

- The canonical structured resume is the source of truth. Agents receive it read-only and return
  patches with a field path, before value, after value, reason and evidence.
- A job requirement is marked `demonstrated` only when a **verbatim quote** from the resume
  supports it. Partial matches score lower; absent evidence stays `not_demonstrated`.
- New metrics, employers, dates, degrees, certifications and technologies must be traceable to
  stored resume facts. Unsupported claims are blocked and surfaced to the reviewer.
- Translation must preserve numbers, dates, proper nouns and certifications; violations are
  flagged rather than silently accepted.
- Company research only cites sources that were actually retrieved. If nothing is found, the
  agent says so. It does not invent culture, priorities, funding or interview processes.

## 3. Deterministic scoring

- JD alignment is computed in application code from requirement statuses and fixed category
  weights. The model's own numeric output is ignored.
- The interface labels the result a **resume-to-JD alignment score**, not a hiring probability
  and not a vendor ATS score.
- Scoring is reproducible: the same resume and JD produce the same result.
- When the LLM is unavailable, heuristic parsing and deterministic rewriting keep the product
  usable. The UI and run notes explicitly identify this local mode.

## 4. Untrusted input

Resume text, job descriptions and fetched web pages are **data**, never instructions.

- Instruction-like patterns (“ignore previous instructions”, “reveal the system prompt”, role
  tags) are detected and reported as a security notice.
- Detection never changes routing, grants tools or executes content. It only informs the user
  and the audit log.
- Research fetches are restricted to public HTTP(S) destinations. Private, loopback,
  link-local, reserved and multicast targets are rejected, including addresses embedded in
  NAT64 prefixes. Redirects are re-validated.
- Fetched content is truncated before it reaches an agent.

## 5. Privacy and data minimization

- Authentication is explicit: development header auth for local work, Firebase ID tokens when
  configured. Every resume, conversation, run and proposal is ownership-checked.
- LLM prompts receive a bounded context: the active version, derived facts, a short recent
  message window and the current JD. Full version history and the entire conversation are not
  sent.
- Logs contain request metadata, status and hashed identities — never resume content, email
  addresses, phone numbers or job-description text.
- `DELETE /api/me` performs a best-effort deletion of the user's relational rows and stored
  files. Optional Firestore mirror documents are written for durability/analytics and are
  governed by the deployment's retention policy; they are not currently cascade-deleted.
- The optional Firestore mirror is write-only from the app's perspective; SQLite remains the
  transactional source of truth, so a Firebase outage cannot corrupt local state.
- Synthetic resumes and job descriptions are used for tests and demos. Do not commit real
  personal data.

## 6. Appropriate use

Resume Studio helps people present their **real** experience more clearly. It is not intended to:

- fabricate employment, education, certifications, metrics or achievements;
- infer or exploit protected characteristics such as age, gender, race, disability or
  nationality;
- produce a “guaranteed” hiring outcome or mimic a specific employer's ATS;
- replace a person's judgment about what is accurate and appropriate to submit.

Localization adapts language and conventions; it must not change the underlying facts. Users
remain responsible for verifying every accepted change before sending a resume to an employer.

## 7. Transparency

- The chat shows agent activity: intent, agent start/finish, validation and blocking.
- Each change card explains **why** it was proposed and which agent produced it.
- The viewer distinguishes current content from the proposed candidate and never implies an
  unsaved change is already active.
- Deterministic fallbacks, missing research providers and OCR availability are visible through
  `/api/me` capabilities and run notes rather than hidden.

## 8. Limitations to disclose to users

- Heuristic mode is less fluent than an LLM and may propose fewer or simpler edits.
- Official-site discovery without Exa is best-effort; some companies will have no research.
- Scanned PDFs require OCR. Without it, the system asks for a text-based export instead of
  inventing content.
- The in-process vector index is exact search over verified facts, not a distributed vector
  database.
- Local `MemorySaver` checkpoints are not durable across multi-node deployments.

## 9. Reporting concerns

If a proposal contains unsupported content, accept nothing: reject the proposal, inspect the
change card and validation notes, and report the run ID. Because versions are immutable and
proposals are persisted with their validation output, the exact behavior can be reproduced and
audited.
