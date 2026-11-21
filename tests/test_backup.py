import errno
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from timeday.backup import backup_database
from timeday.store import Store


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.source = self.folder / "рабочая база #1.sqlite3"
        self.destination = self.folder / "Резервная копия.sqlite3"
        self.store = Store(self.source)
        self.addCleanup(self.store.close)

    def test_snapshot_from_live_wal_is_standalone(self):
        today = date(2026, 11, 18)
        task = self.store.save_task("Отчёт", "Готов", today)
        self.store.set_done(task, True)
        event = self.store.save_event("Встреча", today, 600, 660, 127)
        self.store.save_occurrence(event, today, "Перенос", today, 720, 780)
        self.store.set_setting("clocks", '{"stopwatch":42}')
        self.store.record_activity("Firefox", 100, 200)
        self.store.record_runtime([("Firefox", 100, 250)])
        self.store.save_awake_checkpoint("boot", (50, 300), "{}")
        self.store.db.execute("INSERT INTO settings VALUES ('uncommitted', '1')")
        backup_database(self.source, self.destination)
        self.store.db.rollback()
        self.assertFalse(Path(f"{self.destination}-wal").exists())
        with closing(sqlite3.connect(self.destination)) as snapshot:
            self.assertEqual(snapshot.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        with closing(Store(self.destination)) as restored:
            self.assertEqual(restored.tasks(), self.store.tasks())
            self.assertEqual(restored.calendar(), self.store.calendar())
            self.assertEqual(restored.setting("clocks", ""), '{"stopwatch":42}')
            self.assertEqual(restored.setting("uncommitted", ""), "")
            self.assertEqual(len(restored.activity(0, 300)), 1)
            self.assertEqual(len(restored.runtime(0, 300)), 1)
            self.assertEqual(len(restored.awake_intervals(0, 400)), 1)

    def test_backup_rejects_existing_files_and_database_sidecars(self):
        self.destination.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            backup_database(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), b"keep me")
        for suffix in ("", "-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                backup_database(self.source, f"{self.source}{suffix}")

    def test_invalid_source_never_publishes_a_backup(self):
        damaged = self.folder / "damaged.sqlite3"
        damaged.write_bytes(b"not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            backup_database(damaged, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.folder.glob(".timeday-backup-*")), [])

    def test_backup_without_hard_link_support(self):
        with patch("timeday.backup.os.link", side_effect=OSError(errno.EOPNOTSUPP, "no links")):
            backup_database(self.source, self.destination)
        with closing(Store(self.destination)) as restored:
            self.assertEqual(restored.tasks(), [])
