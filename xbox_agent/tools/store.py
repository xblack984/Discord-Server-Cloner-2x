"""SQLite state manager: sessions, redeem-code statuses, event log, screenshot index."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, state TEXT NOT NULL, status TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS codes (
    session_id TEXT NOT NULL, code_id TEXT NOT NULL, masked TEXT NOT NULL,
    status TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (session_id, code_id));
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session_id TEXT, state TEXT,
    kind TEXT NOT NULL, payload TEXT NOT NULL, screenshot_id TEXT);
CREATE TABLE IF NOT EXISTS shots (id TEXT PRIMARY KEY, path TEXT NOT NULL, ts REAL NOT NULL);
"""


class SqliteStore:
    """Only masked codes are persisted: full gift codes live in memory for the session only."""

    def __init__(self, db_path: str, shots_dir: str):
        self._db = sqlite3.connect(db_path)
        self._db.executescript(_SCHEMA)
        self._shots = Path(shots_dir)
        self._shots.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self._db.close()

    def save_shot(self, png: bytes) -> str:
        shot_id = uuid.uuid4().hex
        path = self._shots / f"{shot_id}.png"
        path.write_bytes(png)
        self._db.execute("INSERT INTO shots VALUES (?,?,?)", (shot_id, str(path), time.time()))
        self._db.commit()
        return shot_id

    def log_event(self, session_id: str | None, state: str, kind: str, payload: dict[str, Any],
                  screenshot_id: str | None = None) -> None:
        self._db.execute(
            "INSERT INTO events (ts, session_id, state, kind, payload, screenshot_id) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), session_id, state, kind, json.dumps(payload, ensure_ascii=False), screenshot_id),
        )
        self._db.commit()

    def upsert_session(self, session_id: str, state: str, status: str) -> None:
        now = time.time()
        self._db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "state=excluded.state, status=excluded.status, updated_at=excluded.updated_at",
            (session_id, state, status, now, now),
        )
        self._db.commit()

    def upsert_code(self, session_id: str, code_id: str, masked: str, status: str) -> None:
        self._db.execute(
            "INSERT INTO codes VALUES (?,?,?,?,?) ON CONFLICT(session_id, code_id) DO UPDATE SET "
            "status=excluded.status, updated_at=excluded.updated_at",
            (session_id, code_id, masked, status, time.time()),
        )
        self._db.commit()

    def unfinished_sessions(self) -> list[str]:
        rows = self._db.execute("SELECT id FROM sessions WHERE status = 'running'").fetchall()
        return [r[0] for r in rows]

    def close_unfinished(self) -> None:
        self._db.execute("UPDATE sessions SET status='abandoned' WHERE status='running'")
        self._db.commit()
