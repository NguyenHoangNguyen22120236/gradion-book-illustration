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
- polling,
- loading/error/retry/stale UI,
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
- retry and stale recovery,
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
- style
- gemini_interaction_id

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
- upload book to Gemini once,
- create initial interaction,
- save interaction ID,
- initialize project state.

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

## 6. Stale-step recovery

Persist `step_started_at`.

Use a configurable setting such as:

```env
STEP_STALE_AFTER_SECONDS=
```

Do not use the demo's 8-second threshold.

When a running step is older than the configured threshold:
- expose a recovery path,
- keep all completed outputs,
- make the current step retryable.

The final threshold should be chosen after observing real Gemini timings and recorded in `DECISIONS.md`.

---

## 7. Gemini mapping

### Project creation

1. Save book locally.
2. Upload book once via Gemini File API.
3. Start initial interaction using:
   - instruction text,
   - uploaded document URI.
4. Persist returned interaction ID.

Do not resend the entire book later.

### Step 1 — Style

If style is blank:
- ask Gemini to generate a suitable art style.

If provided:
- send it as context for future steps.

Persist:
- style,
- latest interaction ID,
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
- poll backend,
- show per-item portrait progress.

On failure:
- show error,
- allow retry of current step only.

On stale state:
- show interrupted/stale status,
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

### Stage 4 — Duplicate execution + stale recovery

Goal:
Solve concurrency before real API cost exists.

Tasks:
- atomic step claim,
- duplicate request behavior,
- concurrency tests,
- stale detection,
- recovery endpoint/action.

Acceptance:
- two concurrent calls produce one execution,
- second does not invoke fake Gemini,
- stale step can be recovered.

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
- persist interaction ID,
- style step,
- structured character response,
- adult/max-2 validation,
- mocked Gemini tests.

Acceptance:
- book uploaded once,
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
- polling,
- running state,
- failure/retry state,
- stale recovery,
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
12. Simulate stale-running state.
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
- stale-running state is recoverable,
- images and book stored locally,
- frontend shows required states,
- frontend tests pass,
- backend tests pass,
- real test report committed,
- Git history is incremental,
- AI artifacts are committed,
- at least 3 genuine AI overrides documented,
- Gemini key is not committed.
