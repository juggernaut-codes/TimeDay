import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Barrier

from timeday.activity import ActivityRecorder, Sample
from timeday.models import Clock, clock_state, period_bins, summarize
from timeday.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_deadline_delivers_two_reminders_and_keeps_unrelated_edits(self):
        day = date(2026, 11, 17)
        task_id = self.store.save_task("  Отчёт  ", "Описание", day, 1_000)
        self.assertEqual(self.store.tasks()[0].title, "Отчёт")
        self.assertFalse(self.store.reminders(899, 100))
        self.assertEqual(len(self.store.reminders(900, 100)), 1)
        self.store.save_task("Отчёт", "Новые детали", day, 1_000, task_id)
        self.assertFalse(self.store.reminders(950, 100))
        self.assertEqual(len(self.store.reminders(1_000, 100)), 1)
        self.assertFalse(self.store.reminders(1_050, 100))
        self.store.save_task("Отчёт", "", day, 1_100, task_id)
        self.assertEqual(len(self.store.reminders(1_050, 100)), 1)
        self.store.set_done(task_id, True)
        self.assertFalse(self.store.reminders(1_100, 100))
        self.store.delete_task(task_id)
        self.assertFalse(self.store.tasks())

    def test_concurrent_startups_and_reminder_claims(self):
        workers = 8
        opening, claiming = Barrier(workers, timeout=10), Barrier(workers, timeout=10)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeday.sqlite3"

            def claim(index):
                opening.wait()
                store = Store(path)
                try:
                    if index == 0:
                        store.save_task("Дедлайн", "", date.today(), 1_000)
                    claiming.wait()
                    return len(store.reminders(1_000, 0))
                finally:
                    store.close()

            with ThreadPoolExecutor(max_workers=workers) as executor:
                self.assertEqual(sum(executor.map(claim, range(workers))), 1)

    def test_activity_coalesces_and_clips_to_period(self):
        self.store.record_activity("Brave", 100, 110)
        self.store.record_activity("Brave", 110, 120)
        self.store.record_activity("Discord", 120, 130)
        records = self.store.activity(105, 125)
        self.assertCountEqual([tuple(row) for row in records], [
            ("Brave", 105, 120), ("Discord", 120, 125),
        ])
        series, apps, ranked = summarize(records, [105, 115, 125])
        self.assertEqual(series, [10, 10])
        self.assertEqual(apps["Brave"], [10, 5])
        self.assertEqual(ranked, [("Brave", 15), ("Discord", 5)])

    def test_idle_threshold_and_suspend_do_not_inflate_activity(self):
        recorder = ActivityRecorder(self.store)
        for sample in (
            Sample(1_000, 100, "Brave", 4),
            Sample(1_002, 102, "Brave", 6),
            Sample(1_004, 104, "Brave", 8),
            Sample(1_006, 106, "Discord", 0),
            Sample(1_008, 108, "Discord", 2),
            Sample(2_000, 110, "Discord", 4),
        ):
            recorder.observe(sample, idle_limit=5)
        self.assertCountEqual([tuple(row) for row in self.store.activity(0, 3_000)], [
            ("Brave", 1_000, 1_001), ("Discord", 1_006, 1_008),
        ])

    def test_data_survives_reopening(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "timeday.sqlite3"
            original = Store(path)
            original.set_setting("accent", "Мятный")
            date_value = date.today() + timedelta(days=1)
            task = original.save_task("Задача", "", date_value)
            original.set_done(task, True)
            original.record_activity("Brave", 100, 110)
            original.close()
            reopened = Store(path)
            try:
                self.assertEqual(reopened.setting("accent", ""), "Мятный")
                [task] = reopened.tasks()
                self.assertEqual(task.day, date_value)
                self.assertTrue(task.done)
                moved_day = date_value + timedelta(days=2)
                reopened.save_task(task.title, task.description, moved_day, task_id=task.id)
                self.assertEqual(reopened.tasks()[0].day, moved_day)
                self.assertTrue(reopened.tasks()[0].done)
                self.assertEqual(len(reopened.activity(95, 105)), 1)
            finally:
                reopened.close()


    def test_focus_changes_inside_one_heartbeat_are_split(self):
        recorder = ActivityRecorder(self.store)
        for sample in (
            Sample(1_000, 100, "Brave", 0),
            Sample(1_000.2, 100.2, "Discord", .2),
            Sample(1_000.4, 100.4, "Brave", .4),
            Sample(1_000.9, 100.9, None, .9),
        ):
            recorder.observe(sample, idle_limit=5)
        self.assertCountEqual([tuple(row) for row in self.store.activity(999, 1_002)], [
            ("Brave", 1_000, 1_000.2), ("Discord", 1_000.2, 1_000.4), ("Brave", 1_000.4, 1_000.9),
        ])

    def test_same_window_resumes_after_idle_without_counting_the_gap(self):
        recorder = ActivityRecorder(self.store)
        for sample in (
            Sample(1_000, 100, "Brave", 4.9),
            Sample(1_000.6, 100.6, "Brave", .2),
        ):
            recorder.observe(sample, idle_limit=5)
        rows = [tuple(row) for row in self.store.activity(999, 1_001)]
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0][2], 1_000.1)
        self.assertAlmostEqual(rows[1][1], 1_000.4)

    def test_legacy_database_migrates_in_place(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite3"
            due = int(datetime(2026, 11, 17, 18).timestamp())
            with sqlite3.connect(path) as old:
                old.executescript("""
                    CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                        description TEXT NOT NULL, due INTEGER, progress INTEGER NOT NULL,
                        warned INTEGER NOT NULL DEFAULT 0, expired INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX tasks_due ON tasks(due) WHERE progress < 100;
                    INSERT INTO tasks (id, title, description, progress) VALUES (33, 'old', '', 100);
                    INSERT INTO tasks (id, title, description, progress) VALUES (90, 'old', '', 0);
                    DELETE FROM tasks WHERE id = 90;
                """)
                old.execute("""
                    INSERT INTO tasks (id, title, description, due, progress, warned)
                    VALUES (10, 'Report', 'Draft', ?, 40, 1)
                """, (due,))
            old.close()
            database = Store(path)
            try:
                tasks = {task.id: task for task in database.tasks()}
                self.assertEqual(tasks[10].day, date(2026, 11, 17))
                self.assertEqual(tasks[10].description, "Draft")
                self.assertFalse(tasks[10].done)
                self.assertEqual(tasks[33].day, date.today())
                self.assertTrue(tasks[33].done)
                self.assertFalse(database.reminders(due - 30, 60))
                self.assertEqual(database.save_task("New task", "", date.today()), 91)
                self.assertNotIn("progress", {row["name"] for row in database.db.execute("PRAGMA table_info(tasks)")})
            finally:
                database.close()
            second_open = Store(path)
            try:
                self.assertEqual(len(second_open.tasks()), 3)
            finally:
                second_open.close()


class ClockTests(unittest.TestCase):
    def test_clock_settings_recover_from_invalid_values(self):
        defaults = {"mode": "countdown", "duration": 1500, "countdown": 0, "stopwatch": 0, "laps": []}
        for state in ("broken", "null", "[]", '{"duration": null, "countdown": NaN}'):
            with self.subTest(state=state):
                self.assertEqual(clock_state(state), defaults)
        self.assertEqual(clock_state(
            '{"mode":"stopwatch", "duration":30, "countdown":29.5, '
            '"stopwatch":14.25, "laps":[1, 5, 3, -1, null, 15, 14.25]}'
        ), {"mode": "stopwatch", "duration": 30, "countdown": 29.5,
            "stopwatch": 14.25, "laps": [1, 5, 14.25]})

    def test_countdown_pause_and_completion_only_once(self):
        now = [0]
        clock = Clock(5, now=lambda: now[0])
        clock.start()
        now[0] = 1.4
        clock.pause()
        now[0] = 21
        self.assertEqual(clock.remaining, 4)
        clock.start()
        now[0] = 24.5
        self.assertFalse(clock.check_finished())
        now[0] = 25
        self.assertTrue(clock.check_finished())
        self.assertFalse(clock.check_finished())
        self.assertEqual(clock.remaining, 0)
        clock.reset()
        self.assertEqual(clock.remaining, 5)

    def test_stopwatch_accumulates_running_time(self):
        now = [10]
        clock = Clock(now=lambda: now[0])
        clock.start()
        now[0] = 14
        clock.pause()
        now[0] = 28
        self.assertEqual(clock.elapsed, 4)
        clock.start()
        now[0] = 30
        self.assertEqual(clock.elapsed, 6)
        clock.reset()
        self.assertEqual(clock.elapsed, 0)

    def test_week_and_month_end_at_tomorrows_local_midnight(self):
        today = date(2026, 11, 17)
        day, _ = period_bins("day", today)
        week, week_labels = period_bins("week", today)
        month, month_labels = period_bins("month", today)
        self.assertEqual(len(week_labels), 7)
        self.assertEqual(len(month_labels), 30)
        self.assertEqual(day[-1], week[-1])
        self.assertEqual(day[-1], month[-1])
        self.assertEqual(week_labels[-1], "17.11")
