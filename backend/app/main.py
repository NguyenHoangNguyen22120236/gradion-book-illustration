import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from .database import Database
from .pipeline import (
    FakePipelineExecutor,
    InvalidTransition,
    PipelineExecutor,
    PipelineStateMachine,
    PipelineStep,
    ProjectNotFound,
    StepExecutionFailed,
)


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionRequest(BaseModel):
    name: str
    email: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address")
        return value


class ProjectRequest(BaseModel):
    title: str
    book_text: str
    source_filename: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project title is required")
        return value

    @field_validator("book_text")
    @classmethod
    def validate_book_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Book text is required")
        return value

    @field_validator("source_filename")
    @classmethod
    def validate_source_filename(cls, value: str | None) -> str | None:
        if value is not None and Path(value).suffix.lower() != ".txt":
            raise ValueError("Uploaded book must be a .txt file")
        return value


def user_dict(row: sqlite3.Row) -> dict[str, str]:
    return {key: row[key] for key in ("id", "name", "email", "created_at")}


def project_dict(row: sqlite3.Row, book_text: str | None = None) -> dict:
    result = {
        key: row[key]
        for key in (
            "id",
            "title",
            "created_at",
            "updated_at",
            "completed_stage",
            "step_state",
            "active_step",
            "step_started_at",
            "step_error",
            "style",
        )
    }
    if book_text is not None:
        result["book_text"] = book_text
    return result


def create_app(
    database_path: Path | str | None = None,
    data_dir: Path | str | None = None,
    pipeline_executor: PipelineExecutor | None = None,
) -> FastAPI:
    root = Path(__file__).resolve().parents[2]
    resolved_data = Path(data_dir) if data_dir else root / "data"
    database = Database(Path(database_path) if database_path else resolved_data / "app.db")
    state_machine = PipelineStateMachine(
        database, pipeline_executor or FakePipelineExecutor()
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resolved_data.mkdir(parents=True, exist_ok=True)
        (resolved_data / "books").mkdir(exist_ok=True)
        (resolved_data / "images").mkdir(exist_ok=True)
        database.initialize()
        yield

    application = FastAPI(title="Gradion Book Illustration API", lifespan=lifespan)
    application.state.database = database
    application.state.data_dir = resolved_data

    def current_user(authorization: Annotated[str | None, Header()] = None) -> sqlite3.Row:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authentication required")
        token = authorization.removeprefix("Bearer ").strip()
        with database.connect() as connection:
            row = connection.execute(
                """SELECT users.* FROM users JOIN sessions ON sessions.user_id = users.id
                   WHERE sessions.token = ?""",
                (token,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid session")
        return row

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/api/session")
    def sign_in(payload: SessionRequest) -> dict:
        timestamp = now_iso()
        user_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        with database.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO users (id, name, email, created_at) VALUES (?, ?, ?, ?)",
                (user_id, payload.name, payload.email, timestamp),
            )
            user = connection.execute(
                "SELECT * FROM users WHERE email = ?", (payload.email,)
            ).fetchone()
            connection.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, user["id"], timestamp),
            )
        return {"user": user_dict(user), "token": token}

    @application.get("/api/session")
    def get_session(user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict[str, str]:
        return user_dict(user)

    @application.delete("/api/session", status_code=status.HTTP_204_NO_CONTENT)
    def sign_out(
        user: Annotated[sqlite3.Row, Depends(current_user)],
        authorization: Annotated[str, Header()],
    ) -> Response:
        del user
        token = authorization.removeprefix("Bearer ").strip()
        with database.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def read_book(row: sqlite3.Row) -> str:
        book_path = resolved_data / row["book_path"]
        try:
            return book_path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise HTTPException(status_code=500, detail="Stored book file is missing") from error

    @application.post("/api/projects", status_code=status.HTTP_201_CREATED)
    def create_project(
        payload: ProjectRequest,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> dict:
        project_id = str(uuid4())
        timestamp = now_iso()
        relative_book_path = Path("books") / f"{project_id}.txt"
        absolute_book_path = resolved_data / relative_book_path
        try:
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO projects
                       (id, user_id, title, book_path, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        user["id"],
                        payload.title,
                        relative_book_path.as_posix(),
                        timestamp,
                        timestamp,
                    ),
                )
                absolute_book_path.write_text(payload.book_text, encoding="utf-8")
                row = connection.execute(
                    "SELECT * FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
        except OSError as error:
            raise HTTPException(status_code=500, detail="Could not save book text") from error
        return project_dict(row, payload.book_text)

    @application.get("/api/projects")
    def list_projects(
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> list[dict]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
        return [project_dict(row) for row in rows]

    @application.get("/api/projects/{project_id}")
    def get_project(
        project_id: str,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> dict:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user["id"]),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project_dict(row, read_book(row))

    @application.post(
        "/api/projects/{project_id}/steps/{step}", response_model=None
    )
    def execute_pipeline_step(
        project_id: str,
        step: str,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> dict | JSONResponse:
        try:
            requested_step = PipelineStep(step.upper())
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Unknown pipeline step") from error
        try:
            return state_machine.execute(project_id, user["id"], requested_step)
        except ProjectNotFound as error:
            raise HTTPException(status_code=404, detail="Project not found") from error
        except InvalidTransition as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except StepExecutionFailed as error:
            return JSONResponse(
                status_code=502,
                content={"detail": str(error), "project": error.project},
            )

    return application


app = create_app()
