# Implementation Plan

## Goal

Build the smallest reliable full-stack implementation that fully satisfies the Gradion Intern Fullstack Developer assessment.

The application converts a book into:

1. style,
2. adult characters,
3. character portraits,
4. chapter prompt,
5. final chapter illustration.

The implementation follows the Google Gemini notebook pipeline rather than inventing a simplified flow.

---

## 1. Confirmed notebook understanding

The reference notebook was reviewed before application coding.

Observed mechanics:

1. Upload the book through Gemini File API.
2. Start an interaction containing:
   - initial text instruction,
   - uploaded book URI.
3. Chain later text interactions with `previous_interaction_id`.
4. Generate or accept a style.
5. Generate structured character prompts.
6. Generate portraits one character at a time.
7. Generate structured chapter prompts.
8. Reuse character portrait images when generating chapter illustrations.

Assessment-specific limits override the broader notebook examples:

- maximum 2 adult characters,
- maximum 1 chapter.

Both limits must be enforced server-side.

---

## 2. Architecture

```text
React + TypeScript + Vite
            |
            | HTTP
            v
      FastAPI backend
        /         \
       v           v
    SQLite      Local files
                   |
             books + images
                   |
                   v
               Gemini API
```

### Frontend

Responsibilities:
- identity screen,
- project list,
- new project,
- project detail,
- five-step progress,
- authenticated SSE updates with REST refresh fallback,
- loading/error/retry/interrupted-run UI,
- image rendering.

The frontend is not the source of truth for pipeline state.

### Backend

Responsibilities:
- users,
- sessions,
- projects,
- persistent pipeline state,
- ordering,
- concurrency control,
- Gemini integration,
- retry and interrupted-run recovery,
- filesystem persistence,
- image/book serving.

### Persistence

SQLite.

Reason:
- durable across restart,
- transactions,
- simple local development,
- no external DB service required.

### Files

Suggested structure:

```text
data/
  books/
    <project-id>.txt
  images/
    <project-id>/
      characters/
      chapters/
```

---

## 3. Domain model

### User

Suggested fields:
- id
- name
- email
- created_at

Email is unique.

### Project

Suggested fields:
- id
- user_id
- title
- book_path
- created_at
- updated_at
- completed_stage
- step_state
- active_step
- step_started_at
- step_error
- execution_owner
- style
- gemini_file_uri
- latest_interaction_id

`completed_stage`:
- CREATED
- STYLE_SET
- CHARACTERS_GENERATED
- PORTRAITS_GENERATED
- CHAPTERS_GENERATED
- DONE

`step_state`:
- IDLE
- RUNNING
- FAILED

`active_step`:
- STYLE
- CHARACTERS
- PORTRAITS
- CHAPTERS
- ILLUSTRATIONS
- null

### Character

Suggested fields:
- id
- project_id
- name
- prompt
- portrait_path
- image_state
- error

Maximum 2.

### Chapter

Suggested fields:
- id
- project_id
- name
- prompt
- illustration_path
- image_state
- error

Maximum 1.

---

## 4. API direction

Exact endpoint names may change slightly during implementation.

### Session

`POST /api/session`

Input:

```json
{
  "name": "Mira Hassan",
  "email": "mira@example.com"
}
```

Behavior:
- find or create user,
- establish lightweight session,
- return current user.

`GET /api/session`
- return current user.

`DELETE /api/session`
- sign out.

### Projects

`GET /api/projects`
- current user's projects.

`POST /api/projects`
- title,
- pasted text or `.txt`,
- save local file,
- initialize local project state only,
- do not call Gemini during project creation.

`GET /api/projects/{id}`
- return current persisted project state.

`GET /api/projects/{id}/book`
- return full book text.

### Pipeline

Possible endpoints:

- `POST /api/projects/{id}/steps/style`
- `POST /api/projects/{id}/steps/characters`
- `POST /api/projects/{id}/steps/portraits`
- `POST /api/projects/{id}/steps/chapters`
- `POST /api/projects/{id}/steps/illustrations`

Style accepts an optional user-provided value.

The backend decides whether the requested transition is legal.

---

## 5. Atomic step claim

Before any Gemini call, the backend must atomically claim the step.

Conceptually:

```sql
UPDATE projects
SET
  step_state = 'RUNNING',
  active_step = :requested_step,
  step_started_at = CURRENT_TIMESTAMP,
  execution_owner = :current_process_id,
  step_error = NULL
WHERE
  id = :project_id
  AND completed_stage = :required_previous_stage
  AND step_state IN ('IDLE', 'FAILED');
```

If affected rows = 1:
- caller owns execution.

If affected rows = 0:
- do not call Gemini,
- reload and return current state/conflict.

This must be covered by tests.

---

## 6. Interrupted-step recovery

Persist both:

- `step_started_at` for observability and UX,
- `execution_owner` to identify the backend process that claimed the step.

Create a random backend process instance ID whenever FastAPI starts. Store that ID
as `execution_owner` when a step is claimed.

If a request sees a `RUNNING` step whose `execution_owner` matches the current
backend process, treat that execution as active and do not start another Gemini
call.

After a backend restart, a persisted `RUNNING` step owned by a different process
is considered interrupted. Expose a manual recovery/retry path without requiring
database surgery, and keep all completed outputs.

Elapsed time alone must never authorize a second Gemini call. Configure a
conservative provider-operation timeout so a genuinely hung request eventually
becomes `FAILED` and requires manual retry.

---

## 7. Gemini mapping

### Project creation

1. Save book locally.
2. Initialize local project state.
3. Do not call Gemini.

### First Step 1 execution

When the user explicitly starts Style:

1. Upload the book through Gemini File API if `gemini_file_uri` is not already persisted.
2. Persist `gemini_file_uri` immediately after a successful upload.
3. Start the initial interaction using:
   - instruction text,
   - uploaded document URI.
4. Persist the returned interaction ID as `latest_interaction_id`.

Do not resend the entire book later.

### Step 1 — Style

If style is blank:
- ask Gemini to generate a suitable art style.

If provided:
- send it as context for future steps.

Persist:
- style,
- the new `latest_interaction_id`,
- `STYLE_SET`.

### Step 2 — Characters

Continue from the style interaction.

Request structured JSON.

Requirements:
- adult characters only,
- maximum 2,
- name,
- detailed image prompt.

Even if Gemini returns more than 2, enforce the cap server-side.

Persist character records.

### Step 3 — Portraits

Generate one portrait at a time.

For each character:
1. mark item running,
2. call image model,
3. save image locally,
4. persist success immediately,
5. continue.

If one later portrait fails:
- keep earlier successes,
- mark step failed,
- allow manual retry,
- avoid regenerating already successful portraits.

### Step 4 — Chapter

Continue from prior text interaction.

Request structured JSON:
- name,
- prompt,
- character references if useful.

Enforce maximum 1 chapter server-side.

Persist chapter.

### Step 5 — Illustration

Use:
- chapter prompt,
- existing character portraits,
- established style/context as needed.

Generate final image.

Save locally.

Mark project `DONE`.

---

## 8. Frontend behavior

### Identity

Fields:
- name,
- email.

Validate both.

Existing email resumes existing projects.

### Project list

Show:
- title,
- created date,
- Draft / In progress / Done,
- five-step progress indicator,
- empty state.

### New project

Support:
- title,
- `.txt` upload,
- pasted text,
- validation.

### Project detail

Show:
- title,
- created date,
- full book text,
- five-step stepper,
- style,
- character cards,
- portrait images,
- chapter,
- final illustration.

Show exactly one primary action for the next legal step.

While running:
- show the specific active step,
- follow authenticated backend SSE snapshots,
- show per-item portrait progress.

On failure:
- show error,
- allow retry of current step only.

On interrupted state:
- show interrupted status,
- offer recovery.

---

## 9. Implementation stages

### Stage 1 — Scaffold + test harness

Goal:
Create a runnable skeleton.

Tasks:
- React + TypeScript + Vite frontend,
- FastAPI backend,
- basic backend health endpoint,
- minimal frontend page,
- backend test harness,
- frontend test harness,
- `.env.example`,
- `.gitignore`,
- `start.sh`,
- `test.sh`.

Do not implement Gemini yet.

Acceptance:
- one command starts stack,
- one command runs tests,
- initial tests pass.

Suggested commit:

```text
chore: scaffold application and test harness
```

---

### Stage 2 — Persistence + identity + projects

Goal:
Persist users/projects and prove restart-safe state.

Tasks:
- SQLite setup,
- models,
- identity/session flow,
- project create/list/get,
- local book file persistence,
- ownership checks,
- tests.

Acceptance:
- user signs in,
- creates project,
- backend restarts,
- project remains,
- another user cannot access it.

Suggested commit:

```text
feat: add persistent users and projects
```

---

### Stage 3 — Pipeline state machine

Goal:
Implement ordering before real Gemini.

Tasks:
- enums,
- transition rules,
- active step,
- error state,
- timestamps,
- fake Gemini service,
- tests for legal/illegal transitions.

Acceptance:
- valid order works,
- invalid order fails,
- failed step can retry,
- prior results remain.

Suggested commit:

```text
feat: add resumable pipeline state machine
```

---

### Stage 4 — Duplicate execution + interrupted recovery

Goal:
Solve concurrency before real API cost exists.

Tasks:
- atomic step claim,
- duplicate request behavior,
- concurrency tests,
- backend process instance ID and execution ownership,
- interrupted-run detection after backend restart,
- recovery endpoint/action.

Acceptance:
- two concurrent calls produce one execution,
- second does not invoke fake Gemini,
- an interrupted step can be recovered manually,
- elapsed time alone cannot authorize duplicate execution.

Suggested commit:

```text
fix: prevent duplicate pipeline execution
```

---

### Stage 5 — Gemini book + style + characters

Goal:
Integrate real text-side Gemini workflow.

Tasks:
- Gemini configuration,
- File API upload,
- initial interaction,
- persist `gemini_file_uri` and `latest_interaction_id` separately,
- style step,
- structured character response,
- adult/max-2 validation,
- mocked Gemini tests.

Acceptance:
- book uploaded once,
- project creation remains local-only,
- style generated/accepted,
- later interaction reuses context,
- at most 2 adult characters persisted.

Suggested commit:

```text
feat: integrate Gemini style and character pipeline
```

---

### Stage 6 — Portraits

Goal:
Add real image generation.

Tasks:
- image-model integration,
- one portrait per character,
- persist each result immediately,
- expose per-item progress,
- retry failed remaining portrait,
- serve images,
- tests with mocks.

Acceptance:
- portrait 1 can appear while portrait 2 runs,
- portrait 1 survives portrait 2 failure,
- retry does not unnecessarily regenerate portrait 1.

Suggested commit:

```text
feat: generate and persist character portraits
```

---

### Stage 7 — Chapter + final illustration

Goal:
Complete the Gemini pipeline.

Tasks:
- structured chapter generation,
- server-side max-1 enforcement,
- portrait references in final image call,
- save/serve final illustration,
- mark project done,
- tests.

Acceptance:
- at most 1 chapter,
- final illustration uses character references,
- project reaches `DONE`.

Suggested commit:

```text
feat: complete chapter illustration pipeline
```

---

### Stage 8 — Full frontend

Goal:
Match or beat demo behavior.

Tasks:
- polished identity screen,
- project list,
- new-project page,
- project detail,
- stepper,
- style UI,
- character/chapter cards,
- authenticated SSE progress with REST refresh fallback,
- running state,
- failure/retry state,
- interrupted-run recovery,
- responsive layout,
- accessibility basics,
- frontend tests.

Acceptance:
- backend is authoritative,
- refresh shows true backend state,
- all required states are visible.

Suggested commit:

```text
feat: build project workflow interface
```

---

### Stage 9 — Hardening and UAT

Manual cases:

1. Fresh user sees empty state.
2. Existing user resumes projects.
3. Create project with pasted text.
4. Create project with `.txt`.
5. Run all five steps.
6. Refresh while running.
7. Double-click step action.
8. Trigger same step from second tab.
9. Force/mock Gemini failure.
10. Retry failed step.
11. Restart backend between stages.
12. Restart the backend with a persisted running state and recover it manually.
13. Verify full book remains readable.
14. Verify generated files remain available.
15. Verify max 2 characters.
16. Verify max 1 chapter.

Fix discovered bugs.

---

### Stage 10 — Submission documentation

Complete:

- `README.md`
- `DECISIONS.md`
- `TESTING.md`

`DECISIONS.md` must contain 4–6 real decisions and at least 3 genuine AI overrides/corrections.

Do not invent them after the fact.

`TESTING.md` must include output from a real test run.

Suggested commit:

```text
docs: finalize assessment documentation
```

### Optional bonus stages — only after Stage 10

These stages are optional and must not delay or destabilize the required
five-step pipeline. Begin them only after the definition of done is satisfied
and the full required test suite passes.

Implement optional bonuses in this order, from lowest risk to highest risk:

#### Bonus 1 — CI pipeline

Status: Complete (2026-08-14).

Goal:
Run the existing backend and frontend tests automatically on every push and
pull request.

Tasks:

- add a GitHub Actions workflow,
- install the pinned Python and Node dependencies,
- run the existing one-command test script,
- run the frontend production build,
- keep Gemini mocked and require no Gemini secret in CI.

#### Bonus 2 — Sample public-domain books

Status: Complete (2026-08-14).

Goal:
Let users create a project from a small curated set of public-domain books in
addition to uploading or pasting text.

Tasks:

- include a small, bounded set of local `.txt` samples,
- record title, author, source, and public-domain attribution,
- add a sample selector to the new-project UI,
- send the selected sample through the existing project-creation path,
- keep project creation local-only and do not call Gemini automatically.

#### Bonus 3 — Retry and attempt history

Status: Complete (2026-08-14).

Goal:
Make prior execution attempts visible for each pipeline step without changing
the existing manual-retry rules.

Tasks:

- add an append-only persisted attempt record,
- record step, attempt number, start/end timestamps, outcome, and sanitized
  error information,
- preserve interrupted-attempt information during recovery,
- expose attempt history through the project API,
- show a compact history under each step,
- test success, failure, retry, interruption, and duplicate-request behavior.

Attempt history is observability only. It must not introduce automatic retries
or weaken atomic step claiming.

#### Bonus 4 — More characters or chapters

Goal:
Increase the existing limits while keeping both dimensions explicitly bounded.

This bonus requires separate approval before implementation because the current
repository contract defines maximums of 2 adult characters and 1 chapter.

Tasks after approval:

- choose and document the new limits,
- update `AGENTS.md`, schemas, validation, prompts, tests, and documentation,
- migrate the SQLite `sort_order` constraints safely,
- confirm the frontend remains usable with the larger result sets,
- document the increased Gemini cost and runtime.

Do not loosen either cap through frontend-only validation.

#### Bonus 5 — One later notebook section

Status: Complete (2026-08-14) — TTS narration.

Goal:
Implement exactly one later notebook feature after verifying its mechanics in
the source notebook and current official API documentation.

Prefer TTS narration as the smallest extension. Lyria background music or Veo
chapter animation may be chosen instead with an explicit scope decision.

Tasks:

- add a user-triggered action after the required illustration pipeline,
- persist running, failed, interrupted, and completed state,
- store generated audio or video on the local filesystem,
- provide authenticated media serving and an accessible player,
- preserve completed required outputs when the bonus generation fails,
- mock the provider in automated tests and never auto-retry it.

#### Bonus 6 — Real-time step updates

Status: Complete (2026-08-14).

Goal:
Replace frontend polling with server-pushed progress only after the polling
implementation is stable.

Prefer SSE over WebSocket unless bidirectional messaging becomes necessary.

Tasks:

- define authentication for long-lived connections,
- stream authoritative persisted state or state-change events,
- handle reconnects, duplicate events, backend restarts, and multiple tabs,
- retain a safe refresh/fallback path,
- add frontend and backend tests for connection and recovery behavior,
- remove polling only after feature parity is verified.

Suggested bonus progression:

```text
CI
  → sample public-domain books
  → retry/attempt history
  → optionally increase bounded caps
  → one later notebook section (prefer TTS)
  → SSE real-time updates
```

---

## 10. AI workflow

For every stage:

```text
requirements
    ↓
Codex reads AGENTS.md + docs/plan.md
    ↓
Codex implements ONE stage
    ↓
Codex writes/runs tests
    ↓
human reviews and runs it
    ↓
accept / reject / simplify
    ↓
record meaningful decision if needed
    ↓
commit
    ↓
next stage
```

Do not ask Codex to implement the entire assessment at once.

---

## 11. First Codex prompt

Use this after committing `AGENTS.md` and `docs/plan.md`:

```text
Read AGENTS.md and docs/plan.md completely before making changes.

Implement Stage 1 only: Scaffold + test harness.

Use:
- React + TypeScript + Vite
- FastAPI + Python

Requirements:
- create the frontend and backend project structure,
- add a backend health endpoint,
- render a minimal frontend page,
- establish frontend and backend test harnesses,
- add .env.example and .gitignore,
- create one-command start and test scripts.

Do not implement Gemini integration yet.
Do not implement the full UI yet.
Do not add Docker, Redis, queues, object storage, or other infrastructure.
Do not change architecture without asking.

Write or update tests for the work.
Run relevant tests before finishing.

At the end report:
1. files changed,
2. tests run and results,
3. assumptions,
4. anything you think should change from docs/plan.md, without changing it silently.
```

---

## 12. Final success checklist

Before submission verify:

- all 5 real Gemini steps work,
- max 2 adult characters enforced server-side,
- max 1 chapter enforced server-side,
- book context reused instead of resent,
- step ordering enforced,
- duplicate calls prevented server-side,
- refresh/restart preserves state,
- failures are manually retryable,
- interrupted running state is manually recoverable,
- images and book stored locally,
- frontend shows required states,
- frontend tests pass,
- backend tests pass,
- real test report committed,
- Git history is incremental,
- AI artifacts are committed,
- at least 3 genuine AI overrides documented,
- Gemini key is not committed.
