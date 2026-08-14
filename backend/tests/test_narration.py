import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.gemini import GoogleGenAIClient
from app.main import create_app


PCM_BYTES = b"\x01\x00\x02\x00narrated scene"
TRANSCRIPT = (
    "Mole stepped into the spring sunshine and paused beside the river. "
    "Rat greeted him warmly from the bank."
)


class NarrationGeminiClient:
    def __init__(self) -> None:
        self.transcript_calls: list[str] = []
        self.speech_calls: list[str] = []
        self.speech_failures = 0
        self._lock = Lock()

    def create_narration_transcript(self, file_uri: str) -> SimpleNamespace:
        with self._lock:
            self.transcript_calls.append(file_uri)
        return SimpleNamespace(id="narration-transcript-1", output_text=TRANSCRIPT)

    def generate_speech(self, transcript: str) -> SimpleNamespace:
        with self._lock:
            self.speech_calls.append(transcript)
            if self.speech_failures:
                self.speech_failures -= 1
                raise RuntimeError("raw TTS provider detail")
        return SimpleNamespace(data=PCM_BYTES)


class BlockingNarrationGeminiClient(NarrationGeminiClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def create_narration_transcript(self, file_uri: str) -> SimpleNamespace:
        self.started.set()
        assert self.release.wait(timeout=5)
        return super().create_narration_transcript(file_uri)


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


def mark_pipeline_done(client: TestClient, project_id: str) -> None:
    with client.app.state.database.connect() as connection:
        connection.execute(
            """UPDATE projects
               SET completed_stage = 'DONE', style = 'Watercolor',
                   gemini_file_uri = 'gemini://uploaded-book'
               WHERE id = ?""",
            (project_id,),
        )
        connection.execute(
            """INSERT INTO characters
               (id, project_id, name, prompt, sort_order, portrait_path, image_state)
               VALUES ('mole', ?, 'Mole', 'Preserved Mole prompt', 0,
                       'images/mole.png', 'READY')""",
            (project_id,),
        )
        connection.execute(
            """INSERT INTO chapters
               (id, project_id, name, prompt, sort_order, illustration_path, image_state)
               VALUES ('chapter-1', ?, 'Spring Cleaning',
                       'Preserved illustration prompt', 0,
                       'images/chapter.png', 'READY')""",
            (project_id,),
        )


def start_narration(
    client: TestClient, project_id: str, headers: dict[str, str]
):
    return client.post(f"/api/projects/{project_id}/narration", headers=headers)


def test_narration_requires_done_pipeline_and_explicit_authenticated_action(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)

        initial = client.get(f"/api/projects/{project['id']}", headers=headers)
        unauthenticated = start_narration(client, project["id"], {})
        too_early = start_narration(client, project["id"], headers)

    assert initial.json()["narration"] == {
        "state": "IDLE",
        "started_at": None,
        "error": None,
        "can_recover": False,
        "audio_url": None,
    }
    assert gemini.transcript_calls == []
    assert gemini.speech_calls == []
    assert unauthenticated.status_code == 401
    assert too_early.status_code == 409


def test_another_user_cannot_start_or_access_narration(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        owner = sign_in(client, "owner@example.com")
        other = sign_in(client, "other@example.com")
        project = create_project(client, owner)
        mark_pipeline_done(client, project["id"])

        owner_start = start_narration(client, project["id"], owner)
        forbidden_start = start_narration(client, project["id"], other)
        forbidden_detail = client.get(
            f"/api/projects/{project['id']}", headers=other
        )

    assert owner_start.status_code == 200
    assert forbidden_start.status_code == 404
    assert forbidden_detail.status_code == 404
    assert gemini.transcript_calls == ["gemini://uploaded-book"]
    assert gemini.speech_calls == [TRANSCRIPT]


def test_narration_claim_is_atomic_and_running_state_is_persisted(
    storage: tuple[Path, Path],
) -> None:
    gemini = BlockingNarrationGeminiClient()
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
        mark_pipeline_done(client, project["id"])

        with ThreadPoolExecutor(max_workers=1) as pool:
            running_request = pool.submit(
                start_narration, client, project["id"], headers
            )
            assert gemini.started.wait(timeout=5)
            during = client.get(
                f"/api/projects/{project['id']}", headers=headers
            ).json()
            duplicate = start_narration(client, project["id"], headers)
            gemini.release.set()
            completed = running_request.result(timeout=5)

    assert during["completed_stage"] == "DONE"
    assert during["narration"]["state"] == "RUNNING"
    assert during["narration"]["started_at"]
    assert during["narration"]["can_recover"] is False
    assert duplicate.status_code == 409
    assert completed.status_code == 200
    assert gemini.transcript_calls == ["gemini://uploaded-book"]
    assert gemini.speech_calls == [TRANSCRIPT]


def test_success_stores_wav_and_completed_state(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        mark_pipeline_done(client, project["id"])

        response = start_narration(client, project["id"], headers)
        detail = client.get(f"/api/projects/{project['id']}", headers=headers).json()
        with client.app.state.database.connect() as connection:
            persisted = connection.execute(
                "SELECT state, transcript, audio_path FROM narrations WHERE project_id = ?",
                (project["id"],),
            ).fetchone()

    assert response.status_code == 200
    assert detail["completed_stage"] == "DONE"
    assert detail["narration"]["state"] == "COMPLETED"
    assert detail["narration"]["error"] is None
    assert detail["narration"]["audio_url"] == (
        f"/api/projects/{project['id']}/narration/audio"
    )
    assert persisted["transcript"] == TRANSCRIPT
    assert persisted["audio_path"] == f"audio/{project['id']}/narration.wav"
    audio_path = data_dir / persisted["audio_path"]
    assert audio_path.read_bytes().startswith(b"RIFF")
    assert PCM_BYTES in audio_path.read_bytes()


def test_failure_is_persisted_without_touching_required_outputs_or_auto_retry(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    gemini.speech_failures = 1
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        mark_pipeline_done(client, project["id"])
        with client.app.state.database.connect() as connection:
            before_project = dict(
                connection.execute(
                    "SELECT completed_stage, style FROM projects WHERE id = ?",
                    (project["id"],),
                ).fetchone()
            )
            before_character = dict(
                connection.execute(
                    "SELECT name, prompt, portrait_path, image_state FROM characters WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()
            )
            before_chapter = dict(
                connection.execute(
                    "SELECT name, prompt, illustration_path, image_state FROM chapters WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()
            )

        failed = start_narration(client, project["id"], headers)
        detail = client.get(f"/api/projects/{project['id']}", headers=headers).json()
        with client.app.state.database.connect() as connection:
            after_project = dict(
                connection.execute(
                    "SELECT completed_stage, style FROM projects WHERE id = ?",
                    (project["id"],),
                ).fetchone()
            )
            after_character = dict(
                connection.execute(
                    "SELECT name, prompt, portrait_path, image_state FROM characters WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()
            )
            after_chapter = dict(
                connection.execute(
                    "SELECT name, prompt, illustration_path, image_state FROM chapters WHERE project_id = ?",
                    (project["id"],),
                ).fetchone()
            )

    assert failed.status_code == 502
    assert detail["completed_stage"] == "DONE"
    assert detail["narration"]["state"] == "FAILED"
    assert detail["narration"]["error"]
    assert "raw TTS provider detail" not in detail["narration"]["error"]
    assert after_project == before_project
    assert after_character == before_character
    assert after_chapter == before_chapter
    assert gemini.transcript_calls == ["gemini://uploaded-book"]
    assert gemini.speech_calls == [TRANSCRIPT]


def test_failed_narration_retries_only_once_and_reuses_persisted_transcript(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    gemini.speech_failures = 1
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        mark_pipeline_done(client, project["id"])

        failed = start_narration(client, project["id"], headers)
        retried = start_narration(client, project["id"], headers)

    assert failed.status_code == 502
    assert retried.status_code == 200
    assert retried.json()["narration"]["state"] == "COMPLETED"
    assert gemini.transcript_calls == ["gemini://uploaded-book"]
    assert gemini.speech_calls == [TRANSCRIPT, TRANSCRIPT]


def test_old_process_running_narration_requires_recovery_then_explicit_retry(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    database_path, data_dir = storage

    with TestClient(
        create_app(
            database_path,
            data_dir,
            gemini_client=gemini,
            process_instance_id="process-a",
        )
    ) as old_client:
        headers = sign_in(old_client)
        project = create_project(old_client, headers)
        mark_pipeline_done(old_client, project["id"])
        with old_client.app.state.database.connect() as connection:
            connection.execute(
                """INSERT INTO narrations
                   (project_id, state, started_at, execution_owner, transcript)
                   VALUES (?, 'RUNNING', '2026-01-01T00:00:00+00:00',
                           'process-a', ?)""",
                (project["id"], TRANSCRIPT),
            )

    with TestClient(
        create_app(
            database_path,
            data_dir,
            gemini_client=gemini,
            process_instance_id="process-b",
        )
    ) as client:
        interrupted = client.get(
            f"/api/projects/{project['id']}", headers=headers
        ).json()
        blocked_retry = start_narration(client, project["id"], headers)
        recovered = client.post(
            f"/api/projects/{project['id']}/narration/recover", headers=headers
        )

        assert interrupted["narration"]["state"] == "RUNNING"
        assert interrupted["narration"]["can_recover"] is True
        assert blocked_retry.status_code == 409
        assert recovered.status_code == 200
        assert recovered.json()["narration"]["state"] == "FAILED"
        assert "backend restart" in recovered.json()["narration"]["error"].lower()
        assert gemini.transcript_calls == []
        assert gemini.speech_calls == []

        retried = start_narration(client, project["id"], headers)

    assert retried.status_code == 200
    assert gemini.transcript_calls == []
    assert gemini.speech_calls == [TRANSCRIPT]


def test_completed_narration_is_not_regenerated(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        headers = sign_in(client)
        project = create_project(client, headers)
        mark_pipeline_done(client, project["id"])
        assert start_narration(client, project["id"], headers).status_code == 200

        repeated = start_narration(client, project["id"], headers)

    assert repeated.status_code == 409
    assert gemini.transcript_calls == ["gemini://uploaded-book"]
    assert gemini.speech_calls == [TRANSCRIPT]


def test_narration_audio_serving_requires_authentication_and_ownership(
    storage: tuple[Path, Path],
) -> None:
    gemini = NarrationGeminiClient()
    database_path, data_dir = storage

    with TestClient(create_app(database_path, data_dir, gemini_client=gemini)) as client:
        owner = sign_in(client, "owner@example.com")
        other = sign_in(client, "other@example.com")
        project = create_project(client, owner)
        mark_pipeline_done(client, project["id"])
        detail = start_narration(client, project["id"], owner).json()
        audio_url = detail["narration"]["audio_url"]

        own_audio = client.get(audio_url, headers=owner)
        unauthenticated = client.get(audio_url)
        wrong_user = client.get(audio_url, headers=other)

    assert own_audio.status_code == 200
    assert own_audio.headers["content-type"] == "audio/wav"
    assert own_audio.content.startswith(b"RIFF")
    assert unauthenticated.status_code == 401
    assert wrong_user.status_code == 404


def test_google_client_uses_bounded_book_excerpt_and_current_single_speaker_tts_api() -> None:
    interactions = SimpleNamespace(calls=[])

    def create(**kwargs: Any) -> SimpleNamespace:
        interactions.calls.append(kwargs)
        if len(interactions.calls) == 1:
            return SimpleNamespace(id="transcript-1", output_text=TRANSCRIPT)
        return SimpleNamespace(
            output_audio=SimpleNamespace(
                data=base64.b64encode(PCM_BYTES).decode("ascii")
            )
        )

    interactions.create = create
    sdk = SimpleNamespace(interactions=interactions)
    gemini = GoogleGenAIClient(
        sdk_client=sdk,
        text_model="test-text-model",
        image_model="test-image-model",
        tts_model="gemini-3.1-flash-tts-preview",
    )

    transcript = gemini.create_narration_transcript("gemini://uploaded-book")
    audio = gemini.generate_speech(transcript.output_text)

    transcript_call, speech_call = interactions.calls
    assert transcript_call["model"] == "test-text-model"
    assert transcript_call["input"][1] == {
        "type": "document",
        "uri": "gemini://uploaded-book",
        "mime_type": "text/plain",
    }
    assert "first chapter" in transcript_call["input"][0]["text"].lower()
    assert "500 words" in transcript_call["input"][0]["text"].lower()
    assert speech_call == {
        "model": "gemini-3.1-flash-tts-preview",
        "input": (
            "Synthesize the following book excerpt as speech. Read only the text under "
            f"TRANSCRIPT.\n\nTRANSCRIPT:\n{TRANSCRIPT}"
        ),
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": "Aoede"}]},
    }
    assert audio.data == PCM_BYTES
