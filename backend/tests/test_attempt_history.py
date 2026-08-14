import sqlite3
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import sleep
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import PipelineExecutionError


class AttemptExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures: dict[str, str] = {}

    def fail_next(self, step: str, message: str | None = None) -> None:
        self.failures[step] = message or f"Safe {step.lower()} failure"

    def execute(self, step: str, project: Mapping[str, Any]) -> dict[str, Any]:
        del project
        self.calls.append(step)
        if step in self.failures:
            raise PipelineExecutionError(self.failures.pop(step))
        if step == "STYLE":
            return {"style": "Watercolor"}
        return {}


class SlowAttemptExecutor(AttemptExecutor):
    def execute(self, step: str, project: Mapping[str, Any]) -> dict[str, Any]:
        sleep(0.1)
        return super().execute(step, project)


def sign_in(client: TestClient, email: str = "mira@example.com") -> dict[str, str]:
    token = client.post(
        "/api/session", json={"name": "Mira", "email": email}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_project(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/projects",
        headers=headers,
        json={"title": "River story", "book_text": "A river story."},
    )
    assert response.status_code == 201
    return response.json()


def execute_step(
    client: TestClient, project_id: str, step: str, headers: dict[str, str]
):
    return client.post(
        f"/api/projects/{project_id}/steps/{step.lower()}", headers=headers
    )


def attempts_for(
    client: TestClient,
    project_id: str,
    headers: dict[str, str],
    step: str | None = None,
) -> list[dict[str, Any]]:
    attempts = client.get(
        f"/api/projects/{project_id}", headers=headers
    ).json()["attempts"]
    return [attempt for attempt in attempts if step is None or attempt["step"] == step]


def test_successful_execution_records_one_completed_attempt(storage: tuple) -> None:
    executor = AttemptExecutor()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        response = execute_step(client, project["id"], "STYLE", headers)
        attempts = attempts_for(client, project["id"], headers, "STYLE")

    assert response.status_code == 200
    assert len(attempts) == 1
    assert attempts[0]["attempt_number"] == 1
    assert attempts[0]["outcome"] == "SUCCEEDED"
    assert attempts[0]["started_at"]
    assert attempts[0]["ended_at"]
    assert attempts[0]["error"] is None
    assert "execution_owner" not in attempts[0]


def test_failure_and_manual_retry_keep_append_only_attempts(storage: tuple) -> None:
    executor = AttemptExecutor()
    executor.fail_next("CHARACTERS")
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        assert execute_step(client, project["id"], "STYLE", headers).status_code == 200

        failed = execute_step(client, project["id"], "CHARACTERS", headers)
        failed_attempts = attempts_for(client, project["id"], headers, "CHARACTERS")
        retried = execute_step(client, project["id"], "CHARACTERS", headers)
        final = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()

    assert failed.status_code == 502
    assert failed.json()["project"]["completed_stage"] == "STYLE_SET"
    assert failed.json()["project"]["step_state"] == "FAILED"
    assert len(failed_attempts) == 1
    assert failed_attempts[0]["outcome"] == "FAILED"
    assert failed_attempts[0]["ended_at"]
    assert failed_attempts[0]["error"] == "Safe characters failure"
    assert retried.status_code == 200
    assert final["completed_stage"] == "CHARACTERS_GENERATED"
    assert [(item["attempt_number"], item["outcome"]) for item in final["attempts"] if item["step"] == "CHARACTERS"] == [
        (2, "SUCCEEDED"),
        (1, "FAILED"),
    ]


def test_attempt_error_is_sanitized_before_persistence(storage: tuple) -> None:
    executor = AttemptExecutor()
    executor.fail_next(
        "STYLE",
        "Authorization: Bearer secret-token api_key=private-key request failed\n"
        "Traceback: raw provider payload follows",
    )
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        failed = execute_step(client, project["id"], "STYLE", headers)
        assert failed.status_code == 502
        attempt = attempts_for(client, project["id"], headers, "STYLE")[0]

    assert attempt["outcome"] == "FAILED"
    assert "secret-token" not in attempt["error"]
    assert "private-key" not in attempt["error"]
    assert "Traceback" not in attempt["error"]
    assert "raw provider payload" not in attempt["error"]
    assert "[REDACTED]" in attempt["error"]
    assert "secret-token" not in failed.json()["project"]["step_error"]
    assert "private-key" not in failed.json()["detail"]


def test_recovery_interrupts_old_attempt_and_retry_uses_next_number(
    storage: tuple,
) -> None:
    executor = AttemptExecutor()
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, executor, process_instance_id="process-a")
    ) as old_client:
        headers = sign_in(old_client)
        project = create_project(old_client, headers)
        started_at = datetime.now(UTC).isoformat()
        with old_client.app.state.database.connect() as connection:
            connection.execute(
                """UPDATE projects
                   SET completed_stage = 'STYLE_SET', step_state = 'RUNNING',
                       active_step = 'CHARACTERS', step_started_at = ?,
                       execution_owner = 'process-a', style = 'Preserved style'
                   WHERE id = ?""",
                (started_at, project["id"]),
            )
            connection.execute(
                """INSERT INTO pipeline_attempts
                   (id, project_id, step, attempt_number, started_at, outcome)
                   VALUES ('attempt-old', ?, 'CHARACTERS', 1, ?, 'RUNNING')""",
                (project["id"], started_at),
            )

    with TestClient(
        create_app(database_path, data_dir, executor, process_instance_id="process-b")
    ) as client:
        recovered = client.post(
            f"/api/projects/{project['id']}/recover", headers=headers
        )
        after_recovery = recovered.json()
        retried = execute_step(client, project["id"], "CHARACTERS", headers)
        history = attempts_for(client, project["id"], headers, "CHARACTERS")

    assert recovered.status_code == 200
    assert after_recovery["step_state"] == "FAILED"
    assert len(after_recovery["attempts"]) == 1
    assert after_recovery["attempts"][0]["outcome"] == "INTERRUPTED"
    assert after_recovery["attempts"][0]["ended_at"]
    assert "backend restart" in after_recovery["attempts"][0]["error"].lower()
    assert retried.status_code == 200
    assert [(item["attempt_number"], item["outcome"]) for item in history] == [
        (2, "SUCCEEDED"),
        (1, "INTERRUPTED"),
    ]


def test_duplicate_requests_create_only_one_attempt(storage: tuple) -> None:
    executor = SlowAttemptExecutor()
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, executor, process_instance_id="process-a")
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        start = Event()

        def request_style() -> int:
            assert start.wait(timeout=5)
            return execute_step(client, project["id"], "STYLE", headers).status_code

        with ThreadPoolExecutor(max_workers=8) as pool:
            requests = [pool.submit(request_style) for _ in range(8)]
            start.set()
            statuses = [request.result(timeout=5) for request in requests]
        history = attempts_for(client, project["id"], headers, "STYLE")

    assert statuses.count(200) == 1
    assert statuses.count(409) == 7
    assert executor.calls == ["STYLE"]
    assert len(history) == 1
    assert history[0]["attempt_number"] == 1


def test_attempts_are_project_scoped_and_each_step_has_own_sequence(
    storage: tuple,
) -> None:
    executor = AttemptExecutor()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        owner = sign_in(client, "owner@example.com")
        other = sign_in(client, "other@example.com")
        project = create_project(client, owner)
        other_project = create_project(client, other)
        execute_step(client, project["id"], "STYLE", owner)
        execute_step(client, project["id"], "CHARACTERS", owner)
        execute_step(client, other_project["id"], "STYLE", other)

        owner_history = attempts_for(client, project["id"], owner)
        forbidden = client.get(f"/api/projects/{project['id']}", headers=other)

    assert [(item["step"], item["attempt_number"]) for item in owner_history] == [
        ("CHARACTERS", 1),
        ("STYLE", 1),
    ]
    assert forbidden.status_code == 404


def test_pre_bonus_database_is_migrated_without_manual_deletion(
    storage: tuple[Path, Path],
) -> None:
    database_path, data_dir = storage
    executor = AttemptExecutor()

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE IF EXISTS pipeline_attempts")

    with TestClient(create_app(database_path, data_dir, executor)) as client:
        detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()

    assert detail["attempts"] == []
