from collections.abc import Mapping
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import PipelineExecutionError


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.projects_seen: list[Mapping[str, Any]] = []
        self.failures: dict[str, int] = {}

    def fail_next(self, step: str) -> None:
        self.failures[step] = self.failures.get(step, 0) + 1

    def execute(self, step: str, project: Mapping[str, Any]) -> dict[str, str]:
        self.calls.append(step)
        self.projects_seen.append(project)
        if self.failures.get(step, 0):
            self.failures[step] -= 1
            raise PipelineExecutionError(f"Fake {step.lower()} provider failure")
        if step == "STYLE":
            return {"style": "Deterministic watercolor storybook style"}
        return {}


def sign_in(client: TestClient, email: str = "mira@example.com") -> dict[str, str]:
    token = client.post(
        "/api/session", json={"name": "Mira", "email": email}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_project(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/projects",
        headers=headers,
        json={"title": "River story", "book_text": "A story beside the river."},
    )
    assert response.status_code == 201
    return response.json()


def execute_step(
    client: TestClient, project_id: str, step: str, headers: dict[str, str]
):
    return client.post(
        f"/api/projects/{project_id}/steps/{step.lower()}", headers=headers
    )


def test_new_project_has_initial_pipeline_state(client: TestClient) -> None:
    headers = sign_in(client)
    project = create_project(client, headers)

    detail = client.get(f"/api/projects/{project['id']}", headers=headers).json()

    assert detail["completed_stage"] == "CREATED"
    assert detail["step_state"] == "IDLE"
    assert detail["active_step"] is None
    assert detail["step_started_at"] is None
    assert detail["step_error"] is None


def test_pipeline_completes_only_in_the_valid_order(storage: tuple) -> None:
    executor = RecordingExecutor()
    database_path, data_dir = storage
    expected_stages = {
        "STYLE": "STYLE_SET",
        "CHARACTERS": "CHARACTERS_GENERATED",
        "PORTRAITS": "PORTRAITS_GENERATED",
        "CHAPTERS": "CHAPTERS_GENERATED",
        "ILLUSTRATIONS": "DONE",
    }

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        for step, expected_stage in expected_stages.items():
            response = execute_step(client, project["id"], step, headers)
            assert response.status_code == 200
            state = response.json()
            assert state["completed_stage"] == expected_stage
            assert state["step_state"] == "IDLE"
            assert state["active_step"] is None
            assert state["step_started_at"] is None
            assert state["step_error"] is None

    assert executor.calls == list(expected_stages)
    assert all(item["step_state"] == "RUNNING" for item in executor.projects_seen)
    assert [item["active_step"] for item in executor.projects_seen] == list(
        expected_stages
    )
    assert all(item["step_started_at"] for item in executor.projects_seen)


def test_out_of_order_step_is_rejected_without_execution(storage: tuple) -> None:
    executor = RecordingExecutor()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        response = execute_step(client, project["id"], "CHARACTERS", headers)
        detail = client.get(f"/api/projects/{project['id']}", headers=headers).json()

        assert execute_step(client, project["id"], "STYLE", headers).status_code == 200
        repeated_style = execute_step(client, project["id"], "STYLE", headers)

    assert response.status_code == 409
    assert detail["completed_stage"] == "CREATED"
    assert detail["step_state"] == "IDLE"
    assert repeated_style.status_code == 409
    assert executor.calls == ["STYLE"]


def test_failed_step_persists_failure_and_can_be_retried(storage: tuple) -> None:
    executor = RecordingExecutor()
    executor.fail_next("CHARACTERS")
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        assert execute_step(client, project["id"], "STYLE", headers).status_code == 200

        failed = execute_step(client, project["id"], "CHARACTERS", headers)
        assert failed.status_code == 502
        failure_state = failed.json()["project"]
        assert failure_state["completed_stage"] == "STYLE_SET"
        assert failure_state["step_state"] == "FAILED"
        assert failure_state["active_step"] == "CHARACTERS"
        assert failure_state["step_started_at"] is not None
        assert failure_state["step_error"] == "Fake characters provider failure"

        skipped_retry = execute_step(client, project["id"], "PORTRAITS", headers)
        assert skipped_retry.status_code == 409

        retried = execute_step(client, project["id"], "CHARACTERS", headers)
        assert retried.status_code == 200
        retry_state = retried.json()

    assert retry_state["completed_stage"] == "CHARACTERS_GENERATED"
    assert retry_state["step_state"] == "IDLE"
    assert retry_state["active_step"] is None
    assert retry_state["step_error"] is None
    assert executor.calls == ["STYLE", "CHARACTERS", "CHARACTERS"]


def test_earlier_result_survives_later_failure_and_retry(storage: tuple) -> None:
    executor = RecordingExecutor()
    executor.fail_next("PORTRAITS")
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        execute_step(client, project["id"], "STYLE", headers)
        execute_step(client, project["id"], "CHARACTERS", headers)

        failed = execute_step(client, project["id"], "PORTRAITS", headers)
        assert failed.status_code == 502
        after_failure = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        assert after_failure["style"] == "Deterministic watercolor storybook style"

        assert execute_step(client, project["id"], "PORTRAITS", headers).status_code == 200
        after_retry = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()

    assert after_retry["style"] == "Deterministic watercolor storybook style"
    assert after_retry["completed_stage"] == "PORTRAITS_GENERATED"


def test_completed_project_rejects_all_further_execution(storage: tuple) -> None:
    executor = RecordingExecutor()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        for step in (
            "STYLE",
            "CHARACTERS",
            "PORTRAITS",
            "CHAPTERS",
            "ILLUSTRATIONS",
        ):
            assert execute_step(client, project["id"], step, headers).status_code == 200

        calls_before = len(executor.calls)
        response = execute_step(client, project["id"], "STYLE", headers)

    assert response.status_code == 409
    assert len(executor.calls) == calls_before


def test_pipeline_action_requires_project_ownership(storage: tuple) -> None:
    executor = RecordingExecutor()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        owner = sign_in(client, "owner@example.com")
        other_user = sign_in(client, "other@example.com")
        project = create_project(client, owner)

        response = execute_step(client, project["id"], "STYLE", other_user)

    assert response.status_code == 404
    assert executor.calls == []
