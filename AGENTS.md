# AGENTS.md

## Purpose

This repository is for the Gradion Intern Fullstack Developer take-home assessment.

The product is a local full-stack web app that turns a book's text into:
1. an art style,
2. a structured list of main adult characters,
3. one portrait per character,
4. a structured chapter illustration prompt,
5. one final chapter illustration.

The implementation must follow the Gradion assessment and the Google Gemini "Illustrate a book: The Wind in the Willows" pipeline, steps 1–5 only.

This file is the operating contract for Codex or any other AI coding agent working in this repository.

---

## Source of truth

Use this priority order:

1. `gradion-assessment-intern-software-engineer.md`
2. Google's book-illustration notebook, steps 1–5
3. `app-demo.html` for UI/behavior reference
4. `docs/plan.md`
5. existing code and tests

If these sources conflict, do not guess. Stop and report the conflict.

---

## Chosen stack

Use:

- Frontend: React + TypeScript + Vite
- Backend: FastAPI + Python
- Persistence: SQLite
- Book text and generated images: local filesystem
- Frontend progress updates: authenticated SSE with REST refresh fallback
- Gemini: official Python SDK and/or documented REST endpoints as needed
- Local development only

Do not change this stack without explicit approval.

Do not add:
- Redis
- RabbitMQ
- Kafka
- S3 / Cloudinary / Azure Blob
- Kubernetes
- microservices
- background job infrastructure
- OAuth
- password authentication
- automatic Gemini retry loops
- WebSockets unless explicitly approved later

Keep the solution lean.

---

## Required Gemini pipeline

The book must be supplied to Gemini once and reused across later steps.

Project creation itself must remain local-only. Do not call Gemini when the user merely creates a project. Gemini work begins only when the user explicitly starts Step 1 (Style).

Required behavior:

1. On the first Style execution, upload the book through Gemini File API if `gemini_file_uri` is not already persisted.
2. Persist `gemini_file_uri` immediately after a successful upload so a later retry does not upload the same book again unnecessarily.
3. Create the initial Gemini interaction using:
   - an initial text instruction,
   - the uploaded document URI.
4. Persist the returned interaction ID as `latest_interaction_id`.
5. Continue later text steps using `previous_interaction_id = latest_interaction_id`.
6. Style:
   - use a user-supplied style if provided,
   - otherwise generate a style from the book.
7. Characters:
   - structured JSON output,
   - main adult characters only,
   - each with an image prompt.
8. Portraits:
   - generate one image per character.
9. Chapters:
   - structured JSON output,
   - chapter prompt references relevant characters.
10. Illustrations:
   - reuse generated character portraits as reference images to keep characters visually consistent.

`gemini_file_uri` and `latest_interaction_id` have different responsibilities:
- `gemini_file_uri` records the uploaded book resource so retries can reuse it.
- `latest_interaction_id` points to the latest successful Gemini conversation state for chaining.

Do not collapse these into one ambiguous field.

Hard assessment caps:
- maximum 2 adult characters,
- maximum 1 chapter.

Enforce these caps server-side even if Gemini returns more.

Do not resend the full book text on every step.

---

## Pipeline behavior

Steps must be user-triggered and in this order:

`STYLE -> CHARACTERS -> PORTRAITS -> CHAPTERS -> ILLUSTRATIONS`

A step cannot run before the previous step succeeds.

The app must remain resumable after:
- browser refresh,
- sign out and sign back in,
- second browser tab,
- backend restart.

Completed results must never be deleted just because a later step fails.

---

## Required persisted project state

Keep completed progress separate from current execution state.

Recommended fields:

- `completed_stage`
- `step_state`
- `active_step`
- `step_started_at`
- `step_error`
- `execution_owner`
- `gemini_file_uri`
- `latest_interaction_id`

Recommended `completed_stage` values:

- `CREATED`
- `STYLE_SET`
- `CHARACTERS_GENERATED`
- `PORTRAITS_GENERATED`
- `CHAPTERS_GENERATED`
- `DONE`

Recommended `step_state` values:

- `IDLE`
- `RUNNING`
- `FAILED`

Recommended `active_step` values:

- `STYLE`
- `CHARACTERS`
- `PORTRAITS`
- `CHAPTERS`
- `ILLUSTRATIONS`
- `null`

Do not collapse completed progress and current execution state into one ambiguous field without explicit approval.

---

## Duplicate execution

Duplicate Gemini calls are forbidden.

Frontend button disabling is not sufficient.

The backend must claim a step atomically before calling Gemini.

Two overlapping requests caused by:
- double-click,
- refresh,
- second browser tab,
- concurrent HTTP calls

must not result in two Gemini calls for the same step.

Use SQLite transaction/conditional-update semantics or another simple database-level mechanism.

If a request fails to claim the step:
- do not call Gemini,
- return the existing current project state or an appropriate conflict response.

Add backend tests for this.

---

## Failure and retry

Never auto-retry Gemini in a loop.

If Gemini fails:
- preserve completed data,
- mark only the current step as failed,
- persist the error state,
- allow the user to manually retry only that step.

For portrait generation:
- save each completed portrait immediately,
- if character 1 succeeds and character 2 fails, keep character 1,
- retry should not regenerate already successful portraits unless necessary.

---

## Interrupted-step recovery

Persist:
- `step_started_at`
- `execution_owner`

Do not decide that a step is stale only because a fixed amount of time has elapsed. A long Gemini call can be legitimate, so elapsed time by itself must not enable a second Gemini call.

Create a random backend process instance ID whenever the FastAPI process starts. When a step is claimed, persist that ID as `execution_owner`.

If another request arrives while:
- the project is still `RUNNING`, and
- `execution_owner` matches the current backend process,

treat the original execution as active and do not start another Gemini call.

If the backend restarts:
- the new process gets a different owner ID,
- a persisted `RUNNING` step owned by the previous process is considered interrupted,
- expose a manual recovery/retry path without DB surgery.

Also configure a conservative backend Gemini operation timeout so a genuinely hung provider request eventually becomes `FAILED` and can be retried manually.

Keep `step_started_at` for observability and UX, but do not use elapsed time alone as proof that execution is dead.

---

## Identity

Authentication is intentionally lightweight.

User enters:
- name,
- email.

If the email exists:
- load that user's projects.

If not:
- create a user.

No password or OAuth.

A user must only access their own projects.

---

## Storage

Use SQLite for durable application state.

Use the local filesystem for:
- uploaded/pasted book text,
- character portraits,
- chapter illustration.

Suggested layout:

```text
data/
  books/
  images/
```

Do not use cloud object storage.

---

## Frontend requirements

Required screens:

- Identity
- Project list
- New project
- Project detail

Project list must show:
- title,
- created date,
- Draft / In progress / Done pill,
- five-step visual progress,
- empty state.

New project must support:
- title,
- `.txt` upload,
- pasted book text,
- validation.

Project detail must show:
- title,
- created date,
- full readable book text,
- five-step stepper,
- generated style,
- character cards,
- portrait images,
- chapter card,
- final illustration,
- current-step action,
- optional style input on step 1,
- named in-progress state,
- per-item portrait progress,
- error state,
- retry action,
- stuck-step recovery.

The demo is the visual and behavioral floor only.

Do not copy:
- localStorage as authoritative storage,
- fake Gemini calls,
- fake timing constants.

---

## Real-time progress

Project detail uses authenticated Server-Sent Events to receive complete,
authoritative project snapshots. SQLite remains the source of truth, and SSE
must never claim or own pipeline execution.

Keep `GET /api/projects/{project_id}` as the initial-load and one-shot refresh
fallback. Do not add periodic project polling or WebSockets unless explicitly
approved later.

---

## Testing

Backend tests must cover at least:
- step ordering,
- invalid out-of-order execution,
- state transitions,
- failed-step retry,
- duplicate execution prevention,
- preservation of completed results.

Frontend tests should cover a small number of meaningful states:
- empty project list,
- running step,
- failed step + retry,
- completed/generated item rendering.

Mock Gemini in automated tests.

Do not consume real Gemini quota during the automated test suite.

---

## AI working rules

For every Codex task:

1. Read `AGENTS.md` and `docs/plan.md`.
2. Implement only the requested stage.
3. For pipeline ordering, retry, duplicate-execution, interrupted-run recovery, and server-side caps, write the failing tests first.
4. For simple scaffolding/CSS/plumbing, test-first is optional; add targeted tests where they provide useful confidence.
5. Run the relevant tests.
6. Report:
   - files changed,
   - test commands,
   - test results,
   - assumptions,
   - any proposed architecture change.

Do not silently:
- change architecture,
- change storage,
- loosen server-side caps,
- add automatic retries,
- resend full book text,
- add infrastructure not required by the assessment.

If an AI-generated approach is unsafe, incorrect, or overcomplicated, report it instead of silently implementing it.

These disagreements may later become genuine entries in `DECISIONS.md`.

---

## Git workflow

Use small meaningful commits.

Do not make one giant final commit.

Suggested progression:

1. scaffold + test harness
2. persistence + identity/projects
3. pipeline state machine
4. duplicate-execution protection
5. Gemini style + characters
6. portraits
7. chapter + illustration
8. frontend workflow
9. hardening + tests
10. documentation

If a commit is substantially AI-authored, say so honestly in the commit body.

Do not fabricate test output, timestamps, decisions, or AI disagreements.

---

## Required final artifacts

The final repository must include:

- `README.md`
- `DECISIONS.md`
- `TESTING.md`
- `AGENTS.md`
- `docs/plan.md`
- `.env.example`
- one-command start script
- one-command test script
- incremental Git history

`DECISIONS.md` must contain real engineering decisions, not a worklog.

At least 3 entries must describe genuine cases where AI output was rejected, corrected, or simplified.

---

## Definition of done

The project is done only when:

- all 5 real Gemini steps work end-to-end,
- max 2 adult characters is enforced server-side,
- max 1 chapter is enforced server-side,
- the book is not resent in full on every step,
- generated state survives refresh/restart,
- duplicate execution is prevented server-side,
- failed steps are manually retryable,
- stale running state is recoverable,
- generated files remain available,
- frontend and backend tests pass,
- one command starts the stack,
- one command runs tests,
- no Gemini secret is committed,
- required documentation is complete.
