import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def sign_in(client: TestClient, name: str, email: str) -> dict[str, str]:
    token = client.post("/api/session", json={"name": name, "email": email}).json()[
        "token"
    ]
    return {"Authorization": f"Bearer {token}"}


def create_project(
    client: TestClient,
    headers: dict[str, str],
    title: str = "The Wind in the Willows",
    book_text: str = "The Mole had been working very hard all the morning.",
    source_filename: str | None = None,
):
    return client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": title,
            "book_text": book_text,
            "source_filename": source_filename,
        },
    )


def test_create_project_persists_metadata_and_book_file(
    client: TestClient, storage: tuple[Path, Path]
) -> None:
    headers = sign_in(client, "Mira", "mira@example.com")

    response = create_project(client, headers)

    assert response.status_code == 201
    project = response.json()
    assert project["title"] == "The Wind in the Willows"
    assert project["completed_stage"] == "CREATED"
    assert project["book_text"].startswith("The Mole")
    assert (storage[1] / "books" / f"{project['id']}.txt").read_text(
        encoding="utf-8"
    ) == project["book_text"]


def test_create_project_validates_title_book_and_txt_filename(client: TestClient) -> None:
    headers = sign_in(client, "Mira", "mira@example.com")

    assert create_project(client, headers, title="  ").status_code == 422
    assert create_project(client, headers, book_text=" \n ").status_code == 422
    assert create_project(client, headers, source_filename="book.pdf").status_code == 422
    assert create_project(client, headers, source_filename="BOOK.TXT").status_code == 201


def test_list_and_get_projects_are_scoped_to_current_user(client: TestClient) -> None:
    mira = sign_in(client, "Mira", "mira@example.com")
    theo = sign_in(client, "Theo", "theo@example.com")
    mira_project = create_project(client, mira, title="Mira's book").json()
    create_project(client, theo, title="Theo's book")

    listing = client.get("/api/projects", headers=mira)
    assert listing.status_code == 200
    assert [project["title"] for project in listing.json()] == ["Mira's book"]

    detail = client.get(f"/api/projects/{mira_project['id']}", headers=mira)
    assert detail.status_code == 200
    assert detail.json()["book_text"].startswith("The Mole")
    assert client.get(f"/api/projects/{mira_project['id']}", headers=theo).status_code == 404


def test_user_project_and_book_survive_app_restart(
    storage: tuple[Path, Path]
) -> None:
    database_path, data_dir = storage
    with TestClient(create_app(database_path, data_dir)) as first_client:
        headers = sign_in(first_client, "Mira", "Mira@example.com")
        project = create_project(
            first_client,
            headers,
            title="Persistent story",
            book_text="This text must survive a process restart.",
        ).json()

    with TestClient(create_app(database_path, data_dir)) as restarted_client:
        new_headers = sign_in(restarted_client, "Mira changed", "mira@example.com")
        detail = restarted_client.get(
            f"/api/projects/{project['id']}", headers=new_headers
        )

    assert detail.status_code == 200
    assert detail.json()["title"] == "Persistent story"
    assert detail.json()["book_text"] == "This text must survive a process restart."


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, step: str, project: dict) -> dict:
        self.calls.append((step, project))
        return {}


def test_list_sample_books_returns_exact_frontend_catalogue(client: TestClient) -> None:
    headers = sign_in(client, "Mira", "mira@example.com")

    response = client.get("/api/sample-books", headers=headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "alice-in-wonderland",
            "title": "Alice’s Adventures in Wonderland",
            "author": "Lewis Carroll",
        },
        {
            "id": "wizard-of-oz",
            "title": "The Wonderful Wizard of Oz",
            "author": "L. Frank Baum",
        },
        {
            "id": "wind-in-the-willows",
            "title": "The Wind in the Willows",
            "author": "Kenneth Grahame",
        },
    ]
    assert all("book_text" not in sample for sample in response.json())


def test_create_project_from_sample_reuses_local_persistence_without_gemini(
    storage: tuple[Path, Path],
) -> None:
    database_path, data_dir = storage
    executor = RecordingExecutor()
    with TestClient(
        create_app(database_path, data_dir, pipeline_executor=executor)
    ) as client:
        headers = sign_in(client, "Mira", "mira@example.com")

        response = client.post(
            "/api/projects",
            headers=headers,
            json={
                "title": "My Alice Project",
                "sample_book_id": "alice-in-wonderland",
            },
        )

    assert response.status_code == 201
    project = response.json()
    assert project["completed_stage"] == "CREATED"
    assert project["step_state"] == "IDLE"
    assert project["active_step"] is None
    assert "Alice’s Adventures in Wonderland" in project["book_text"]
    assert (data_dir / "books" / f"{project['id']}.txt").read_text(
        encoding="utf-8"
    ) == project["book_text"]
    with sqlite3.connect(database_path) as connection:
        persisted = connection.execute(
            "SELECT gemini_file_uri, latest_interaction_id FROM projects WHERE id = ?",
            (project["id"],),
        ).fetchone()
    assert persisted == (None, None)
    assert executor.calls == []


def test_invalid_sample_id_creates_no_project_or_book_and_does_not_call_gemini(
    storage: tuple[Path, Path],
) -> None:
    database_path, data_dir = storage
    executor = RecordingExecutor()
    with TestClient(
        create_app(database_path, data_dir, pipeline_executor=executor)
    ) as client:
        headers = sign_in(client, "Mira", "mira@example.com")
        response = client.post(
            "/api/projects",
            headers=headers,
            json={"title": "Unknown sample", "sample_book_id": "../../secrets"},
        )

    assert response.status_code == 422
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert list((data_dir / "books").iterdir()) == []
    assert executor.calls == []


def test_project_creation_rejects_conflicting_or_missing_book_sources(
    client: TestClient,
) -> None:
    headers = sign_in(client, "Mira", "mira@example.com")

    conflicting = client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "Ambiguous",
            "book_text": "Pasted text",
            "sample_book_id": "wizard-of-oz",
        },
    )
    missing = client.post(
        "/api/projects", headers=headers, json={"title": "No source"}
    )
    filename_only = client.post(
        "/api/projects",
        headers=headers,
        json={"title": "Filename only", "source_filename": "book.txt"},
    )

    assert conflicting.status_code == 422
    assert missing.status_code == 422
    assert filename_only.status_code == 422
    assert client.get("/api/projects", headers=headers).json() == []


def test_sample_project_uses_normal_user_ownership(client: TestClient) -> None:
    mira = sign_in(client, "Mira", "mira@example.com")
    theo = sign_in(client, "Theo", "theo@example.com")
    project = client.post(
        "/api/projects",
        headers=mira,
        json={
            "title": "Mira's Oz",
            "sample_book_id": "wizard-of-oz",
        },
    ).json()

    assert client.get(f"/api/projects/{project['id']}", headers=mira).status_code == 200
    assert client.get(f"/api/projects/{project['id']}", headers=theo).status_code == 404
    assert client.get("/api/projects", headers=theo).json() == []
