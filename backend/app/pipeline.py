from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from .database import Database


class PipelineStep(StrEnum):
    STYLE = "STYLE"
    CHARACTERS = "CHARACTERS"
    PORTRAITS = "PORTRAITS"
    CHAPTERS = "CHAPTERS"
    ILLUSTRATIONS = "ILLUSTRATIONS"


class CompletedStage(StrEnum):
    CREATED = "CREATED"
    STYLE_SET = "STYLE_SET"
    CHARACTERS_GENERATED = "CHARACTERS_GENERATED"
    PORTRAITS_GENERATED = "PORTRAITS_GENERATED"
    CHAPTERS_GENERATED = "CHAPTERS_GENERATED"
    DONE = "DONE"


NEXT_STEP = {
    CompletedStage.CREATED: PipelineStep.STYLE,
    CompletedStage.STYLE_SET: PipelineStep.CHARACTERS,
    CompletedStage.CHARACTERS_GENERATED: PipelineStep.PORTRAITS,
    CompletedStage.PORTRAITS_GENERATED: PipelineStep.CHAPTERS,
    CompletedStage.CHAPTERS_GENERATED: PipelineStep.ILLUSTRATIONS,
}

COMPLETED_BY_STEP = {
    PipelineStep.STYLE: CompletedStage.STYLE_SET,
    PipelineStep.CHARACTERS: CompletedStage.CHARACTERS_GENERATED,
    PipelineStep.PORTRAITS: CompletedStage.PORTRAITS_GENERATED,
    PipelineStep.CHAPTERS: CompletedStage.CHAPTERS_GENERATED,
    PipelineStep.ILLUSTRATIONS: CompletedStage.DONE,
}


class PipelineExecutor(Protocol):
    def execute(
        self, step: str, project: Mapping[str, Any]
    ) -> dict[str, Any]: ...


class PipelineExecutionError(Exception):
    """An expected external-provider failure reported by an executor."""


class UnconfiguredPipelineExecutor:
    """Keeps local project work available while preventing fake pipeline success."""

    def execute(
        self, step: str, project: Mapping[str, Any]
    ) -> dict[str, Any]:
        del step, project
        raise PipelineExecutionError(
            "Gemini is not configured. Set GEMINI_API_KEY and restart the backend."
        )


class InvalidTransition(Exception):
    pass


class ProjectNotFound(Exception):
    pass


class StepExecutionFailed(Exception):
    def __init__(self, message: str, project: dict[str, Any]):
        super().__init__(message)
        self.project = project


def can_recover_execution(
    row: Mapping[str, Any], process_instance_id: str
) -> bool:
    return (
        row["step_state"] == "RUNNING"
        and row["execution_owner"] is not None
        and row["execution_owner"] != process_instance_id
    )


def pipeline_project_dict(
    row: Mapping[str, Any], process_instance_id: str
) -> dict[str, Any]:
    project = {
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
    project["can_recover"] = can_recover_execution(row, process_instance_id)
    return project


def character_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    portrait_url = None
    if row["image_state"] == "READY" and row["portrait_path"]:
        portrait_url = (
            f"/api/projects/{row['project_id']}/characters/{row['id']}/portrait"
        )
    return {
        "id": row["id"],
        "name": row["name"],
        "prompt": row["prompt"],
        "image_state": row["image_state"],
        "image_error": row["image_error"],
        "portrait_url": portrait_url,
    }


def chapter_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    illustration_url = None
    if row["image_state"] == "READY" and row["illustration_path"]:
        illustration_url = (
            f"/api/projects/{row['project_id']}/chapters/{row['id']}/illustration"
        )
    return {
        "id": row["id"],
        "name": row["name"],
        "prompt": row["prompt"],
        "image_state": row["image_state"],
        "image_error": row["image_error"],
        "illustration_url": illustration_url,
    }


class PipelineStateMachine:
    def __init__(
        self, database: Database, executor: PipelineExecutor, process_instance_id: str
    ):
        self.database = database
        self.executor = executor
        self.process_instance_id = process_instance_id

    def execute(
        self,
        project_id: str,
        user_id: str,
        requested_step: PipelineStep,
        requested_style: str | None = None,
    ) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        required_stage = next(
            stage for stage, step in NEXT_STEP.items() if step == requested_step
        )
        with self.database.connect() as connection:
            claim = connection.execute(
                """UPDATE projects
                   SET step_state = 'RUNNING', active_step = ?, step_started_at = ?,
                       step_error = NULL, execution_owner = ?, updated_at = ?
                   WHERE id = ? AND user_id = ? AND completed_stage = ?
                     AND (
                         (step_state = 'IDLE' AND active_step IS NULL)
                         OR (step_state = 'FAILED' AND active_step = ?)
                     )""",
                (
                    requested_step.value,
                    started_at,
                    self.process_instance_id,
                    started_at,
                    project_id,
                    user_id,
                    required_stage.value,
                    requested_step.value,
                ),
            )
            claimed = claim.rowcount == 1

        if not claimed:
            self._raise_claim_failure(project_id, user_id, requested_step)

        running_project = dict(self._get_project_row(project_id, user_id))
        running_project["requested_style"] = requested_style
        try:
            result = self.executor.execute(requested_step.value, running_project)
        except PipelineExecutionError as error:
            failed_at = datetime.now(UTC).isoformat()
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE projects
                       SET step_state = 'FAILED', step_error = ?, execution_owner = NULL,
                           updated_at = ?
                       WHERE id = ? AND user_id = ? AND step_state = 'RUNNING'
                         AND active_step = ? AND execution_owner = ?""",
                    (
                        str(error),
                        failed_at,
                        project_id,
                        user_id,
                        requested_step.value,
                        self.process_instance_id,
                    ),
                )
            failed_project = self._get_project(project_id, user_id)
            raise StepExecutionFailed(str(error), failed_project) from error

        completed_at = datetime.now(UTC).isoformat()
        style = result.get("style") if requested_step == PipelineStep.STYLE else None
        interaction_id = result.get("latest_interaction_id")
        with self.database.connect() as connection:
            if requested_step == PipelineStep.STYLE:
                connection.execute(
                    """UPDATE projects
                       SET completed_stage = ?, step_state = 'IDLE', active_step = NULL,
                           step_started_at = NULL, step_error = NULL,
                           execution_owner = NULL, style = ?,
                           latest_interaction_id = COALESCE(?, latest_interaction_id),
                           updated_at = ?
                       WHERE id = ? AND user_id = ? AND step_state = 'RUNNING'
                         AND active_step = ? AND execution_owner = ?""",
                    (
                        COMPLETED_BY_STEP[requested_step].value,
                        style,
                        interaction_id,
                        completed_at,
                        project_id,
                        user_id,
                        requested_step.value,
                        self.process_instance_id,
                    ),
                )
            elif requested_step == PipelineStep.CHARACTERS:
                characters = result.get("characters")
                if characters is not None:
                    connection.execute(
                        "DELETE FROM characters WHERE project_id = ?", (project_id,)
                    )
                    for sort_order, character in enumerate(characters[:2]):
                        connection.execute(
                            """INSERT INTO characters
                               (id, project_id, name, prompt, sort_order)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                str(uuid4()),
                                project_id,
                                character["name"],
                                character["prompt"],
                                sort_order,
                            ),
                        )
                connection.execute(
                    """UPDATE projects
                       SET completed_stage = ?, step_state = 'IDLE', active_step = NULL,
                           step_started_at = NULL, step_error = NULL,
                           execution_owner = NULL,
                           latest_interaction_id = COALESCE(?, latest_interaction_id),
                           updated_at = ?
                       WHERE id = ? AND user_id = ? AND step_state = 'RUNNING'
                         AND active_step = ? AND execution_owner = ?""",
                    (
                        COMPLETED_BY_STEP[requested_step].value,
                        interaction_id,
                        completed_at,
                        project_id,
                        user_id,
                        requested_step.value,
                        self.process_instance_id,
                    ),
                )
            elif requested_step == PipelineStep.CHAPTERS:
                chapters = result.get("chapters")
                if chapters is not None:
                    connection.execute(
                        "DELETE FROM chapters WHERE project_id = ?", (project_id,)
                    )
                    for sort_order, chapter in enumerate(chapters[:1]):
                        connection.execute(
                            """INSERT INTO chapters
                               (id, project_id, name, prompt, sort_order)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                str(uuid4()),
                                project_id,
                                chapter["name"],
                                chapter["prompt"],
                                sort_order,
                            ),
                        )
                connection.execute(
                    """UPDATE projects
                       SET completed_stage = ?, step_state = 'IDLE', active_step = NULL,
                           step_started_at = NULL, step_error = NULL,
                           execution_owner = NULL,
                           latest_interaction_id = COALESCE(?, latest_interaction_id),
                           updated_at = ?
                       WHERE id = ? AND user_id = ? AND step_state = 'RUNNING'
                         AND active_step = ? AND execution_owner = ?""",
                    (
                        COMPLETED_BY_STEP[requested_step].value,
                        interaction_id,
                        completed_at,
                        project_id,
                        user_id,
                        requested_step.value,
                        self.process_instance_id,
                    ),
                )
            else:
                connection.execute(
                    """UPDATE projects
                       SET completed_stage = ?, step_state = 'IDLE', active_step = NULL,
                           step_started_at = NULL, step_error = NULL,
                           execution_owner = NULL, updated_at = ?
                       WHERE id = ? AND user_id = ? AND step_state = 'RUNNING'
                         AND active_step = ? AND execution_owner = ?""",
                    (
                        COMPLETED_BY_STEP[requested_step].value,
                        completed_at,
                        project_id,
                        user_id,
                        requested_step.value,
                        self.process_instance_id,
                    ),
                )
        return self._get_project(project_id, user_id)

    def recover(self, project_id: str, user_id: str) -> dict[str, Any]:
        recovered_at = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            recovery = connection.execute(
                """UPDATE projects
                   SET step_state = 'FAILED',
                       step_error = 'Pipeline execution was interrupted by a backend restart',
                       execution_owner = NULL, updated_at = ?
                   WHERE id = ? AND user_id = ? AND step_state = 'RUNNING'
                     AND (execution_owner IS NULL OR execution_owner <> ?)""",
                (
                    recovered_at,
                    project_id,
                    user_id,
                    self.process_instance_id,
                ),
            )
            recovered = recovery.rowcount == 1

        if recovered:
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE characters
                       SET image_state = 'FAILED',
                           image_error = 'Portrait generation was interrupted by a backend restart'
                       WHERE project_id = ? AND image_state = 'GENERATING'""",
                    (project_id,),
                )
                connection.execute(
                    """UPDATE chapters
                       SET image_state = 'FAILED',
                           image_error = 'Illustration generation was interrupted by a backend restart'
                       WHERE project_id = ? AND image_state = 'GENERATING'""",
                    (project_id,),
                )
            return self._get_project(project_id, user_id)

        project = self._get_project_row(project_id, user_id)
        if (
            project["step_state"] == "RUNNING"
            and project["execution_owner"] == self.process_instance_id
        ):
            raise InvalidTransition(
                f"{project['active_step']} is still running in this backend process"
            )
        raise InvalidTransition("The project has no interrupted execution to recover")

    def _raise_claim_failure(
        self, project_id: str, user_id: str, requested_step: PipelineStep
    ) -> None:
        project = self._get_project_row(project_id, user_id)
        completed_stage = CompletedStage(project["completed_stage"])
        expected_step = NEXT_STEP.get(completed_stage)
        if expected_step is None:
            raise InvalidTransition("The pipeline is already complete")
        if requested_step != expected_step:
            raise InvalidTransition(
                f"{requested_step.value} cannot run after {completed_stage.value}; "
                f"the next step is {expected_step.value}"
            )
        if project["step_state"] == "RUNNING":
            if project["execution_owner"] == self.process_instance_id:
                raise InvalidTransition(f"{requested_step.value} is already running")
            raise InvalidTransition(
                f"{requested_step.value} was interrupted and must be recovered before retry"
            )
        if project["step_state"] == "FAILED":
            raise InvalidTransition(
                f"Only the failed {project['active_step']} step may be retried"
            )
        raise InvalidTransition(f"{requested_step.value} could not be claimed")

    def _get_project(self, project_id: str, user_id: str) -> dict[str, Any]:
        row = self._get_project_row(project_id, user_id)
        project = pipeline_project_dict(row, self.process_instance_id)
        with self.database.connect() as connection:
            characters = connection.execute(
                """SELECT id, project_id, name, prompt, portrait_path,
                          image_state, image_error
                   FROM characters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project_id,),
            ).fetchall()
            chapters = connection.execute(
                """SELECT id, project_id, name, prompt, illustration_path,
                          image_state, image_error
                   FROM chapters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project_id,),
            ).fetchall()
        project["characters"] = [character_dict(character) for character in characters]
        project["chapters"] = [chapter_dict(chapter) for chapter in chapters]
        return project

    def _get_project_row(
        self, project_id: str, user_id: str
    ) -> Mapping[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFound
        return row
