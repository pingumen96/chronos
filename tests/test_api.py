"""REST and persistence acceptance scenarios without real-time sleeps."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.temporal import MAX_MS, format_iso_ms, parse_iso_ms


class MutableClock:
    def __init__(self):
        self.now = parse_iso_ms("2026-10-08T12:00:00Z")

    def __call__(self):
        return self.now

    def advance(self, milliseconds):
        self.now += milliseconds


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "timelines.db"
        self.clock = MutableClock()
        self.app = create_app(self.path, self.clock)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def create(self, **changes):
        payload = {"name": "Anno 2000", "start_time": "2000-01-01T00:00:00Z", "speed": 3, "status": "running"}
        payload.update(changes)
        response = self.client.post("/api/timelines", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def get(self, timeline_id):
        response = self.client.get(f"/api/timelines/{timeline_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def persisted_fields(view):
        return {key: value for key, value in view.items() if key not in ("virtual_now", "real_now", "time_error")}

    def test_create_list_detail_now_and_defaults(self):
        response = self.client.post("/api/timelines", json={"name": "  Default  ", "start_time": "2000-01-01T00:00:00Z"})
        self.assertEqual(response.status_code, 201)
        timeline = response.json()
        self.assertEqual(UUID(timeline["id"]).version, 4)
        self.assertEqual(response.headers["location"], f"/api/timelines/{timeline['id']}")
        self.assertEqual((timeline["name"], timeline["speed"], timeline["status"]), ("Default", 1, "running"))
        self.assertEqual(self.client.get("/api/timelines").json(), [timeline])
        now = self.client.get(f"/api/timelines/{timeline['id']}/now").json()
        self.assertEqual(set(now), {"id", "virtual_now", "real_now", "speed", "status", "time_error"})
        self.assertEqual(now["virtual_now"], "2000-01-01T00:00:00.000Z")
        self.assertIsNone(now["time_error"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_three_independent_timelines(self):
        first = self.create()
        second = self.create(name="Seconda", start_time="2010-01-01T00:00:00Z", speed=0.5)
        third = self.create(name="Ferma", start_time="2020-02-29T00:00:00Z", speed=100, status="paused")
        self.clock.advance(3_600_000)
        self.assertEqual(self.get(first["id"])["virtual_now"], "2000-01-01T03:00:00.000Z")
        self.assertEqual(self.get(second["id"])["virtual_now"], "2010-01-01T00:30:00.000Z")
        self.assertEqual(self.get(third["id"])["virtual_now"], "2020-02-29T00:00:00.000Z")
        self.client.patch(f"/api/timelines/{first['id']}", json={"speed": 10, "name": "Nuovo nome"})
        self.assertEqual(self.persisted_fields(self.get(second["id"])), self.persisted_fields(second))
        self.assertEqual(self.persisted_fields(self.get(third["id"])), self.persisted_fields(third))

    def test_speed_change_preserves_current_time_and_new_rate(self):
        timeline = self.create()
        self.clock.advance(3_600_000)
        response = self.client.patch(f"/api/timelines/{timeline['id']}", json={"speed": 10})
        self.assertEqual(response.status_code, 200)
        changed = response.json()
        self.assertEqual(changed["virtual_now"], "2000-01-01T03:00:00.000Z")
        self.assertEqual(changed["anchor_real_ms"], self.clock.now)
        self.clock.advance(3_600_000)
        self.assertEqual(self.get(timeline["id"])["virtual_now"], "2000-01-01T13:00:00.000Z")

    def test_pause_resume_and_idempotent_anchors(self):
        timeline = self.create()
        url = f"/api/timelines/{timeline['id']}"
        self.clock.advance(3_600_000)
        paused = self.client.post(url + "/pause").json()
        self.assertEqual(paused["virtual_now"], "2000-01-01T03:00:00.000Z")
        self.assertEqual((paused["speed"], paused["status"]), (3, "paused"))
        self.clock.advance(7_200_000)
        paused_again = self.client.post(url + "/pause").json()
        self.assertEqual(self.persisted_fields(paused_again), self.persisted_fields(paused))
        self.assertEqual(paused_again["virtual_now"], paused["virtual_now"])
        resumed = self.client.post(url + "/resume").json()
        self.assertEqual(resumed["virtual_now"], paused["virtual_now"])
        self.clock.advance(3_600_000)
        resumed_again = self.client.post(url + "/resume").json()
        self.assertEqual(self.persisted_fields(resumed_again), self.persisted_fields(resumed))
        self.assertEqual(resumed_again["virtual_now"], "2000-01-01T06:00:00.000Z")

    def test_manual_date_preserves_speed_and_status(self):
        for status in ("running", "paused"):
            with self.subTest(status=status):
                timeline = self.create(status=status, speed=0.5)
                response = self.client.put(f"/api/timelines/{timeline['id']}/time", json={"virtual_time": "2000-02-29T23:59:59+01:00"})
                self.assertEqual(response.status_code, 200)
                changed = response.json()
                self.assertEqual(changed["virtual_now"], "2000-02-29T22:59:59.000Z")
                self.assertEqual((changed["speed"], changed["status"]), (0.5, status))
                self.clock.advance(2000)
                expected = "2000-02-29T23:00:00.000Z" if status == "running" else changed["virtual_now"]
                self.assertEqual(self.get(timeline["id"])["virtual_now"], expected)

    def test_restart_preserves_anchors_and_catches_up_downtime(self):
        running = self.create()
        paused = self.create(name="Paused", status="paused")
        self.client.__exit__(None, None, None)
        self.clock.advance(7_200_000)
        with TestClient(create_app(self.path, self.clock)) as restarted:
            rows = {row["id"]: row for row in restarted.get("/api/timelines").json()}
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[running["id"]]["virtual_now"], "2000-01-01T06:00:00.000Z")
            self.assertEqual(rows[paused["id"]]["virtual_now"], "2000-01-01T00:00:00.000Z")
            self.assertEqual(self.persisted_fields(rows[running["id"]]), self.persisted_fields(running))
            self.assertEqual(self.persisted_fields(rows[paused["id"]]), self.persisted_fields(paused))

    def test_validation_does_not_create_invalid_rows(self):
        invalid = [
            {"name": " "}, {"name": "x" * 201}, {"name": 3},
            {"speed": 0}, {"speed": -1}, {"speed": 0.009}, {"speed": 1001},
            {"speed": "3"}, {"speed": True}, {"status": "stopped"}, {"unknown": 1},
            {"start_time": "2000-01-01T00:00:00"}, {"start_time": "2001-02-29T00:00:00Z"},
            {"start_time": "2000-01-01T00:00:00+00:60"},
            {"start_time": "2000-01-01T00:00:00+00:99"},
            {"start_time": "2000-01-01T00:00:00-00:99"},
            {"start_time": "2000-01-01T00:00:00+24:00"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                payload = {"name": "Test", "start_time": "2000-01-01T00:00:00Z"}
                payload.update(changes)
                response = self.client.post("/api/timelines", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        for invalid_number in ("NaN", "Infinity", "-Infinity"):
            response = self.client.post("/api/timelines", content='{"name":"Test","start_time":"2000-01-01T00:00:00Z","speed":' + invalid_number + '}', headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIsInstance(response.json()["detail"], list)
        self.assertEqual(self.client.get("/api/timelines").json(), [])

    def test_invalid_patch_and_time_leave_row_unchanged(self):
        timeline = self.create()
        url = f"/api/timelines/{timeline['id']}"
        for payload in ({}, {"name": None}, {"speed": None}, {"speed": "2"}, {"name": "Updated", "speed": -1}, {"unknown": 1}, {"virtual_time": "invalid"}):
            with self.subTest(payload=payload):
                response = self.client.patch(url, json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.get(timeline["id"]), timeline)
        self.assertEqual(self.client.put(url + "/time", json={"virtual_time": "invalid"}).status_code, 422)
        self.assertEqual(self.get(timeline["id"]), timeline)

    def test_overflow_is_visible_and_recoverable_without_reset(self):
        timeline = self.create(start_time=format_iso_ms(MAX_MS))
        self.clock.advance(1000)
        url = f"/api/timelines/{timeline['id']}"
        overflow = self.get(timeline["id"])
        self.assertIsNone(overflow["virtual_now"])
        self.assertTrue(overflow["time_error"])
        self.assertIsNone(self.client.get(url + "/now").json()["virtual_now"])
        self.assertIsNone(self.client.get("/api/timelines").json()[0]["virtual_now"])
        for method, endpoint, payload in (("patch", url, {"name": "Must rollback", "speed": 10}), ("post", url + "/pause", None)):
            with self.subTest(endpoint=endpoint):
                response = getattr(self.client, method)(endpoint, **({"json": payload} if payload else {}))
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.persisted_fields(self.get(timeline["id"])), self.persisted_fields(timeline))
        self.assertEqual(self.client.patch(url, json={"name": "Recover me"}).status_code, 200)
        response = self.client.patch(url, json={"virtual_time": "2000-01-01T00:00:00Z", "speed": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["virtual_now"], "2000-01-01T00:00:00.000Z")
        self.assertIsNone(response.json()["time_error"])

    def test_sql_failure_rolls_back_atomic_mutation(self):
        timeline = self.create()
        with self.app.state.database.transaction(write=True) as connection:
            connection.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON timelines BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        self.clock.advance(1000)
        with self.assertLogs("uvicorn.error", level="ERROR") as captured:
            response = self.client.patch(f"/api/timelines/{timeline['id']}", json={"name": "Changed", "speed": 10})
        self.assertEqual(response.status_code, 500)
        self.assertTrue(any("SQLite request failed" in entry for entry in captured.output))
        self.assertEqual(self.persisted_fields(self.get(timeline["id"])), self.persisted_fields(timeline))

    def test_health_wal_and_nondestructive_initialization(self):
        timeline = self.create()
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok", "database": "ok"})
        self.app.state.database.initialize()
        self.assertEqual(self.get(timeline["id"]), timeline)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            connection.close()

    def test_delete_and_missing_timeline_routes(self):
        timeline = self.create()
        url = f"/api/timelines/{timeline['id']}"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        requests = [
            ("get", url, None), ("get", url + "/now", None),
            ("patch", url, {"name": "Missing"}),
            ("put", url + "/time", {"virtual_time": "2000-01-01T00:00:00Z"}),
            ("post", url + "/pause", None), ("post", url + "/resume", None),
            ("delete", url, None),
        ]
        for method, endpoint, payload in requests:
            with self.subTest(method=method, endpoint=endpoint):
                response = getattr(self.client, method)(endpoint, **({"json": payload} if payload else {}))
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/timelines").json(), [])


if __name__ == "__main__":
    unittest.main()
