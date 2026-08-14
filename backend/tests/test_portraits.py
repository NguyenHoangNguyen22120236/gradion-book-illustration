import base64
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.database import Database
from app.gemini import GeneratedImage, GoogleGenAIClient
from app.main import create_app


PNG_BYTES = b"\x89PNG\r\n\x1a\nportrait"


class PortraitGeminiClient:
    def __init__(self) -> None:
        self.character_output = {
            "characters": [
                {
                    "name": "Mole",
                    "image_prompt": "An adult Mole in a velvet waistcoat",
                    "is_adult": True,
                },
                {
                    "name": "Rat",
                    "image_prompt": "An adult Water Rat in a linen jacket",
                    "is_adult": True,
                },
            ]
        }
        self.portrait_calls: list[str] = []
        self.failures: dict[str, int] = {}
        self._lock = Lock()

    def upload_book(self, book_path: Path) -> str:
        del book_path
        return "gemini://book"

    def create_style_interaction(
        self, file_uri: str, requested_style: str | None
    ) -> SimpleNamespace:
        del file_uri, requested_style
        return SimpleNamespace(id="style-interaction", output_text="Watercolor style")

    def create_characters_interaction(
        self, previous_interaction_id: str
    ) -> SimpleNamespace:
        del previous_interaction_id
        return SimpleNamespace(
            id="characters-interaction", output_text=json.dumps(self.character_output)
        )

    def generate_portrait(self, prompt: str) -> GeneratedImage:
        with self._lock:
            self.portrait_calls.append(prompt)
            for name, remaining in self.failures.items():
                if name in prompt and remaining:
                    self.failures[name] -= 1
                    raise RuntimeError("provider details must not leak")
        return GeneratedImage(data=PNG_BYTES, mime_type="image/png")


class BlockingSecondPortraitClient(PortraitGeminiClient):
    def __init__(self) -> None:
        super().__init__()
        self.second_started = Event()
        self.release_second = Event()

    def generate_portrait(self, prompt: str) -> GeneratedImage:
        with self._lock:
            call_number = len(self.portrait_calls) + 1
        if call_number == 2:
            self.second_started.set()
            assert self.release_second.wait(timeout=5)
        return super().generate_portrait(prompt)


class BlockingPortraitClient(PortraitGeminiClient):
    def __init__(self) -> None:
        super().__init__()
        self.character_output["characters"] = self.character_output["characters"][:1]
        self.started = Event()
        self.release = Event()

    def generate_portrait(self, prompt: str) -> GeneratedImage:
        self.started.set()
        assert self.release.wait(timeout=5)
        return super().generate_portrait(prompt)


def sign_in(
    client: TestClient, email: str = "mira@example.com"
) -> dict[str, str]:
    token = client.post(
        "/api/session", json={"name": "Mira", "email": email}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_project(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/projects",
        headers=headers,
        json={"title": "River story", "book_text": "A complete local book."},
    )
    assert response.status_code == 201
    return response.json()


def prepare_characters(
    client: TestClient, headers: dict[str, str], project_id: str
) -> None:
    assert client.post(
        f"/api/projects/{project_id}/steps/style", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/projects/{project_id}/steps/characters", headers=headers
    ).status_code == 200


def test_successful_portraits_are_saved_and_persisted(
    storage: tuple[Path, Path],
) -> None:
    gemini = PortraitGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_characters(client, headers, project["id"])

        pending = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()["characters"]
        response = client.post(
            f"/api/projects/{project['id']}/steps/portraits", headers=headers
        )
        detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()

    assert [item["image_state"] for item in pending] == ["PENDING", "PENDING"]
    assert response.status_code == 200
    assert detail["completed_stage"] == "PORTRAITS_GENERATED"
    assert detail["step_state"] == "IDLE"
    assert detail["active_step"] is None
    assert [item["image_state"] for item in detail["characters"]] == [
        "READY",
        "READY",
    ]
    assert all(item["portrait_url"] for item in detail["characters"])
    assert len(gemini.portrait_calls) == 2
    assert "An adult Mole" in gemini.portrait_calls[0]
    assert "Watercolor style" in gemini.portrait_calls[0]
    for character in detail["characters"]:
        expected_path = (
            data_dir
            / "images"
            / project["id"]
            / "characters"
            / f"{character['id']}.png"
        )
        assert expected_path.read_bytes() == PNG_BYTES


def test_first_portrait_is_observable_while_second_is_generating(
    storage: tuple[Path, Path],
) -> None:
    gemini = BlockingSecondPortraitClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_characters(client, headers, project["id"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(
                client.post,
                f"/api/projects/{project['id']}/steps/portraits",
                headers=headers,
            )
            assert gemini.second_started.wait(timeout=5)
            during = client.get(
                f"/api/projects/{project['id']}", headers=headers
            ).json()
            first_url = during["characters"][0]["portrait_url"]
            first_image = client.get(first_url, headers=headers)
            gemini.release_second.set()
            completed = running.result(timeout=5)

    assert during["step_state"] == "RUNNING"
    assert [item["image_state"] for item in during["characters"]] == [
        "READY",
        "GENERATING",
    ]
    assert first_image.content == PNG_BYTES
    assert completed.status_code == 200


def test_partial_failure_preserves_completed_portrait_and_project_context(
    storage: tuple[Path, Path],
) -> None:
    gemini = PortraitGeminiClient()
    gemini.failures["Water Rat"] = 1
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_characters(client, headers, project["id"])
        with client.app.state.database.connect() as connection:
            before = dict(
                connection.execute(
                    "SELECT gemini_file_uri, latest_interaction_id FROM projects WHERE id = ?",
                    (project["id"],),
                ).fetchone()
            )

        failed = client.post(
            f"/api/projects/{project['id']}/steps/portraits", headers=headers
        )
        detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        first_image = client.get(
            detail["characters"][0]["portrait_url"], headers=headers
        )
        with client.app.state.database.connect() as connection:
            after = dict(
                connection.execute(
                    "SELECT gemini_file_uri, latest_interaction_id FROM projects WHERE id = ?",
                    (project["id"],),
                ).fetchone()
            )

    assert failed.status_code == 502
    assert detail["completed_stage"] == "CHARACTERS_GENERATED"
    assert detail["step_state"] == "FAILED"
    assert detail["active_step"] == "PORTRAITS"
    assert "Rat" in detail["step_error"]
    assert [item["image_state"] for item in detail["characters"]] == [
        "READY",
        "FAILED",
    ]
    assert detail["characters"][1]["image_error"]
    assert "provider details" not in detail["step_error"]
    assert first_image.status_code == 200
    assert first_image.content == PNG_BYTES
    assert detail["style"] == "Watercolor style"
    assert [item["name"] for item in detail["characters"]] == ["Mole", "Rat"]
    assert after == before


def test_manual_retry_generates_only_the_failed_portrait(
    storage: tuple[Path, Path],
) -> None:
    gemini = PortraitGeminiClient()
    gemini.failures["Water Rat"] = 1
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_characters(client, headers, project["id"])

        assert client.post(
            f"/api/projects/{project['id']}/steps/portraits", headers=headers
        ).status_code == 502
        retried = client.post(
            f"/api/projects/{project['id']}/steps/portraits", headers=headers
        )
        detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()

    assert retried.status_code == 200
    assert detail["completed_stage"] == "PORTRAITS_GENERATED"
    assert [item["image_state"] for item in detail["characters"]] == [
        "READY",
        "READY",
    ]
    assert sum("adult Mole" in prompt for prompt in gemini.portrait_calls) == 1
    assert sum("Water Rat" in prompt for prompt in gemini.portrait_calls) == 2


def test_concurrent_portrait_requests_do_not_duplicate_image_calls(
    storage: tuple[Path, Path],
) -> None:
    gemini = BlockingPortraitClient()
    database_path, data_dir = storage

    with TestClient(
        create_app(
            database_path,
            data_dir,
            gemini_client=gemini,
            process_instance_id="process-a",
        )
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_characters(client, headers, project["id"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(
                client.post,
                f"/api/projects/{project['id']}/steps/portraits",
                headers=headers,
            )
            assert gemini.started.wait(timeout=5)
            duplicate = client.post(
                f"/api/projects/{project['id']}/steps/portraits", headers=headers
            )
            gemini.release.set()
            completed = running.result(timeout=5)

    assert duplicate.status_code == 409
    assert completed.status_code == 200
    assert len(gemini.portrait_calls) == 1


def test_interrupted_portrait_recovery_preserves_ready_items_and_fails_only_in_flight_item(
    storage: tuple[Path, Path],
) -> None:
    gemini = PortraitGeminiClient()
    database_path, data_dir = storage

    with TestClient(
        create_app(
            database_path,
            data_dir,
            gemini_client=gemini,
            process_instance_id="process-b",
        )
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_characters(client, headers, project["id"])
        characters = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()["characters"]
        ready_relative_path = (
            Path("images")
            / project["id"]
            / "characters"
            / f"{characters[0]['id']}.png"
        )
        ready_path = data_dir / ready_relative_path
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_bytes(PNG_BYTES)

        with client.app.state.database.connect() as connection:
            connection.execute(
                """UPDATE projects
                   SET step_state = 'RUNNING', active_step = 'PORTRAITS',
                       step_started_at = '2026-01-01T00:00:00+00:00',
                       execution_owner = 'process-a'
                   WHERE id = ?""",
                (project["id"],),
            )
            connection.execute(
                """UPDATE characters
                   SET image_state = 'READY', portrait_path = ?
                   WHERE id = ?""",
                (ready_relative_path.as_posix(), characters[0]["id"]),
            )
            connection.execute(
                "UPDATE characters SET image_state = 'GENERATING' WHERE id = ?",
                (characters[1]["id"],),
            )

        interrupted = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        recovered = client.post(
            f"/api/projects/{project['id']}/recover", headers=headers
        )

    assert interrupted["can_recover"] is True
    assert recovered.status_code == 200
    state = recovered.json()
    assert state["step_state"] == "FAILED"
    assert state["active_step"] == "PORTRAITS"
    assert [item["image_state"] for item in state["characters"]] == [
        "READY",
        "FAILED",
    ]
    assert state["characters"][0]["portrait_url"]
    assert "backend restart" in state["characters"][1]["image_error"]


def test_portrait_image_serving_is_scoped_to_project_owner(
    storage: tuple[Path, Path],
) -> None:
    gemini = PortraitGeminiClient()
    gemini.character_output["characters"] = gemini.character_output["characters"][:1]
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        owner = sign_in(client, "owner@example.com")
        other = sign_in(client, "other@example.com")
        project = create_project(client, owner)
        prepare_characters(client, owner, project["id"])
        client.post(
            f"/api/projects/{project['id']}/steps/portraits", headers=owner
        )
        character = client.get(
            f"/api/projects/{project['id']}", headers=owner
        ).json()["characters"][0]

        own_image = client.get(character["portrait_url"], headers=owner)
        forbidden = client.get(character["portrait_url"], headers=other)
        missing = client.get(
            f"/api/projects/{project['id']}/characters/missing/portrait",
            headers=owner,
        )

    assert own_image.status_code == 200
    assert own_image.headers["content-type"] == "image/png"
    assert own_image.content == PNG_BYTES
    assert forbidden.status_code == 404
    assert missing.status_code == 404


def test_existing_database_schema_is_extended_safely(tmp_path: Path) -> None:
    database_path = tmp_path / "existing.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE characters (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                sort_order INTEGER NOT NULL
            );
            """
        )

    Database(database_path).initialize()

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(characters)")
        }

    assert {"portrait_path", "image_state", "image_error"} <= columns.keys()
    assert columns["image_state"][4] == "'PENDING'"


def test_google_client_generates_portrait_with_configured_image_model() -> None:
    interactions = SimpleNamespace(calls=[])

    def create(**kwargs: Any) -> SimpleNamespace:
        interactions.calls.append(kwargs)
        return SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(PNG_BYTES).decode("ascii"),
                mime_type="image/png",
            )
        )

    interactions.create = create
    sdk = SimpleNamespace(interactions=interactions)
    gemini = GoogleGenAIClient(
        sdk_client=sdk,
        text_model="test-text-model",
        image_model="gemini-3.1-flash-image",
    )

    image = gemini.generate_portrait("A standalone portrait")

    assert interactions.calls == [
        {
            "model": "gemini-3.1-flash-image",
            "input": "A standalone portrait",
        }
    ]
    assert image == GeneratedImage(data=PNG_BYTES, mime_type="image/png")
