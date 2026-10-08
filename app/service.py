"""Timeline use cases. Read-modify-write operations share one SQLite transaction."""

import sqlite3
import time
from typing import Callable
from uuid import uuid4

from .database import Database
from .temporal import TemporalRangeError, format_iso_ms, parse_iso_ms, virtual_now_ms


def system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


class TimelineNotFound(LookupError):
    pass


class TimelineService:
    def __init__(self, database: Database, clock: Callable[[], int] = system_clock_ms):
        self.database = database
        self.clock = clock

    @staticmethod
    def _get(connection: sqlite3.Connection, timeline_id: str) -> dict:
        row = connection.execute("SELECT * FROM timelines WHERE id = ?", (timeline_id,)).fetchone()
        if row is None:
            raise TimelineNotFound("Timeline non trovata.")
        return dict(row)

    @staticmethod
    def _current(row: dict, now: int) -> int:
        return virtual_now_ms(
            row["anchor_virtual_ms"], row["anchor_real_ms"], row["speed"], row["status"], now
        )

    def _view(self, row: dict, now: int) -> dict:
        result = dict(row)
        result["real_now"] = format_iso_ms(now)
        try:
            result["virtual_now"] = format_iso_ms(self._current(row, now))
            result["time_error"] = None
        except TemporalRangeError as exc:
            # A clock can eventually leave the calendar range while the server is off.
            # Keep its anchors intact and allow the user to reposition it explicitly.
            result["virtual_now"] = None
            result["time_error"] = str(exc)
        return result

    @staticmethod
    def _save(connection: sqlite3.Connection, row: dict) -> None:
        connection.execute(
            """UPDATE timelines SET name = :name, anchor_real_ms = :anchor_real_ms,
            anchor_virtual_ms = :anchor_virtual_ms, speed = :speed, status = :status,
            updated_at = :updated_at WHERE id = :id""",
            row,
        )

    def list(self) -> list[dict]:
        with self.database.transaction() as connection:
            now = self.clock()
            rows = connection.execute("SELECT * FROM timelines ORDER BY created_at, id").fetchall()
            return [self._view(dict(row), now) for row in rows]

    def get(self, timeline_id: str) -> dict:
        with self.database.transaction() as connection:
            row = self._get(connection, timeline_id)
            return self._view(row, self.clock())

    def create(self, name: str, start_time: str, speed: float, status: str) -> dict:
        instant = parse_iso_ms(start_time)
        with self.database.transaction(write=True) as connection:
            now = self.clock()
            timestamp = format_iso_ms(now)
            row = {
                "id": str(uuid4()), "name": name, "anchor_real_ms": now,
                "anchor_virtual_ms": instant, "speed": speed, "status": status,
                "created_at": timestamp, "updated_at": timestamp,
            }
            connection.execute(
                """INSERT INTO timelines
                (id, name, anchor_real_ms, anchor_virtual_ms, speed, status, created_at, updated_at)
                VALUES (:id, :name, :anchor_real_ms, :anchor_virtual_ms, :speed, :status,
                :created_at, :updated_at)""", row,
            )
            return self._view(row, now)

    def patch(self, timeline_id: str, changes: dict) -> dict:
        with self.database.transaction(write=True) as connection:
            row = self._get(connection, timeline_id)
            now = self.clock()
            previous = dict(row)
            if "virtual_time" in changes:
                row["anchor_virtual_ms"] = parse_iso_ms(changes["virtual_time"])
                row["anchor_real_ms"] = now
            elif "speed" in changes and changes["speed"] != row["speed"]:
                row["anchor_virtual_ms"] = self._current(row, now)
                row["anchor_real_ms"] = now
            for field in ("name", "speed"):
                if field in changes:
                    row[field] = changes[field]
            if row != previous:
                row["updated_at"] = format_iso_ms(now)
                self._save(connection, row)
            return self._view(row, now)

    def set_time(self, timeline_id: str, virtual_time: str) -> dict:
        return self.patch(timeline_id, {"virtual_time": virtual_time})

    def set_status(self, timeline_id: str, status: str) -> dict:
        with self.database.transaction(write=True) as connection:
            row = self._get(connection, timeline_id)
            now = self.clock()
            if row["status"] != status:
                row["anchor_virtual_ms"] = self._current(row, now)
                row["anchor_real_ms"] = now
                row["status"] = status
                row["updated_at"] = format_iso_ms(now)
                self._save(connection, row)
            return self._view(row, now)

    def delete(self, timeline_id: str) -> None:
        with self.database.transaction(write=True) as connection:
            cursor = connection.execute("DELETE FROM timelines WHERE id = ?", (timeline_id,))
            if cursor.rowcount == 0:
                raise TimelineNotFound("Timeline non trovata.")
