"""Small SQLite connection and schema helpers for local persistence."""

import sqlite3
from pathlib import Path

from app.core.config import DATABASE_PATH


def connect(database_path: Path | None = None) -> sqlite3.Connection:
    """Open one configured SQLite connection with local-server pragmas."""
    path = database_path or DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(database_path: Path | None = None) -> None:
    """Create the persistence foundation idempotently."""
    with connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT PRIMARY KEY,
                content_hash TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                current_paper_id TEXT NULL,
                title TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS session_papers (
                session_id TEXT NOT NULL,
                paper_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (session_id, paper_id)
            );
            CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                turn_id TEXT NULL,
                step INTEGER NULL,
                event_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (session_id, seq)
            );
            CREATE TABLE IF NOT EXISTS debug_trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                step INTEGER NULL,
                trace_seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (session_id, turn_id, trace_seq)
            );
            CREATE INDEX IF NOT EXISTS idx_debug_trace_events_turn
                ON debug_trace_events (session_id, turn_id, trace_seq);
            CREATE TABLE IF NOT EXISTS persistence_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(sessions)")}
        if "title" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
