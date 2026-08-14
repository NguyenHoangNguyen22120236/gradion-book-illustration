# Engineering Decisions

## 1. Keep the stack and storage local and transactional

I chose React with TypeScript and Vite for the frontend, FastAPI with Python for the backend, SQLite for users, sessions, pipeline state, generated-item metadata, and attempt history, and the local filesystem for source books, generated images, and optional narration audio. This matches the assessment’s local-development scope while keeping the backend authoritative and durable across browser and backend restarts.

SQLite provides the transaction and conditional-update semantics needed to claim pipeline steps atomically, preventing concurrent requests from producing duplicate Gemini calls without introducing Redis, a queue, or another external service. Filesystem storage is the simplest fit for locally generated media. Authenticated SSE delivers persisted project snapshots to the frontend, while REST remains the initial-load and manual-refresh fallback.

The tradeoff is that the application is intentionally designed for a single machine rather than horizontal scaling. Moving to multiple backend instances would require shared media storage and a stronger distributed execution-ownership mechanism. The current Gemini text, image, and narration model IDs are configurable through environment variables—defaulting to `gemini-3.5-flash`, `gemini-3.1-flash-image`, and `gemini-3.1-flash-tts-preview`—so they can be changed without modifying application code.

## 2. Overrode the AI

### Use execution ownership, not elapsed time, for interrupted runs

The AI initially proposed allowing a `RUNNING` step to become retryable after a timeout. I pushed back because a Gemini image request may legitimately take longer than an arbitrary threshold. If the backend assumed the request was stale only because enough time had passed, a user retry could start a second Gemini request while the first one was still running, causing duplicate work and unnecessary API cost.

I changed the design to persist an `execution_owner` for each running step and generate a new process instance ID whenever the backend starts. A `RUNNING` step is considered recoverable only when it belongs to a previous backend process, which means the original process that owned the work no longer exists. `step_started_at` is still stored for observability, but elapsed time alone never authorizes another execution.

The cost is one additional persisted field and more recovery logic, but it gives the backend a safer rule for preventing duplicate Gemini calls.

### Separate the uploaded Gemini file from conversation state

The AI initially proposed storing a single `gemini_interaction_id` on the project. I pushed back because this mixed two different pieces of state: whether the book had already been uploaded to Gemini, and which successful conversation interaction should be continued by the next text-generation step.

I changed the design to store `gemini_file_uri` separately from `latest_interaction_id`. For example, if the book upload succeeds but the Style interaction fails, the project still remembers the uploaded Gemini file. When the user retries Style, the backend can reuse that file instead of uploading the entire book again.

`latest_interaction_id` is updated only after a successful conversation step and is used to continue the Style → Characters → Chapters interaction chain.

The cost is one additional persisted field and slightly more state handling, but it keeps file-upload state and conversation state independent and prevents unnecessary book uploads.

### Expose recovery as `can_recover`, not backend ownership details

After introducing `execution_owner`, the backend could distinguish an actively running step from one stranded by a previous backend process. However, the frontend still needed a safe way to know whether it should show “Generating…” or offer a recovery action.

The AI-generated design did not initially expose that distinction clearly. I pushed back on either exposing the raw `execution_owner` to the browser or making the frontend guess based on `step_started_at`.

Instead, the backend derives a boolean `can_recover` value:

`can_recover = RUNNING and execution_owner != current_process_id`

The frontend only needs to understand the business capability:

* `RUNNING` + `can_recover = false` → the step is still executing.
* `RUNNING` + `can_recover = true` → the previous backend process was interrupted, so the user may recover and retry the step.

The backend keeps process ownership as an internal implementation detail.

The cost is one additional API response field plus frontend and backend tests, but there is now one authoritative place where recovery eligibility is decided.

### Make SSE authoritative instead of refreshing after every completed POST

After adding SSE for real-time project updates, the AI-generated frontend still called `GET /api/projects/{id}` in a `finally` block after every step `POST` completed. I pushed back because the same persisted state was already being delivered through the project's SSE stream, including step completion and per-item portrait or illustration progress. Keeping both paths meant every completed generation caused an unnecessary extra API request and gave the frontend two competing ways to update the same project state.

I removed the automatic post-step `GET` and made SSE the authoritative update path while the project page is open. The normal project `GET` is still used when initially opening or reloading a project so the page can obtain its durable snapshot; SSE then carries subsequent state changes. The cost is that the frontend now depends more directly on the SSE connection for live updates, so disconnect/reconnect handling must remain correct. In exchange, the data flow is simpler and avoids redundant network requests without weakening backend execution safety.


## 3. If I had one more day

I would spend the additional day on browser-level end-to-end and failure-injection testing rather than adding another feature. The current backend and component tests cover the state machine, concurrency protection, retries, recovery, SSE updates, generated media, and narration in isolation, but a small Playwright suite would provide stronger confidence in the complete user journey.

I would cover identity creation, project creation, all five required pipeline steps, refresh and SSE reconnection while a step is running, a failed-step retry, recovery after a simulated backend restart, and authenticated media playback. I would also perform and document one controlled smoke test against the real Gemini service, while keeping normal automated tests mocked to avoid consuming quota.

This would not change the architecture. It would reduce the remaining risk at the boundaries between the browser, long-running requests, persisted state, and the external Gemini API.


## 4. Bonus features

In addition to the required five-step pipeline, this submission includes:

- CI through GitHub Actions.
- Three bundled public-domain sample books.
- Persisted pipeline attempt and retry history.
- Gemini TTS narration with authenticated audio playback.
- Authenticated Server-Sent Events for real-time project updates.
