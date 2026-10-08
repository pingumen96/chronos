"""SQLite setup and short, explicit transactions. Never resets existing anchors."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS timelines (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
    anchor_real_ms INTEGER NOT NULL,
    anchor_virtual_ms INTEGER NOT NULL
        CHECK (anchor_virtual_ms BETWEEN -62135596800000 AND 253402300799999),
    speed REAL NOT NULL CHECK (speed BETWEEN 0.01 AND 1000),
    status TEXT NOT NULL CHECK (status IN ('running', 'paused')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if mode.lower() != "wal":
                raise RuntimeError("Chronos richiede un database SQLite su file con WAL.")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Versione del database non supportata; nessun dato modificato.")
            connection.execute(SCHEMA)
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def healthy(self) -> bool:
        with self.transaction() as connection:
            valid = connection.execute("PRAGMA quick_check(1)").fetchone()[0] == "ok"
            connection.execute("SELECT id FROM timelines LIMIT 1").fetchone()
            return valid
