from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

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
    ) -> dict[str, str]: ...


class FakePipelineExecutor:
    """Deterministic Stage 3 substitute for the future Gemini executor."""

    def execute(
        self, step: str, project: Mapping[str, Any]
    ) -> dict[str, str]:
        del project
        if step == PipelineStep.STYLE:
            return {"style": "Deterministic watercolor storybook style"}
        return {}


class PipelineExecutionError(Exception):
    """An expected external-provider failure reported by an executor."""


class InvalidTransition(Exception):
    pass


class ProjectNotFound(Exception):
    pass


class StepExecutionFailed(Exception):
    def __init__(self, message: str, project: dict[str, Any]):
        super().__init__(message)
        self.project = project


def pipeline_project_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
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


class PipelineStateMachine:
    def __init__(self, database: Database, executor: PipelineExecutor):
        self.database = database
        self.executor = executor

    def execute(
        self, project_id: str, user_id: str, requested_step: PipelineStep
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if project is None:
                raise ProjectNotFound

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
                raise InvalidTransition(f"{requested_step.value} is already running")
            if (
                project["step_state"] == "FAILED"
                and project["active_step"] != requested_step.value
            ):
                raise InvalidTransition(
                    f"Only the failed {project['active_step']} step may be retried"
                )

            started_at = datetime.now(UTC).isoformat()
            connection.execute(
                """UPDATE projects
                   SET step_state = 'RUNNING', active_step = ?, step_started_at = ?,
                       step_error = NULL, updated_at = ?
                   WHERE id = ?""",
                (requested_step.value, started_at, started_at, project_id),
            )

        running_project = self._get_project(project_id, user_id)
        try:
            result = self.executor.execute(requested_step.value, running_project)
        except PipelineExecutionError as error:
            failed_at = datetime.now(UTC).isoformat()
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE projects
                       SET step_state = 'FAILED', step_error = ?, updated_at = ?
                       WHERE id = ?""",
                    (str(error), failed_at, project_id),
                )
            failed_project = self._get_project(project_id, user_id)
            raise StepExecutionFailed(str(error), failed_project) from error

        completed_at = datetime.now(UTC).isoformat()
        style = result.get("style") if requested_step == PipelineStep.STYLE else None
        with self.database.connect() as connection:
            if requested_step == PipelineStep.STYLE:
                connection.execute(
                    """UPDATE projects
                       SET completed_stage = ?, step_state = 'IDLE', active_step = NULL,
                           step_started_at = NULL, step_error = NULL, style = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        COMPLETED_BY_STEP[requested_step].value,
                        style,
                        completed_at,
                        project_id,
                    ),
                )
            else:
                connection.execute(
                    """UPDATE projects
                       SET completed_stage = ?, step_state = 'IDLE', active_step = NULL,
                           step_started_at = NULL, step_error = NULL, updated_at = ?
                       WHERE id = ?""",
                    (
                        COMPLETED_BY_STEP[requested_step].value,
                        completed_at,
                        project_id,
                    ),
                )
        return self._get_project(project_id, user_id)

    def _get_project(self, project_id: str, user_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
        if row is None:
            raise ProjectNotFound
        return pipeline_project_dict(row)
