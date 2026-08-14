# Gradion Book Illustration

A local full-stack application that turns a plain-text book into an art style, up to two main adult character portraits, and one illustrated chapter scene using Gemini. Each of the five pipeline steps is started explicitly by the user and persisted so work can resume after refresh, sign-out, or backend restart. After those required steps are complete, the user may optionally generate a single-speaker narration of a bounded opening excerpt from chapter one.

## Stack

- React 19, TypeScript, and Vite
- FastAPI and Python
- SQLite for users, sessions, project state, and generated-item metadata
- Local filesystem storage for source books, generated images, and optional narration audio
- Official Google Gen AI Python SDK

Docker is intentionally not required. The application is designed for local development and uses no external database, queue, or object store.

## Prerequisites

- Python 3.12+
- Node.js 22+
- A POSIX-compatible shell (Git Bash on Windows, or a standard macOS/Linux shell)
- A valid Gemini API key for real pipeline execution

## Setup

From the repository root:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..
cp .env.example .env
```

On macOS or Linux, use `.venv/bin/python` instead of `.venv/Scripts/python.exe`.

Open `.env` and set `GEMINI_API_KEY` to your own key. Never commit `.env` or a real key. The supplied defaults use `gemini-3.5-flash` for text interactions, `gemini-3.1-flash-image` (Nano Banana 2) for images, and `gemini-3.1-flash-tts-preview` for optional narration. All model IDs and the provider timeout can be overridden in `.env`.

## Run

Start the backend and frontend together:

```bash
./start.sh
```

- Frontend: `http://127.0.0.1:5173`
- Backend API: `http://127.0.0.1:8000`
- Health check: `http://127.0.0.1:8000/api/health`

Project creation works without Gemini, but all five generation steps and optional narration require a valid `GEMINI_API_KEY`.

On the New Project screen, choose one bundled public-domain sample book, upload a
`.txt` file, or paste book text. Sample selection stays local during project
creation and follows the same five user-triggered pipeline steps as every other
project.

The bundled samples are reviewed Project Gutenberg plain-text editions:

- [Alice's Adventures in Wonderland, eBook #11](https://www.gutenberg.org/ebooks/11) by Lewis Carroll;
- [The Wonderful Wizard of Oz, eBook #55](https://www.gutenberg.org/ebooks/55) by L. Frank Baum;
- [The Wind in the Willows, eBook #289](https://www.gutenberg.org/ebooks/289) by Kenneth Grahame.

Each source page identifies its edition as public domain in the USA. The
committed text distributions retain Project Gutenberg's title, author, eBook
number, credits, and license information; users outside the USA should check
their local copyright laws.

## Test

Run both automated suites with one command:

```bash
./test.sh
```

See `TESTING.md` for coverage, deliberate gaps, and the latest real test report.
GitHub Actions also runs this test harness and the frontend production build on every push and pull request.

## Architecture and pipeline

The React frontend calls the FastAPI API and polls project detail while a step is running. The backend is authoritative for identity, ownership, step order, progress, failures, and recovery.

The user-triggered pipeline is:

`STYLE -> CHARACTERS -> PORTRAITS -> CHAPTERS -> ILLUSTRATIONS`

On the first Style run, the backend uploads the saved book through Gemini File API and persists its URI. Text steps then chain from the latest successful interaction instead of resending the book. Portraits are saved individually, and the final illustration request includes those saved portraits as visual references.

SQLite metadata lives at `data/app.db`. Original book text lives in `data/books/`, portraits and illustrations live below `data/images/<project-id>/`, and optional WAV narration lives below `data/audio/<project-id>/`. The entire `data/` directory is local runtime state and is ignored by Git.

Identity is intentionally lightweight: name and email create or resume a local user, with an opaque bearer token kept in browser session storage. There are no passwords or OAuth.

Before any Gemini call, the backend atomically claims the expected step in SQLite. Concurrent double-click, refresh, or second-tab requests therefore cannot claim the same step twice. Failures preserve completed outputs and expose a manual retry for only the failed step. Each backend process has a unique execution owner; after a restart, the UI can recover a stranded `RUNNING` step and then retry it manually without relying on elapsed time alone.

Narration is separate optional state and never changes the required pipeline's `DONE` stage. A user action first extracts and persists a maximum-500-word opening excerpt from the uploaded book, then makes one single-speaker TTS request. Failed TTS requests are never retried automatically; a manual retry reuses the persisted transcript. Audio is served only through an authenticated, project-owned endpoint.
