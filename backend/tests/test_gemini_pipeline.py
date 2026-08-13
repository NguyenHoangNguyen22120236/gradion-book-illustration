import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app


class RecordingGeminiClient:
    def __init__(self) -> None:
        self.upload_calls: list[Path] = []
        self.style_calls: list[dict[str, Any]] = []
        self.character_calls: list[str] = []
        self.fail_style_calls = 0
        self.fail_character_calls = 0
        self.generated_style = "Luminous watercolor with fine ink outlines"
        self.character_output: Any = {
            "characters": [
                {
                    "name": "Mole",
                    "image_prompt": "An adult Mole in a velvet waistcoat, warm window light",
                    "is_adult": True,
                },
                {
                    "name": "Rat",
                    "image_prompt": "An adult Water Rat in a linen jacket beside the river",
                    "is_adult": True,
                },
            ]
        }

    def upload_book(self, book_path: Path) -> str:
        self.upload_calls.append(book_path)
        return "https://generativelanguage.googleapis.com/v1beta/files/book-123"

    def create_style_interaction(
        self, file_uri: str, requested_style: str | None
    ) -> SimpleNamespace:
        self.style_calls.append(
            {"file_uri": file_uri, "requested_style": requested_style}
        )
        if self.fail_style_calls:
            self.fail_style_calls -= 1
            raise RuntimeError("Gemini style interaction failed")
        return SimpleNamespace(id="style-interaction-1", output_text=self.generated_style)

    def create_characters_interaction(
        self, previous_interaction_id: str
    ) -> SimpleNamespace:
        self.character_calls.append(previous_interaction_id)
        if self.fail_character_calls:
            self.fail_character_calls -= 1
            raise RuntimeError("Gemini character interaction failed")
        output = (
            self.character_output
            if isinstance(self.character_output, str)
            else json.dumps(self.character_output)
        )
        return SimpleNamespace(id="characters-interaction-2", output_text=output)


def sign_in(client: TestClient) -> dict[str, str]:
    token = client.post(
        "/api/session", json={"name": "Mira", "email": "mira@example.com"}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def create_project(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "River story",
            "book_text": "The complete local book text must not be resent later.",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_project_creation_is_local_only(storage: tuple[Path, Path]) -> None:
    gemini = RecordingGeminiClient()
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        create_project(client, headers)

    assert gemini.upload_calls == []
    assert gemini.style_calls == []
    assert gemini.character_calls == []


def test_style_upload_is_persisted_before_interaction_and_reused_on_retry(
    storage: tuple[Path, Path],
) -> None:
    gemini = RecordingGeminiClient()
    gemini.fail_style_calls = 1
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        failed = client.post(
            f"/api/projects/{project['id']}/steps/style", headers=headers
        )
        assert failed.status_code == 502
        assert failed.json()["project"]["step_state"] == "FAILED"

        with client.app.state.database.connect() as connection:
            after_failure = connection.execute(
                "SELECT gemini_file_uri FROM projects WHERE id = ?", (project["id"],)
            ).fetchone()
        assert after_failure["gemini_file_uri"].endswith("/files/book-123")

        retried = client.post(
            f"/api/projects/{project['id']}/steps/style", headers=headers
        )

    assert retried.status_code == 200
    assert len(gemini.upload_calls) == 1
    assert len(gemini.style_calls) == 2


def test_generated_style_and_interaction_id_are_persisted(
    storage: tuple[Path, Path],
) -> None:
    gemini = RecordingGeminiClient()
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        response = client.post(
            f"/api/projects/{project['id']}/steps/style", headers=headers
        )
        with client.app.state.database.connect() as connection:
            persisted = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project["id"],)
            ).fetchone()

    assert response.status_code == 200
    assert response.json()["style"] == gemini.generated_style
    assert response.json()["completed_stage"] == "STYLE_SET"
    assert response.json()["step_state"] == "IDLE"
    assert persisted["latest_interaction_id"] == "style-interaction-1"
    assert len(gemini.upload_calls) == 1


def test_user_style_is_sent_to_gemini_and_persisted(
    storage: tuple[Path, Path],
) -> None:
    gemini = RecordingGeminiClient()
    database_path, data_dir = storage
    requested_style = "Bold art nouveau linework"

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        response = client.post(
            f"/api/projects/{project['id']}/steps/style",
            headers=headers,
            json={"style": f"  {requested_style}  "},
        )

    assert response.status_code == 200
    assert response.json()["style"] == requested_style
    assert gemini.style_calls[0]["requested_style"] == requested_style


def test_characters_chain_from_style_without_another_book_upload(
    storage: tuple[Path, Path],
) -> None:
    gemini = RecordingGeminiClient()
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        client.post(f"/api/projects/{project['id']}/steps/style", headers=headers)
        response = client.post(
            f"/api/projects/{project['id']}/steps/characters", headers=headers
        )
        detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        with client.app.state.database.connect() as connection:
            persisted = connection.execute(
                "SELECT latest_interaction_id FROM projects WHERE id = ?",
                (project["id"],),
            ).fetchone()

    assert response.status_code == 200
    assert detail["completed_stage"] == "CHARACTERS_GENERATED"
    assert [item["name"] for item in detail["characters"]] == ["Mole", "Rat"]
    assert gemini.character_calls == ["style-interaction-1"]
    assert len(gemini.upload_calls) == 1
    assert persisted["latest_interaction_id"] == "characters-interaction-2"


def test_only_two_adult_characters_are_persisted(
    storage: tuple[Path, Path],
) -> None:
    gemini = RecordingGeminiClient()
    gemini.character_output = {
        "characters": [
            {"name": "Child", "image_prompt": "A young child", "is_adult": False},
            {"name": "Mole", "image_prompt": "Detailed adult Mole", "is_adult": True},
            {"name": "Rat", "image_prompt": "Detailed adult Rat", "is_adult": True},
            {"name": "Badger", "image_prompt": "Detailed adult Badger", "is_adult": True},
        ]
    }
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        client.post(f"/api/projects/{project['id']}/steps/style", headers=headers)
        response = client.post(
            f"/api/projects/{project['id']}/steps/characters", headers=headers
        )
        detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()

    assert response.status_code == 200
    assert [item["name"] for item in detail["characters"]] == ["Mole", "Rat"]


def test_malformed_characters_fail_without_partial_persistence_and_can_retry(
    storage: tuple[Path, Path],
) -> None:
    gemini = RecordingGeminiClient()
    gemini.character_output = '{"characters": [{"name": "Mole"}]}'
    database_path, data_dir = storage

    with TestClient(
        create_app(database_path, data_dir, gemini_client=gemini)
    ) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        style = client.post(
            f"/api/projects/{project['id']}/steps/style", headers=headers
        ).json()["style"]

        failed = client.post(
            f"/api/projects/{project['id']}/steps/characters", headers=headers
        )
        failed_detail = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        with client.app.state.database.connect() as connection:
            interaction_after_failure = connection.execute(
                "SELECT latest_interaction_id FROM projects WHERE id = ?",
                (project["id"],),
            ).fetchone()["latest_interaction_id"]

        gemini.character_output = {
            "characters": [
                {
                    "name": "Mole",
                    "image_prompt": "Detailed adult Mole portrait",
                    "is_adult": True,
                }
            ]
        }
        retried = client.post(
            f"/api/projects/{project['id']}/steps/characters", headers=headers
        )

    assert failed.status_code == 502
    assert failed_detail["completed_stage"] == "STYLE_SET"
    assert failed_detail["step_state"] == "FAILED"
    assert failed_detail["active_step"] == "CHARACTERS"
    assert failed_detail["style"] == style
    assert failed_detail["characters"] == []
    assert interaction_after_failure == "style-interaction-1"
    assert retried.status_code == 200
    assert retried.json()["completed_stage"] == "CHARACTERS_GENERATED"


def test_google_client_uses_file_reference_and_stateful_structured_interaction(
    tmp_path: Path,
) -> None:
    from app.gemini import GoogleGenAIClient

    class Files:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def upload(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(uri="gemini://uploaded-book")

    class Interactions:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def create(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(id=f"interaction-{len(self.calls)}", output_text="ok")

    sdk = SimpleNamespace(files=Files(), interactions=Interactions())
    gemini = GoogleGenAIClient(sdk_client=sdk, text_model="test-text-model")
    book_path = tmp_path / "book.txt"
    book_path.write_text("This full text should only be uploaded.", encoding="utf-8")

    uri = gemini.upload_book(book_path)
    gemini.create_style_interaction(uri, "Woodcut")
    gemini.create_characters_interaction("interaction-1")

    assert sdk.files.calls == [
        {
            "file": str(book_path),
            "config": {"mime_type": "text/plain", "display_name": "book.txt"},
        }
    ]
    style_call, character_call = sdk.interactions.calls
    assert style_call["model"] == "test-text-model"
    assert style_call["input"][1] == {
        "type": "document",
        "uri": "gemini://uploaded-book",
        "mime_type": "text/plain",
    }
    assert "This full text" not in json.dumps(style_call)
    assert character_call["previous_interaction_id"] == "interaction-1"
    assert "document" not in json.dumps(character_call)
    assert character_call["response_format"]["mime_type"] == "application/json"
    schema = character_call["response_format"]["schema"]
    assert schema["properties"]["characters"]["maxItems"] == 2
    assert "is_adult" in schema["properties"]["characters"]["items"]["required"]
