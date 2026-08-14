# Engineering Decisions

## 1. Keep the stack and storage local and transactional

I chose React with TypeScript and Vite, FastAPI with Python, SQLite for metadata, and the local filesystem for books and images. The AI planning work accepted that boundary; there was no push-back to resolve on this choice. SQLite gives the pipeline a real transactional claim without adding an external service, while files are the simplest fit for durable local media. The cost is that this is deliberately a single-machine application rather than a horizontally scalable deployment. The repository defaults to the configurable `gemini-3.5-flash` text model and `gemini-3.1-flash-image` image model, so model drift can be handled through environment variables.

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

Supplement later.
