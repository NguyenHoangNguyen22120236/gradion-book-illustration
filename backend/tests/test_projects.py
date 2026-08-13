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
