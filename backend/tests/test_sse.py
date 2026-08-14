import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import PipelineExecutionError


class BlockingExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.entered = Event()
        self.release = Event()
        self.fail = fail

    def execute(self, step: str, project: Mapping[str, Any]) -> dict[str, Any]:
        del project
        self.calls.append(step)
        self.entered.set()
        assert self.release.wait(timeout=5)
        if self.fail:
            raise PipelineExecutionError("Deterministic provider failure")
        return {"style": "Pushed watercolor"} if step == "STYLE" else {}


class FakeRequest:
    def __init__(self) -> None:
        self.disconnected = False

    async def is_disconnected(self) -> bool:
        return self.disconnected


def sign_in(client: TestClient, email: str = "mira@example.com") -> dict[str, str]:
    token = client.post(
        "/api/session", json={"name": "Mira", "email": email}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_project(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/projects",
        headers=headers,
        json={"title": "River story", "book_text": "A persisted river story."},
    )
    assert response.status_code == 201
    return response.json()


def current_user_row(client: TestClient, headers: dict[str, str]):
    token = headers["Authorization"].removeprefix("Bearer ")
    with client.app.state.database.connect() as connection:
        return connection.execute(
            """SELECT users.* FROM users JOIN sessions ON sessions.user_id = users.id
               WHERE sessions.token = ?""",
            (token,),
        ).fetchone()


def sse_route(client: TestClient):
    route = next(
        (
            route
            for route in client.app.routes
            if getattr(route, "path", None) == "/api/projects/{project_id}/events"
        ),
        None,
    )
    assert route is not None, "the authenticated project SSE endpoint is not implemented"
    return route


async def open_stream(
    client: TestClient, project_id: str, headers: dict[str, str]
) -> tuple[Any, FakeRequest]:
    request = FakeRequest()
    response = await sse_route(client).endpoint(
        project_id=project_id,
        request=request,
        user=current_user_row(client, headers),
    )
    assert response.media_type == "text/event-stream"
    return response.body_iterator, request


async def next_project_state(iterator: AsyncIterator[str | bytes]) -> dict[str, Any]:
    while True:
        chunk = await asyncio.wait_for(iterator.__anext__(), timeout=1)
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        if text.startswith(":"):
            continue
        fields = {}
        for line in text.strip().splitlines():
            name, _, value = line.partition(":")
            fields[name] = value.lstrip()
        if fields.get("event") == "project-state":
            return json.loads(fields["data"])


async def close_stream(iterator: AsyncIterator[str | bytes], request: FakeRequest) -> None:
    request.disconnected = True
    await iterator.aclose()


def test_sse_requires_bearer_authentication(client: TestClient) -> None:
    response = client.get("/api/projects/unknown/events")

    assert response.status_code == 401


def test_sse_hides_another_users_project(client: TestClient) -> None:
    owner = sign_in(client, "owner@example.com")
    other = sign_in(client, "other@example.com")
    project = create_project(client, owner)

    response = client.get(f"/api/projects/{project['id']}/events", headers=other)

    assert response.status_code == 404


def test_sse_returns_not_found_before_opening_unknown_project(client: TestClient) -> None:
    headers = sign_in(client)

    response = client.get("/api/projects/missing/events", headers=headers)

    assert response.status_code == 404


def test_connecting_emits_the_same_authoritative_snapshot_as_project_detail(
    client: TestClient,
) -> None:
    headers = sign_in(client)
    project = create_project(client, headers)
    expected = client.get(f"/api/projects/{project['id']}", headers=headers).json()

    async def scenario() -> None:
        iterator, request = await open_stream(client, project["id"], headers)
        assert await next_project_state(iterator) == expected
        await close_stream(iterator, request)

    asyncio.run(scenario())


def test_claim_and_completion_are_pushed_from_persisted_state(
    storage: tuple[Path, Path],
) -> None:
    executor = BlockingExecutor()
    database_path, data_dir = storage
    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        async def scenario() -> None:
            iterator, request = await open_stream(client, project["id"], headers)
            await next_project_state(iterator)
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(
                    client.post,
                    f"/api/projects/{project['id']}/steps/style",
                    headers=headers,
                )
                assert executor.entered.wait(timeout=5)
                claimed = await next_project_state(iterator)
                assert claimed["step_state"] == "RUNNING"
                assert claimed["active_step"] == "STYLE"
                executor.release.set()
                assert running.result(timeout=5).status_code == 200
            completed = await next_project_state(iterator)
            assert completed["completed_stage"] == "STYLE_SET"
            assert completed["style"] == "Pushed watercolor"
            await close_stream(iterator, request)

        asyncio.run(scenario())


def test_two_subscribers_receive_the_same_state_and_only_one_step_executes(
    storage: tuple[Path, Path],
) -> None:
    executor = BlockingExecutor()
    database_path, data_dir = storage
    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        async def scenario() -> None:
            first, first_request = await open_stream(client, project["id"], headers)
            second, second_request = await open_stream(client, project["id"], headers)
            await next_project_state(first)
            await next_project_state(second)
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(
                    client.post,
                    f"/api/projects/{project['id']}/steps/style",
                    headers=headers,
                )
                assert executor.entered.wait(timeout=5)
                duplicate = client.post(
                    f"/api/projects/{project['id']}/steps/style", headers=headers
                )
                first_state = await next_project_state(first)
                second_state = await next_project_state(second)
                executor.release.set()
                completed = running.result(timeout=5)
            assert duplicate.status_code == 409
            assert completed.status_code == 200
            assert first_state == second_state
            await close_stream(first, first_request)
            await close_stream(second, second_request)

        asyncio.run(scenario())
    assert executor.calls == ["STYLE"]


def test_opening_streams_does_not_mutate_or_execute_pipeline(
    storage: tuple[Path, Path],
) -> None:
    executor = BlockingExecutor()
    database_path, data_dir = storage
    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        before = client.get(f"/api/projects/{project['id']}", headers=headers).json()

        async def scenario() -> None:
            first, first_request = await open_stream(client, project["id"], headers)
            second, second_request = await open_stream(client, project["id"], headers)
            await next_project_state(first)
            await next_project_state(second)
            await close_stream(first, first_request)
            await close_stream(second, second_request)

        asyncio.run(scenario())
        after = client.get(f"/api/projects/{project['id']}", headers=headers).json()

    assert after == before
    assert executor.calls == []


def test_step_failure_is_pushed(storage: tuple[Path, Path]) -> None:
    executor = BlockingExecutor(fail=True)
    database_path, data_dir = storage
    with TestClient(create_app(database_path, data_dir, executor)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        async def scenario() -> None:
            iterator, request = await open_stream(client, project["id"], headers)
            await next_project_state(iterator)
            with ThreadPoolExecutor(max_workers=1) as pool:
                running = pool.submit(
                    client.post,
                    f"/api/projects/{project['id']}/steps/style",
                    headers=headers,
                )
                assert executor.entered.wait(timeout=5)
                await next_project_state(iterator)
                executor.release.set()
                assert running.result(timeout=5).status_code == 502
            failed = await next_project_state(iterator)
            assert failed["step_state"] == "FAILED"
            assert failed["step_error"] == "Deterministic provider failure"
            await close_stream(iterator, request)

        asyncio.run(scenario())


def test_per_item_portrait_and_illustration_progress_is_pushable(client: TestClient) -> None:
    headers = sign_in(client)
    project = create_project(client, headers)

    async def scenario() -> None:
        iterator, request = await open_stream(client, project["id"], headers)
        await next_project_state(iterator)
        with client.app.state.database.connect() as connection:
            connection.execute(
                """INSERT INTO characters
                   (id, project_id, name, prompt, sort_order, image_state)
                   VALUES ('mole', ?, 'Mole', 'Mole prompt', 0, 'GENERATING')""",
                (project["id"],),
            )
            connection.execute(
                """INSERT INTO chapters
                   (id, project_id, name, prompt, sort_order, image_state)
                   VALUES ('chapter-1', ?, 'River Bank', 'Scene prompt', 0, 'GENERATING')""",
                (project["id"],),
            )
        client.app.state.project_events.publish(project["id"])
        progress = await next_project_state(iterator)
        assert progress["characters"][0]["image_state"] == "GENERATING"
        assert progress["chapters"][0]["image_state"] == "GENERATING"

        with client.app.state.database.connect() as connection:
            connection.execute(
                "UPDATE characters SET image_state = 'READY', portrait_path = 'images/mole.png' WHERE id = 'mole'"
            )
            connection.execute(
                "UPDATE chapters SET image_state = 'READY', illustration_path = 'images/chapter.png' WHERE id = 'chapter-1'"
            )
        client.app.state.project_events.publish(project["id"])
        ready = await next_project_state(iterator)
        assert ready["characters"][0]["image_state"] == "READY"
        assert ready["chapters"][0]["image_state"] == "READY"
        await close_stream(iterator, request)

    asyncio.run(scenario())


def test_manual_recovery_result_is_pushed(client: TestClient) -> None:
    headers = sign_in(client)
    project = create_project(client, headers)
    with client.app.state.database.connect() as connection:
        connection.execute(
            """UPDATE projects SET step_state = 'RUNNING', active_step = 'STYLE',
               step_started_at = '2026-01-01T00:00:00Z', execution_owner = 'old-process'
               WHERE id = ?""",
            (project["id"],),
        )

    async def scenario() -> None:
        iterator, request = await open_stream(client, project["id"], headers)
        initial = await next_project_state(iterator)
        assert initial["can_recover"] is True
        recovered = client.post(f"/api/projects/{project['id']}/recover", headers=headers)
        assert recovered.status_code == 200
        pushed = await next_project_state(iterator)
        assert pushed["step_state"] == "FAILED"
        assert pushed["can_recover"] is False
        await close_stream(iterator, request)

    asyncio.run(scenario())


def test_reconnect_reads_latest_sqlite_snapshot_without_event_history(client: TestClient) -> None:
    headers = sign_in(client)
    project = create_project(client, headers)

    async def scenario() -> None:
        first, first_request = await open_stream(client, project["id"], headers)
        await next_project_state(first)
        await close_stream(first, first_request)
        with client.app.state.database.connect() as connection:
            connection.execute(
                "UPDATE projects SET style = 'Persisted after disconnect', completed_stage = 'STYLE_SET' WHERE id = ?",
                (project["id"],),
            )
        second, second_request = await open_stream(client, project["id"], headers)
        latest = await next_project_state(second)
        assert latest["style"] == "Persisted after disconnect"
        assert latest["completed_stage"] == "STYLE_SET"
        await close_stream(second, second_request)

    asyncio.run(scenario())


def test_closing_one_subscription_cleans_it_up_without_breaking_another(client: TestClient) -> None:
    headers = sign_in(client)
    project = create_project(client, headers)

    async def scenario() -> None:
        first, first_request = await open_stream(client, project["id"], headers)
        second, second_request = await open_stream(client, project["id"], headers)
        await next_project_state(first)
        await next_project_state(second)
        assert client.app.state.project_events.subscriber_count(project["id"]) == 2
        await close_stream(first, first_request)
        assert client.app.state.project_events.subscriber_count(project["id"]) == 1
        with client.app.state.database.connect() as connection:
            connection.execute(
                "UPDATE projects SET style = 'Still connected' WHERE id = ?",
                (project["id"],),
            )
        client.app.state.project_events.publish(project["id"])
        assert (await next_project_state(second))["style"] == "Still connected"
        await close_stream(second, second_request)
        assert client.app.state.project_events.subscriber_count(project["id"]) == 0

    asyncio.run(scenario())
