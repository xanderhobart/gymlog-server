"""The small SQLite database that remembers who the users are and which backups each one has."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL UNIQUE,      -- lower case name, so "Sam" and "sam" are one person
    token_hash  TEXT NOT NULL UNIQUE,      -- SHA-256 of the user's token; the token itself is never stored
    created_at  INTEGER NOT NULL,
    disabled    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS backups (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    device      TEXT NOT NULL DEFAULT '',
    db_version  INTEGER,
    summary     TEXT NOT NULL DEFAULT '{}'  -- JSON: how many workouts, weigh-ins, photos... were in it
);
CREATE INDEX IF NOT EXISTS backups_by_user ON backups(user_id, created_at);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        # One short-lived connection per piece of work: simple, and safe with the server's threads.
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
