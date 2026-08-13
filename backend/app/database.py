import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    book_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_stage TEXT NOT NULL DEFAULT 'CREATED',
    step_state TEXT NOT NULL DEFAULT 'IDLE',
    active_step TEXT,
    step_started_at TEXT,
    step_error TEXT,
    execution_owner TEXT,
    style TEXT,
    gemini_file_uri TEXT,
    latest_interaction_id TEXT
);

CREATE INDEX IF NOT EXISTS projects_user_created_idx
ON projects(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order IN (0, 1)),
    portrait_path TEXT,
    image_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (image_state IN ('PENDING', 'GENERATING', 'READY', 'FAILED')),
    image_error TEXT,
    UNIQUE(project_id, sort_order)
);

CREATE INDEX IF NOT EXISTS characters_project_idx
ON characters(project_id, sort_order);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prompt TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order = 0),
    illustration_path TEXT,
    image_state TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (image_state IN ('PENDING', 'GENERATING', 'READY', 'FAILED')),
    image_error TEXT,
    UNIQUE(project_id, sort_order)
);

CREATE INDEX IF NOT EXISTS chapters_project_idx
ON chapters(project_id, sort_order);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_characters_for_portraits(connection)

    @staticmethod
    def _migrate_characters_for_portraits(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(characters)").fetchall()
        }
        additions = {
            "portrait_path": "TEXT",
            "image_state": (
                "TEXT NOT NULL DEFAULT 'PENDING' "
                "CHECK (image_state IN ('PENDING', 'GENERATING', 'READY', 'FAILED'))"
            ),
            "image_error": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE characters ADD COLUMN {name} {definition}"
                )
