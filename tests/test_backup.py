"""Backup guarantees against live WAL, accidental overwrite and failed copies."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.backup import backup_database


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "timelines.db"

    def create_database(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
            connection.execute("INSERT INTO sample VALUES ('original')")
            connection.commit()

    def test_online_backup_includes_committed_wal_and_excludes_uncommitted_changes(self):
        destination = self.root / "backups" / "snapshot.db"
        with closing(sqlite3.connect(self.source)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE sample (value TEXT NOT NULL)")
            writer.execute("INSERT INTO sample VALUES ('committed in WAL')")
            writer.commit()
            wal = Path(str(self.source) + "-wal")
            self.assertTrue(wal.exists())
            self.assertGreater(wal.stat().st_size, 0)

            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO sample VALUES ('not committed')")
            backup_database(self.source, destination)
            writer.rollback()
            writer.execute("INSERT INTO sample VALUES ('after the snapshot')")
            writer.commit()

            with closing(sqlite3.connect(destination)) as snapshot:
                self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    snapshot.execute("SELECT value FROM sample").fetchall(),
                    [("committed in WAL",)],
                )
            self.assertEqual(writer.execute("SELECT count(*) FROM sample").fetchone()[0], 2)

    def test_existing_destination_is_preserved(self):
        self.create_database()
        destination = self.root / "existing.db"
        destination.write_bytes(b"existing backup must survive")

        with self.assertRaises(FileExistsError):
            backup_database(self.source, destination)

        self.assertEqual(destination.read_bytes(), b"existing backup must survive")
        with closing(sqlite3.connect(self.source)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "original")

    def test_source_and_sqlite_sidecars_cannot_be_destinations(self):
        self.create_database()
        source_bytes = self.source.read_bytes()
        for destination in (self.source, Path(str(self.source) + "-wal"), Path(str(self.source) + "-shm")):
            with self.subTest(destination=destination.name):
                with self.assertRaises(ValueError):
                    backup_database(self.source, destination)

        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertFalse(Path(str(self.source) + "-wal").exists())
        self.assertFalse(Path(str(self.source) + "-shm").exists())

    def test_missing_source_creates_neither_database_nor_destination(self):
        destination = self.root / "backups" / "snapshot.db"

        with self.assertRaises(FileNotFoundError):
            backup_database(self.source, destination)

        self.assertFalse(self.source.exists())
        self.assertFalse(destination.exists())
        self.assertFalse(destination.parent.exists())

    def test_invalid_source_removes_incomplete_backup_and_closes_handles(self):
        self.source.write_bytes(b"this is not a SQLite database")
        destination = self.root / "incomplete.db"

        with self.assertRaises(sqlite3.DatabaseError):
            backup_database(self.source, destination)

        self.assertFalse(destination.exists())
        # On Windows this unlink would fail if the failed backup retained its handle.
        self.source.unlink()


if __name__ == "__main__":
    unittest.main()
