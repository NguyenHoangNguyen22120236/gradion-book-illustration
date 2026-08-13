# Gradion Book Illustration

A local React and FastAPI application for the Gradion book-illustration assessment.

## Prerequisites

- Python 3.12+
- Node.js 22+
- A POSIX-compatible shell such as Git Bash on Windows

## Setup

Create the backend virtual environment and install both dependency sets:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
cd frontend && npm install && cd ..
```

On macOS or Linux, use `.venv/bin/python` in place of the Windows executable.

## Run

Start the FastAPI backend and Vite frontend together:

```bash
./start.sh
```

The frontend is served at `http://127.0.0.1:5173` and the health endpoint at
`http://127.0.0.1:8000/api/health`.

Application data is stored locally under `data/`: SQLite metadata in `app.db`
and project book text in `books/`. This runtime directory is ignored by Git.

Identity is intentionally lightweight for this local assessment: enter a name
and email, and the backend creates or resumes the user by normalized email. The
browser keeps only an opaque session token in session storage, so closing the
tab signs the browser out without deleting backend data.

## Test

Run the backend and frontend test suites together:

```bash
./test.sh
```
