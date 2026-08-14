from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
import wave

from .database import Database
from .pipeline import InvalidTransition, ProjectNotFound


INTERRUPTED_ERROR = "Narration generation was interrupted by a backend restart"


class NarrationClient(Protocol):
    def create_narration_transcript(self, file_uri: str) -> Any: ...

    def generate_speech(self, transcript: str) -> Any: ...


class NarrationProviderError(Exception):
    pass


class UnconfiguredNarrationClient:
    def create_narration_transcript(self, file_uri: str) -> Any:
        del file_uri
        raise NarrationProviderError(
            "Gemini is not configured. Set GEMINI_API_KEY and restart the backend."
        )

    def generate_speech(self, transcript: str) -> Any:
        del transcript
        raise NarrationProviderError(
            "Gemini is not configured. Set GEMINI_API_KEY and restart the backend."
        )


class NarrationExecutionFailed(Exception):
    def __init__(self, message: str, project: dict[str, Any]):
        super().__init__(message)
        self.project = project


def narration_dict(
    row: Mapping[str, Any] | None,
    project_id: str,
    process_instance_id: str,
) -> dict[str, Any]:
    if row is None:
        return {
            "state": "IDLE",
            "started_at": None,
            "error": None,
            "can_recover": False,
            "audio_url": None,
        }
    completed = row["state"] == "COMPLETED" and bool(row["audio_path"])
    return {
        "state": row["state"],
        "started_at": row["started_at"],
        "error": row["error"],
        "can_recover": (
            row["state"] == "RUNNING"
            and row["execution_owner"] is not None
            and row["execution_owner"] != process_instance_id
        ),
        "audio_url": (
            f"/api/projects/{project_id}/narration/audio" if completed else None
        ),
    }


def read_narration(
    database: Database, project_id: str, process_instance_id: str
) -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM narrations WHERE project_id = ?", (project_id,)
        ).fetchone()
    return narration_dict(row, project_id, process_instance_id)


class NarrationStateMachine:
    def __init__(
        self,
        database: Database,
        client: NarrationClient,
        data_dir: Path,
        process_instance_id: str,
    ) -> None:
        self.database = database
        self.client = client
        self.data_dir = data_dir
        self.process_instance_id = process_instance_id

    def execute(self, project_id: str, user_id: str) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if project is None:
                raise ProjectNotFound
            if project["completed_stage"] != "DONE":
                raise InvalidTransition(
                    "Narration is available only after all five illustration steps are complete"
                )
            claim = connection.execute(
                """INSERT INTO narrations
                   (project_id, state, started_at, error, execution_owner)
                   VALUES (?, 'RUNNING', ?, NULL, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       state = 'RUNNING', started_at = excluded.started_at,
                       error = NULL, execution_owner = excluded.execution_owner,
                       audio_path = NULL
                   WHERE narrations.state IN ('IDLE', 'FAILED')""",
                (project_id, started_at, self.process_instance_id),
            )
            claimed = claim.rowcount == 1

        if not claimed:
            self._raise_claim_failure(project_id, user_id)

        try:
            transcript = self._get_or_create_transcript(project_id, project)
            audio = self.client.generate_speech(transcript)
            audio_data = getattr(audio, "data", None)
            if not isinstance(audio_data, bytes) or not audio_data:
                raise NarrationProviderError("Gemini returned no narration audio")
            relative_path = self._save_audio(project_id, audio_data)
            with self.database.connect() as connection:
                completion = connection.execute(
                    """UPDATE narrations
                       SET state = 'COMPLETED', error = NULL, execution_owner = NULL,
                           audio_path = ?
                       WHERE project_id = ? AND state = 'RUNNING'
                         AND execution_owner = ?""",
                    (relative_path, project_id, self.process_instance_id),
                )
            if completion.rowcount != 1:
                raise NarrationProviderError("Could not persist completed narration")
        except Exception as error:
            safe_error = (
                str(error)
                if isinstance(error, NarrationProviderError)
                else "Gemini narration request failed"
            )
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE narrations
                       SET state = 'FAILED', error = ?, execution_owner = NULL
                       WHERE project_id = ? AND state = 'RUNNING'
                         AND execution_owner = ?""",
                    (safe_error[:500], project_id, self.process_instance_id),
                )
            raise NarrationExecutionFailed(
                safe_error[:500], self.project_state(project_id, user_id)
            ) from error

        return self.project_state(project_id, user_id)

    def recover(self, project_id: str, user_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT id FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if project is None:
                raise ProjectNotFound
            recovery = connection.execute(
                """UPDATE narrations
                   SET state = 'FAILED', error = ?, execution_owner = NULL
                   WHERE project_id = ? AND state = 'RUNNING'
                     AND execution_owner IS NOT NULL AND execution_owner <> ?""",
                (INTERRUPTED_ERROR, project_id, self.process_instance_id),
            )
            recovered = recovery.rowcount == 1
            row = connection.execute(
                "SELECT * FROM narrations WHERE project_id = ?", (project_id,)
            ).fetchone()
        if recovered:
            return self.project_state(project_id, user_id)
        if row is not None and row["state"] == "RUNNING":
            raise InvalidTransition(
                "Narration is still running in this backend process"
            )
        raise InvalidTransition("The project has no interrupted narration to recover")

    def project_state(self, project_id: str, user_id: str) -> dict[str, Any]:
        from .pipeline import pipeline_project_dict, character_dict, chapter_dict, attempt_dict

        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if project is None:
                raise ProjectNotFound
            characters = connection.execute(
                """SELECT id, project_id, name, prompt, portrait_path,
                          image_state, image_error FROM characters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project_id,),
            ).fetchall()
            chapters = connection.execute(
                """SELECT id, project_id, name, prompt, illustration_path,
                          image_state, image_error FROM chapters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project_id,),
            ).fetchall()
            attempts = connection.execute(
                """SELECT id, step, attempt_number, started_at, ended_at, outcome, error
                   FROM pipeline_attempts WHERE project_id = ?
                   ORDER BY started_at DESC, attempt_number DESC, id DESC""",
                (project_id,),
            ).fetchall()
        result = pipeline_project_dict(project, self.process_instance_id)
        result["characters"] = [character_dict(row) for row in characters]
        result["chapters"] = [chapter_dict(row) for row in chapters]
        result["attempts"] = [attempt_dict(row) for row in attempts]
        result["narration"] = read_narration(
            self.database, project_id, self.process_instance_id
        )
        return result

    def _get_or_create_transcript(
        self, project_id: str, project: Mapping[str, Any]
    ) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT transcript FROM narrations WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is not None and isinstance(row["transcript"], str) and row["transcript"].strip():
            return row["transcript"].strip()
        file_uri = project["gemini_file_uri"]
        if not isinstance(file_uri, str) or not file_uri.strip():
            raise NarrationProviderError(
                "Narration requires the uploaded Gemini book reference"
            )
        interaction = self.client.create_narration_transcript(file_uri)
        output = getattr(interaction, "output_text", None)
        if not isinstance(output, str) or not output.strip():
            raise NarrationProviderError("Gemini returned no narration transcript")
        transcript = " ".join(output.split()[:500])
        with self.database.connect() as connection:
            saved = connection.execute(
                """UPDATE narrations SET transcript = ?
                   WHERE project_id = ? AND state = 'RUNNING'
                     AND execution_owner = ?""",
                (transcript, project_id, self.process_instance_id),
            )
        if saved.rowcount != 1:
            raise NarrationProviderError("Could not persist narration transcript")
        return transcript

    def _save_audio(self, project_id: str, pcm_data: bytes) -> str:
        relative_path = Path("audio") / project_id / "narration.wav"
        target = self.resolve_audio_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".wav.tmp")
        try:
            with wave.open(str(temporary), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(pcm_data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return relative_path.as_posix()

    def resolve_audio_path(self, relative_path: str | Path) -> Path:
        root = self.data_dir.resolve()
        resolved = (root / relative_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("Narration path is outside the data directory") from error
        if resolved.suffix.lower() != ".wav":
            raise ValueError("Narration path has an unsupported audio type")
        return resolved

    def _raise_claim_failure(self, project_id: str, user_id: str) -> None:
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT completed_stage FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            narration = connection.execute(
                "SELECT * FROM narrations WHERE project_id = ?", (project_id,)
            ).fetchone()
        if project is None:
            raise ProjectNotFound
        if project["completed_stage"] != "DONE":
            raise InvalidTransition(
                "Narration is available only after all five illustration steps are complete"
            )
        if narration is not None and narration["state"] == "RUNNING":
            if narration["execution_owner"] == self.process_instance_id:
                raise InvalidTransition("Narration is already running")
            raise InvalidTransition(
                "Narration was interrupted and must be recovered before retry"
            )
        if narration is not None and narration["state"] == "COMPLETED":
            raise InvalidTransition("Narration is already complete")
        raise InvalidTransition("Narration could not be claimed")
