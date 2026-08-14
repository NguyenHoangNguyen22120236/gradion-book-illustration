# Engineering Decisions

## 1. Keep the stack and storage local and transactional

I chose React with TypeScript and Vite, FastAPI with Python, SQLite for metadata, and the local filesystem for books and images. The AI planning work accepted that boundary; there was no push-back to resolve on this choice. SQLite gives the pipeline a real transactional claim without adding an external service, while files are the simplest fit for durable local media. The cost is that this is deliberately a single-machine application rather than a horizontally scalable deployment. The repository defaults to the configurable `gemini-3.5-flash` text model and `gemini-3.1-flash-image` image model, so model drift can be handled through environment variables.

## 2. Overrode the AI
### Use execution ownership, not elapsed time, for interrupted runs

The AI initially proposed treating a `RUNNING` step as stale after a configurable timeout. I pushed back because a legitimate Gemini image request can outlast an arbitrary threshold; authorizing retry from elapsed time alone could issue a duplicate paid request. I chose to persist `execution_owner`, generate a process instance ID at backend startup, and distinguish work owned by this process from a run stranded by an earlier process. `step_started_at` remains useful for observability, but never authorizes another execution. The cost is one persisted field and more recovery logic.

### Separate the uploaded file URI from conversation state

The AI initially proposed one `gemini_interaction_id` field. I pushed back because that mixed two independent facts: whether the book was already uploaded and which successful interaction later text steps should continue. I kept `gemini_file_uri` for the uploaded resource and `latest_interaction_id` for conversation chaining. If upload succeeds but the Style interaction fails, a manual retry can reuse the file instead of uploading it again. The cost is another persisted field and slightly more retry/state handling.

### Make recovery a backend-derived capability

The backend could identify a `RUNNING` row owned by a previous process, but the frontend originally had no safe way to distinguish it from active work. I pushed back on exposing raw `execution_owner` or asking the browser to infer interruption from age. The final API returns a derived `can_recover` value and keeps ownership rules internal. The cost is one response field plus backend and frontend tests, in exchange for one authoritative recovery rule.

### Require useful minimum structured output

The AI-generated character and chapter schemas initially allowed empty arrays. Testing showed that Gemini could satisfy those schemas with zero results, leaving later portrait or illustration steps with nothing meaningful to process. I tightened both the provider schema and backend validation so an otherwise successful Characters or Chapters step must contain at least one usable item; zero adult characters or zero chapters becomes a clear, retryable step failure. This minimum is my implementation assumption for completing the five-step pipeline, not an explicit assessment count requirement. The assessment explicitly requires only the server-side maxima of two adult characters and one chapter. The accepted cost is rejecting a technically well-formed empty response and requiring a manual retry.

### Use tests as constraints on AI implementation

An earlier AI-generated stage prompt did not explicitly require tests before implementation. I corrected the workflow to write the behavioral tests first where practical, especially for ordering, retries, duplicate claims, interrupted recovery, and caps. That made the contract executable before Codex filled in the implementation. The assessment recommends this practice rather than mandating strict TDD everywhere, so I did not force test-first work for simple styling or plumbing. The cost was extra up-front test work, accepted for more reliable AI-generated behavior.

## 3. If I had one more day

Supplement later.
