import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.gemini import GeneratedImage, GoogleGenAIClient
from app.main import create_app


PORTRAIT_BYTES = b"\x89PNG\r\n\x1a\nportrait"
ILLUSTRATION_BYTES = b"\x89PNG\r\n\x1a\nillustration"


class StageSevenGeminiClient:
    def __init__(self) -> None:
        self.chapter_calls: list[str] = []
        self.illustration_calls: list[dict[str, Any]] = []
        self.portrait_calls: list[str] = []
        self.fail_chapter_calls = 0
        self.fail_illustration_calls = 0
        self.chapter_output: Any = {
            "chapters": [
                {
                    "name": "The River Bank",
                    "image_prompt": (
                        "Mole and Rat, matching their established portraits, "
                        "share a picnic beside the bright river."
                    ),
                }
            ]
        }

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
            id="characters-interaction",
            output_text=json.dumps(
                {
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
            ),
        )

    def generate_portrait(self, prompt: str) -> GeneratedImage:
        self.portrait_calls.append(prompt)
        return GeneratedImage(data=PORTRAIT_BYTES, mime_type="image/png")

    def create_chapters_interaction(
        self, previous_interaction_id: str
    ) -> SimpleNamespace:
        self.chapter_calls.append(previous_interaction_id)
        if self.fail_chapter_calls:
            self.fail_chapter_calls -= 1
            raise RuntimeError("chapter provider failure")
        output = (
            self.chapter_output
            if isinstance(self.chapter_output, str)
            else json.dumps(self.chapter_output)
        )
        return SimpleNamespace(id="chapters-interaction", output_text=output)

    def generate_illustration(
        self, prompt: str, portrait_references: list[GeneratedImage]
    ) -> GeneratedImage:
        self.illustration_calls.append(
            {"prompt": prompt, "portrait_references": portrait_references}
        )
        if self.fail_illustration_calls:
            self.fail_illustration_calls -= 1
            raise RuntimeError("illustration provider failure")
        return GeneratedImage(data=ILLUSTRATION_BYTES, mime_type="image/png")


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


def execute_step(
    client: TestClient, headers: dict[str, str], project_id: str, step: str
):
    return client.post(
        f"/api/projects/{project_id}/steps/{step.lower()}", headers=headers
    )


def prepare_portraits(
    client: TestClient, headers: dict[str, str], project_id: str
) -> None:
    for step in ("STYLE", "CHARACTERS", "PORTRAITS"):
        assert execute_step(client, headers, project_id, step).status_code == 200


def prepare_chapter(
    client: TestClient, headers: dict[str, str], project_id: str
) -> None:
    prepare_portraits(client, headers, project_id)
    assert execute_step(client, headers, project_id, "CHAPTERS").status_code == 200


def test_chapters_cannot_execute_before_portraits_are_complete(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        response = execute_step(client, headers, project["id"], "CHAPTERS")

    assert response.status_code == 409
    assert gemini.chapter_calls == []


def test_chapters_chain_context_persist_only_first_and_advance_state(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    gemini.chapter_output = {
        "chapters": [
            {"name": "First", "image_prompt": "Mole and Rat beside the river"},
            {"name": "Second", "image_prompt": "Badger near the Wild Wood"},
        ]
    }
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_portraits(client, headers, project["id"])

        response = execute_step(client, headers, project["id"], "CHAPTERS")
        detail = client.get(f"/api/projects/{project['id']}", headers=headers).json()
        with client.app.state.database.connect() as connection:
            persisted_interaction = connection.execute(
                "SELECT latest_interaction_id FROM projects WHERE id = ?",
                (project["id"],),
            ).fetchone()["latest_interaction_id"]
            chapter_count = connection.execute(
                "SELECT COUNT(*) AS count FROM chapters WHERE project_id = ?",
                (project["id"],),
            ).fetchone()["count"]

    assert response.status_code == 200
    assert gemini.chapter_calls == ["characters-interaction"]
    assert persisted_interaction == "chapters-interaction"
    assert chapter_count == 1
    assert [chapter["name"] for chapter in detail["chapters"]] == ["First"]
    assert detail["chapters"][0]["prompt"] == "Mole and Rat beside the river"
    assert detail["completed_stage"] == "CHAPTERS_GENERATED"
    assert detail["step_state"] == "IDLE"
    assert detail["active_step"] is None
    assert detail["step_error"] is None


def test_failed_chapters_preserve_portraits_and_retry_only_chapters(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    gemini.fail_chapter_calls = 1
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_portraits(client, headers, project["id"])
        portrait_call_count = len(gemini.portrait_calls)

        failed = execute_step(client, headers, project["id"], "CHAPTERS")
        failed_detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        retried = execute_step(client, headers, project["id"], "CHAPTERS")

    assert failed.status_code == 502
    assert failed_detail["completed_stage"] == "PORTRAITS_GENERATED"
    assert failed_detail["step_state"] == "FAILED"
    assert failed_detail["active_step"] == "CHAPTERS"
    assert failed_detail["step_error"]
    assert all(item["image_state"] == "READY" for item in failed_detail["characters"])
    assert retried.status_code == 200
    assert retried.json()["completed_stage"] == "CHAPTERS_GENERATED"
    assert gemini.chapter_calls == ["characters-interaction", "characters-interaction"]
    assert len(gemini.portrait_calls) == portrait_call_count


def test_illustrations_cannot_execute_before_chapters_are_complete(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_portraits(client, headers, project["id"])

        response = execute_step(client, headers, project["id"], "ILLUSTRATIONS")

    assert response.status_code == 409
    assert gemini.illustration_calls == []


def test_illustration_uses_persisted_prompt_and_portraits_then_marks_done(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_chapter(client, headers, project["id"])
        chapter_before = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()["chapters"][0]
        chapter_call_count = len(gemini.chapter_calls)
        portrait_call_count = len(gemini.portrait_calls)

        response = execute_step(client, headers, project["id"], "ILLUSTRATIONS")
        detail = client.get(f"/api/projects/{project['id']}", headers=headers).json()
        chapter = detail["chapters"][0]
        served = client.get(chapter["illustration_url"], headers=headers)
        with client.app.state.database.connect() as connection:
            persisted_path = connection.execute(
                "SELECT illustration_path FROM chapters WHERE id = ?",
                (chapter["id"],),
            ).fetchone()["illustration_path"]

    assert response.status_code == 200
    assert detail["completed_stage"] == "DONE"
    assert detail["step_state"] == "IDLE"
    assert detail["active_step"] is None
    assert detail["step_error"] is None
    assert len(gemini.illustration_calls) == 1
    image_call = gemini.illustration_calls[0]
    assert chapter_before["prompt"] in image_call["prompt"]
    assert "Watercolor style" in image_call["prompt"]
    assert [image.data for image in image_call["portrait_references"]] == [
        PORTRAIT_BYTES,
        PORTRAIT_BYTES,
    ]
    assert len(gemini.chapter_calls) == chapter_call_count
    assert len(gemini.portrait_calls) == portrait_call_count
    assert persisted_path == (
        f"images/{project['id']}/chapters/{chapter['id']}.png"
    )
    assert (data_dir / persisted_path).read_bytes() == ILLUSTRATION_BYTES
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == ILLUSTRATION_BYTES


def test_failed_illustration_preserves_chapter_and_portraits_and_retries_only_image(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    gemini.fail_illustration_calls = 1
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        prepare_chapter(client, headers, project["id"])
        chapter_call_count = len(gemini.chapter_calls)
        portrait_call_count = len(gemini.portrait_calls)

        failed = execute_step(client, headers, project["id"], "ILLUSTRATIONS")
        failed_detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        retried = execute_step(client, headers, project["id"], "ILLUSTRATIONS")

    assert failed.status_code == 502
    assert failed_detail["completed_stage"] == "CHAPTERS_GENERATED"
    assert failed_detail["step_state"] == "FAILED"
    assert failed_detail["active_step"] == "ILLUSTRATIONS"
    assert failed_detail["step_error"]
    assert len(failed_detail["chapters"]) == 1
    assert failed_detail["chapters"][0]["image_state"] == "FAILED"
    assert all(item["image_state"] == "READY" for item in failed_detail["characters"])
    assert retried.status_code == 200
    assert retried.json()["completed_stage"] == "DONE"
    assert len(gemini.illustration_calls) == 2
    assert len(gemini.chapter_calls) == chapter_call_count
    assert len(gemini.portrait_calls) == portrait_call_count


def test_illustration_serving_is_scoped_to_project_owner(
    storage: tuple[Path, Path],
) -> None:
    gemini = StageSevenGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        owner = sign_in(client, "owner@example.com")
        other = sign_in(client, "other@example.com")
        project = create_project(client, owner)
        prepare_chapter(client, owner, project["id"])
        execute_step(client, owner, project["id"], "ILLUSTRATIONS")
        chapter = client.get(
            f"/api/projects/{project['id']}", headers=owner
        ).json()["chapters"][0]

        own_image = client.get(chapter["illustration_url"], headers=owner)
        forbidden = client.get(chapter["illustration_url"], headers=other)

    assert own_image.status_code == 200
    assert forbidden.status_code == 404


def test_google_client_chains_chapters_and_sends_inline_portrait_references() -> None:
    interactions = SimpleNamespace(calls=[])

    def create(**kwargs: Any) -> SimpleNamespace:
        interactions.calls.append(kwargs)
        if len(interactions.calls) == 1:
            return SimpleNamespace(id="chapter-interaction", output_text='{"chapters": []}')
        return SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(ILLUSTRATION_BYTES).decode("ascii"),
                mime_type="image/png",
            )
        )

    interactions.create = create
    sdk = SimpleNamespace(interactions=interactions)
    gemini = GoogleGenAIClient(
        sdk_client=sdk,
        text_model="test-text-model",
        image_model="test-image-model",
    )
    references = [
        GeneratedImage(data=b"portrait-one", mime_type="image/png"),
        GeneratedImage(data=b"portrait-two", mime_type="image/jpeg"),
    ]

    gemini.create_chapters_interaction("characters-interaction")
    image = gemini.generate_illustration("Persisted scene prompt", references)

    chapter_call, illustration_call = interactions.calls
    assert chapter_call["previous_interaction_id"] == "characters-interaction"
    assert chapter_call["response_format"]["mime_type"] == "application/json"
    assert (
        chapter_call["response_format"]["schema"]["properties"]["chapters"]["maxItems"]
        == 1
    )
    assert "document" not in json.dumps(chapter_call)
    assert illustration_call["model"] == "test-image-model"
    assert illustration_call["input"][0] == {
        "type": "text",
        "text": "Persisted scene prompt",
    }
    assert illustration_call["input"][1:] == [
        {
            "type": "image",
            "data": base64.b64encode(b"portrait-one").decode("ascii"),
            "mime_type": "image/png",
        },
        {
            "type": "image",
            "data": base64.b64encode(b"portrait-two").decode("ascii"),
            "mime_type": "image/jpeg",
        },
    ]
    assert image == GeneratedImage(data=ILLUSTRATION_BYTES, mime_type="image/png")
