from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def storage(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "app.db", tmp_path / "data"


@pytest.fixture
def client(storage: tuple[Path, Path]) -> TestClient:
    database_path, data_dir = storage
    with TestClient(create_app(database_path=database_path, data_dir=data_dir)) as test_client:
        yield test_client

