import sqlite3

from fastapi.testclient import TestClient


def test_sign_in_creates_a_user_and_session(client: TestClient) -> None:
    response = client.post(
        "/api/session", json={"name": "Mira Hassan", "email": "Mira@example.com"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["name"] == "Mira Hassan"
    assert body["user"]["email"] == "mira@example.com"
    assert body["token"]

    current = client.get(
        "/api/session", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert current.status_code == 200
    assert current.json() == body["user"]


def test_sign_in_reuses_user_for_normalized_email(
    client: TestClient, storage: tuple
) -> None:
    first = client.post(
        "/api/session", json={"name": "Mira", "email": "Mira@example.com"}
    ).json()
    second = client.post(
        "/api/session", json={"name": "Different Name", "email": "  mira@example.com "}
    ).json()

    assert second["user"]["id"] == first["user"]["id"]
    assert second["user"]["name"] == "Mira"
    with sqlite3.connect(storage[0]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_sign_in_validates_name_and_email(client: TestClient) -> None:
    assert client.post("/api/session", json={"name": "", "email": "mira@example.com"}).status_code == 422
    assert client.post("/api/session", json={"name": "Mira", "email": "not-email"}).status_code == 422


def test_sign_out_invalidates_session(client: TestClient) -> None:
    token = client.post(
        "/api/session", json={"name": "Mira", "email": "mira@example.com"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.delete("/api/session", headers=headers).status_code == 204
    assert client.get("/api/session", headers=headers).status_code == 401
