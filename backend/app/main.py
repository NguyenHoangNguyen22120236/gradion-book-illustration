import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator, model_validator

from .database import Database
from .gemini import (
    GeminiClient,
    GeminiPipelineExecutor,
    GeminiSettings,
    GoogleGenAIClient,
)
from .pipeline import (
    InvalidTransition,
    PipelineExecutor,
    PipelineStateMachine,
    PipelineStep,
    ProjectNotFound,
    StepExecutionFailed,
    UnconfiguredPipelineExecutor,
    can_recover_execution,
    chapter_dict,
    character_dict,
)
from .sample_books import SAMPLE_BOOKS, read_sample_book


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
    book_text: str | None = None
    source_filename: str | None = None
    sample_book_id: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project title is required")
        return value

    @field_validator("book_text")
    @classmethod
    def validate_book_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Book text is required")
        return value

    @field_validator("source_filename")
    @classmethod
    def validate_source_filename(cls, value: str | None) -> str | None:
        if value is not None and Path(value).suffix.lower() != ".txt":
            raise ValueError("Uploaded book must be a .txt file")
        return value

    @model_validator(mode="after")
    def validate_book_source(self) -> "ProjectRequest":
        source_count = int(self.book_text is not None) + int(
            self.sample_book_id is not None
        )
        if source_count != 1:
            raise ValueError("Provide exactly one book source")
        if self.source_filename is not None and self.book_text is None:
            raise ValueError("A source filename requires book text")
        return self


class StyleStepRequest(BaseModel):
    style: str | None = None

    @field_validator("style")
    @classmethod
    def normalize_style(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def user_dict(row: sqlite3.Row) -> dict[str, str]:
    return {key: row[key] for key in ("id", "name", "email", "created_at")}


def project_dict(
    row: sqlite3.Row,
    process_instance_id: str,
    book_text: str | None = None,
    characters: list[dict] | None = None,
    chapters: list[dict] | None = None,
) -> dict:
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
    result["can_recover"] = can_recover_execution(row, process_instance_id)
    result["characters"] = characters or []
    result["chapters"] = chapters or []
    if book_text is not None:
        result["book_text"] = book_text
    return result


def create_app(
    database_path: Path | str | None = None,
    data_dir: Path | str | None = None,
    pipeline_executor: PipelineExecutor | None = None,
    process_instance_id: str | None = None,
    gemini_client: GeminiClient | None = None,
) -> FastAPI:
    root = Path(__file__).resolve().parents[2]
    resolved_data = Path(data_dir) if data_dir else root / "data"
    database = Database(Path(database_path) if database_path else resolved_data / "app.db")
    backend_process_id = process_instance_id or str(uuid4())
    if pipeline_executor is not None:
        resolved_executor = pipeline_executor
    elif gemini_client is not None:
        resolved_executor = GeminiPipelineExecutor(gemini_client, database, resolved_data)
    else:
        gemini_settings = GeminiSettings.from_environment()
        resolved_executor = (
            GeminiPipelineExecutor(
                GoogleGenAIClient(settings=gemini_settings), database, resolved_data
            )
            if gemini_settings
            else UnconfiguredPipelineExecutor()
        )
    state_machine = PipelineStateMachine(
        database, resolved_executor, backend_process_id
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
    application.state.process_instance_id = backend_process_id

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

    @application.get("/api/sample-books")
    def list_sample_books(
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> list[dict[str, str]]:
        del user
        return [sample.public_dict() for sample in SAMPLE_BOOKS]

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

    def read_characters(project_id: str) -> list[dict]:
        with database.connect() as connection:
            rows = connection.execute(
                """SELECT id, project_id, name, prompt, portrait_path,
                          image_state, image_error
                   FROM characters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project_id,),
            ).fetchall()
        return [character_dict(row) for row in rows]

    def read_chapters(project_id: str) -> list[dict]:
        with database.connect() as connection:
            rows = connection.execute(
                """SELECT id, project_id, name, prompt, illustration_path,
                          image_state, image_error
                   FROM chapters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project_id,),
            ).fetchall()
        return [chapter_dict(row) for row in rows]

    @application.post("/api/projects", status_code=status.HTTP_201_CREATED)
    def create_project(
        payload: ProjectRequest,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> dict:
        if payload.sample_book_id is not None:
            try:
                book_text = read_sample_book(payload.sample_book_id)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            except OSError as error:
                raise HTTPException(
                    status_code=500, detail="Bundled sample book is unavailable"
                ) from error
        else:
            book_text = payload.book_text
        if book_text is None:
            raise HTTPException(status_code=422, detail="Book text is required")
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
                absolute_book_path.write_text(book_text, encoding="utf-8")
                row = connection.execute(
                    "SELECT * FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
        except OSError as error:
            raise HTTPException(status_code=500, detail="Could not save book text") from error
        return project_dict(row, backend_process_id, book_text, [])

    @application.get("/api/projects")
    def list_projects(
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> list[dict]:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
        return [
            project_dict(
                row,
                backend_process_id,
                characters=read_characters(row["id"]),
                chapters=read_chapters(row["id"]),
            )
            for row in rows
        ]

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
        return project_dict(
            row,
            backend_process_id,
            read_book(row),
            read_characters(project_id),
            read_chapters(project_id),
        )

    def stored_image_response(relative_path: str | None, label: str) -> FileResponse:
        if not relative_path:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        data_root = resolved_data.resolve()
        image_path = (data_root / relative_path).resolve()
        try:
            image_path.relative_to(data_root)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=f"{label} not found") from error
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail=f"{label} not found")
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        media_type = media_types.get(image_path.suffix.lower())
        if media_type is None:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        return FileResponse(image_path, media_type=media_type)

    @application.get(
        "/api/projects/{project_id}/characters/{character_id}/portrait",
        response_model=None,
    )
    def get_character_portrait(
        project_id: str,
        character_id: str,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> FileResponse:
        with database.connect() as connection:
            row = connection.execute(
                """SELECT characters.portrait_path
                   FROM characters
                   JOIN projects ON projects.id = characters.project_id
                   WHERE characters.id = ? AND characters.project_id = ?
                     AND projects.user_id = ? AND characters.image_state = 'READY'""",
                (character_id, project_id, user["id"]),
            ).fetchone()
        if row is None or not row["portrait_path"]:
            raise HTTPException(status_code=404, detail="Portrait not found")
        return stored_image_response(row["portrait_path"], "Portrait")

    @application.get(
        "/api/projects/{project_id}/chapters/{chapter_id}/illustration",
        response_model=None,
    )
    def get_chapter_illustration(
        project_id: str,
        chapter_id: str,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> FileResponse:
        with database.connect() as connection:
            row = connection.execute(
                """SELECT chapters.illustration_path
                   FROM chapters
                   JOIN projects ON projects.id = chapters.project_id
                   WHERE chapters.id = ? AND chapters.project_id = ?
                     AND projects.user_id = ? AND chapters.image_state = 'READY'""",
                (chapter_id, project_id, user["id"]),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Illustration not found")
        return stored_image_response(row["illustration_path"], "Illustration")

    @application.post(
        "/api/projects/{project_id}/steps/{step}", response_model=None
    )
    def execute_pipeline_step(
        project_id: str,
        step: str,
        user: Annotated[sqlite3.Row, Depends(current_user)],
        payload: StyleStepRequest | None = None,
    ) -> dict | JSONResponse:
        try:
            requested_step = PipelineStep(step.upper())
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Unknown pipeline step") from error
        try:
            requested_style = (
                payload.style
                if requested_step == PipelineStep.STYLE and payload is not None
                else None
            )
            return state_machine.execute(
                project_id, user["id"], requested_step, requested_style
            )
        except ProjectNotFound as error:
            raise HTTPException(status_code=404, detail="Project not found") from error
        except InvalidTransition as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except StepExecutionFailed as error:
            return JSONResponse(
                status_code=502,
                content={"detail": str(error), "project": error.project},
            )

    @application.post("/api/projects/{project_id}/recover")
    def recover_pipeline_step(
        project_id: str,
        user: Annotated[sqlite3.Row, Depends(current_user)],
    ) -> dict:
        try:
            return state_machine.recover(project_id, user["id"])
        except ProjectNotFound as error:
            raise HTTPException(status_code=404, detail="Project not found") from error
        except InvalidTransition as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return application


app = create_app()
