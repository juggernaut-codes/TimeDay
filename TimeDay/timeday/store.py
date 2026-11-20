"""Local persistence and queries for tasks, preferences and activity."""

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .i18n import tr
from .events import Event, Override


def database_path():
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return root / "timeday" / "timeday.sqlite3"


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    description: str
    due: int | None
    day: date
    done: bool


class Store:
    def __init__(self, path=None):
        if path is None:
            path = database_path()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        try:
            self.initialize()
        except sqlite3.Error:
            self.close()
            raise

    def initialize(self):
        # Switching a new database to WAL can report SQLITE_BUSY immediately,
        # even with the connection's busy timeout set.
        deadline = time.monotonic() + 5
        while True:
            try:
                self.db.execute("PRAGMA journal_mode = WAL").fetchone()
                break
            except sqlite3.OperationalError as exc:
                busy = (getattr(exc, "sqlite_errorcode", None) == getattr(sqlite3, "SQLITE_BUSY", 5)
                        or str(exc) == "database is locked")
                if not busy or time.monotonic() >= deadline:
                    raise
                time.sleep(.025)
        self.db.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY,
                app TEXT NOT NULL,
                started REAL NOT NULL,
                ended REAL NOT NULL CHECK (ended > started)
            );
            CREATE INDEX IF NOT EXISTS activity_ended ON activity(ended);
            CREATE TABLE IF NOT EXISTS app_runtime (
                id INTEGER PRIMARY KEY,
                app TEXT NOT NULL,
                started REAL NOT NULL,
                ended REAL NOT NULL CHECK (ended > started)
            );
            CREATE INDEX IF NOT EXISTS app_runtime_latest ON app_runtime(app, id DESC);
            CREATE INDEX IF NOT EXISTS app_runtime_ended ON app_runtime(ended);
            CREATE TABLE IF NOT EXISTS computer_awake (
                id INTEGER PRIMARY KEY,
                boot_id TEXT NOT NULL,
                source TEXT NOT NULL,
                started REAL NOT NULL,
                ended REAL NOT NULL CHECK (ended > started)
            );
            CREATE INDEX IF NOT EXISTS computer_awake_ended ON computer_awake(ended);
            CREATE INDEX IF NOT EXISTS computer_awake_boot ON computer_awake(boot_id, source, id DESC);
            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                day TEXT NOT NULL,
                start INTEGER NOT NULL CHECK (start BETWEEN 0 AND 1439),
                end INTEGER NOT NULL CHECK (end > start AND end <= start + 1440),
                repeat INTEGER NOT NULL CHECK (repeat BETWEEN 0 AND 127)
            );
            CREATE TABLE IF NOT EXISTS calendar_overrides (
                event_id INTEGER NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
                original_day TEXT NOT NULL,
                title TEXT,
                day TEXT,
                start INTEGER,
                end INTEGER,
                PRIMARY KEY (event_id, original_day)
            );
            CREATE TABLE IF NOT EXISTS computer_idle (
                id INTEGER PRIMARY KEY,
                started REAL NOT NULL,
                ended REAL NOT NULL CHECK (ended > started)
            );
            CREATE INDEX IF NOT EXISTS computer_idle_ended ON computer_idle(ended);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for table in ("calendar_events", "calendar_overrides"):
                columns = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
                for name, definition in (("color", "INTEGER"), ("marker", "TEXT")):
                    if name not in columns:
                        default = (" DEFAULT 0" if name == "color" else " DEFAULT ''") if table == "calendar_events" else ""
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}{default}")

        self.migrate_tasks()

    def migrate_tasks(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(tasks)")}
            if "progress" in columns:
                self.db.execute("ALTER TABLE tasks RENAME TO legacy_tasks")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    due INTEGER,
                    day TEXT NOT NULL,
                    done INTEGER NOT NULL CHECK (done IN (0, 1)),
                    warned INTEGER NOT NULL DEFAULT 0,
                    expired INTEGER NOT NULL DEFAULT 0
                )
            """)
            if "progress" in columns:
                self.db.execute("""
                    INSERT INTO tasks (id, title, description, due, day, done, warned, expired)
                    SELECT id, title, description, due,
                        COALESCE(date(due, 'unixepoch', 'localtime'), ?),
                        progress = 100, warned, expired
                    FROM legacy_tasks
                """, (date.today().isoformat(),))
                self.db.execute("""
                    UPDATE sqlite_sequence SET seq = MAX(seq, COALESCE(
                        (SELECT seq FROM sqlite_sequence WHERE name = 'legacy_tasks'), 0))
                    WHERE name = 'tasks'
                """)
                self.db.execute("""
                    UPDATE sqlite_sequence SET name = 'tasks'
                    WHERE name = 'legacy_tasks'
                        AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'tasks')
                """)
                self.db.execute("DROP TABLE legacy_tasks")
            self.db.execute("CREATE INDEX IF NOT EXISTS tasks_day ON tasks(day, done, due)")
            self.db.execute("CREATE INDEX IF NOT EXISTS tasks_due ON tasks(due) WHERE done = 0")

    def close(self):
        self.db.close()

    def setting(self, key, default):
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    def tasks(self):
        rows = self.db.execute("""
            SELECT id, title, description, due, day, done FROM tasks
            ORDER BY day, done, due IS NULL, due, id
        """)
        return [
            Task(row["id"], row["title"], row["description"], row["due"],
                 date.fromisoformat(row["day"]), bool(row["done"]))
            for row in rows
        ]

    def save_task(self, title, description, day, due=None, task_id=None):
        title = title.strip()
        if not title:
            raise ValueError(tr('Введите название задачи.'))
        with self.db:
            if task_id is None:
                return self.db.execute("""
                    INSERT INTO tasks (title, description, day, due, done) VALUES (?, ?, ?, ?, 0)
                """, (title, description, day.isoformat(), due)).lastrowid
            self.db.execute("""
                UPDATE tasks SET title = ?, description = ?, day = ?, due = ?,
                    warned = CASE WHEN due IS ? THEN warned ELSE 0 END,
                    expired = CASE WHEN due IS ? THEN expired ELSE 0 END
                WHERE id = ?
            """, (title, description, day.isoformat(), due, due, due, task_id))
            return task_id

    def set_done(self, task_id, done):
        with self.db:
            self.db.execute("""
                UPDATE tasks SET done = ?,
                    warned = CASE WHEN done = 1 AND ? = 0 THEN 0 ELSE warned END,
                    expired = CASE WHEN done = 1 AND ? = 0 THEN 0 ELSE expired END
                WHERE id = ?
            """, (done, done, done, task_id))

    def delete_task(self, task_id):
        with self.db:
            self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def reminders(self, now, lead_seconds):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.db.execute("""
                SELECT id, title, due FROM tasks
                WHERE done = 0 AND (
                    (due <= ? AND expired = 0) OR
                    (due > ? AND due <= ? AND warned = 0)
                )
                ORDER BY due
            """, (now, now, now + lead_seconds)).fetchall()
            for row in rows:
                expired = row["due"] <= now
                self.db.execute("""
                    UPDATE tasks SET warned = 1, expired = ? WHERE id = ?
                """, (expired, row["id"]))
        return rows

    def calendar(self):
        events = [
            Event(row["id"], row["title"], date.fromisoformat(row["day"]), row["start"],
                  row["end"], row["repeat"], row["color"], row["marker"])
            for row in self.db.execute("SELECT * FROM calendar_events")
        ]
        overrides = [
            Override(row["event_id"], date.fromisoformat(row["original_day"]), row["title"],
                     date.fromisoformat(row["day"]) if row["day"] else None, row["start"], row["end"], row["color"], row["marker"])
            for row in self.db.execute("SELECT * FROM calendar_overrides")
        ]
        return events, overrides

    def save_event(self, title, day, start, end, repeat=0, event_id=None, color=0, marker=""):
        title = title.strip()
        if not title:
            raise ValueError(tr('Введите название события.'))
        values = (title, day.isoformat(), start, end, repeat, color, marker)
        with self.db:
            if event_id is None:
                return self.db.execute("""
                    INSERT INTO calendar_events (title, day, start, end, repeat, color, marker) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, values).lastrowid
            self.db.execute("""
                UPDATE calendar_events SET title = ?, day = ?, start = ?, end = ?, repeat = ?, color = ?, marker = ? WHERE id = ?
            """, (*values, event_id))
            event = Event(event_id, title, day, start, end, repeat)
            for row in self.db.execute(
                "SELECT original_day FROM calendar_overrides WHERE event_id = ?", (event_id,),
            ).fetchall():
                if not repeat or not event.occurs(date.fromisoformat(row["original_day"])):
                    self.db.execute("""
                        DELETE FROM calendar_overrides WHERE event_id = ? AND original_day = ?
                    """, (event_id, row["original_day"]))
        return event_id

    def save_occurrence(self, event_id, original_day, title=None, day=None, start=None, end=None, color=None, marker=None):
        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError(tr('Введите название события.'))
        with self.db:
            self.db.execute("""
                INSERT INTO calendar_overrides (event_id, original_day, title, day, start, end, color, marker)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id, original_day) DO UPDATE SET
                    title = excluded.title, day = excluded.day, start = excluded.start, end = excluded.end, color = excluded.color, marker = excluded.marker
            """, (event_id, original_day.isoformat(), title, day.isoformat() if day else None, start, end, color, marker))

    def delete_event(self, event_id):
        with self.db:
            self.db.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))

    def record_activity(self, app, started, ended):
        if ended <= started:
            return
        with self.db:
            last = self.db.execute("""
                SELECT id, app, ended FROM activity ORDER BY id DESC LIMIT 1
            """).fetchone()
            if last and last["app"] == app and last["ended"] == started:
                self.db.execute("UPDATE activity SET ended = ? WHERE id = ?", (ended, last["id"]))
            else:
                self.db.execute("""
                    INSERT INTO activity (app, started, ended) VALUES (?, ?, ?)
                """, (app, started, ended))

    def activity(self, start, end):
        return self.db.execute("""
            SELECT app, MAX(started, ?) AS started, MIN(ended, ?) AS ended FROM activity
            WHERE ended > ? AND started < ?
            ORDER BY activity.started
        """, (start, end, start, end)).fetchall()

    def record_runtime(self, intervals):
        with self.db:
            for app, start, end in intervals:
                if end <= start:
                    continue
                previous = self.db.execute("""
                    SELECT id, ended FROM app_runtime WHERE app = ? ORDER BY id DESC LIMIT 1
                """, (app,)).fetchone()
                if previous and previous["ended"] == start:
                    self.db.execute("UPDATE app_runtime SET ended = ? WHERE id = ?", (end, previous["id"]))
                else:
                    self.db.execute("""
                        INSERT INTO app_runtime (app, started, ended) VALUES (?, ?, ?)
                    """, (app, start, end))

    def runtime(self, start, end):
        return self.db.execute("""
            SELECT app, MAX(started, ?) AS started, MIN(ended, ?) AS ended FROM app_runtime
            WHERE ended > ? AND started < ?
            ORDER BY app_runtime.started
        """, (start, end, start, end)).fetchall()

    def record_idle(self, start, end):
        if end <= start:
            return
        with self.db:
            last = self.db.execute("SELECT id, ended FROM computer_idle ORDER BY id DESC LIMIT 1").fetchone()
            if last and last["ended"] == start:
                self.db.execute("UPDATE computer_idle SET ended = ? WHERE id = ?", (end, last["id"]))
            else:
                self.db.execute("INSERT INTO computer_idle (started, ended) VALUES (?, ?)", (start, end))

    def idle_intervals(self, start, end):
        return self.db.execute("SELECT MAX(started, ?), MIN(ended, ?) FROM computer_idle "
                               "WHERE ended > ? AND started < ?", (start, end, start, end)).fetchall()

    def save_awake_checkpoint(self, boot_id, interval, checkpoint):
        with self.db:
            if interval:
                start, end = interval
                last = self.db.execute("""
                    SELECT id, ended FROM computer_awake
                    WHERE boot_id = ? AND source = 'live' ORDER BY id DESC LIMIT 1
                """, (boot_id,)).fetchone()
                if last and last["ended"] == start:
                    self.db.execute("UPDATE computer_awake SET ended = ? WHERE id = ?", (end, last["id"]))
                else:
                    self.db.execute("""
                        INSERT INTO computer_awake (boot_id, source, started, ended) VALUES (?, 'live', ?, ?)
                    """, (boot_id, start, end))
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('pc_awake_checkpoint', ?)", (checkpoint,))

    def save_awake_history(self, history):
        with self.db:
            for boot_id, spans in history.items():
                self.db.execute("DELETE FROM computer_awake WHERE boot_id = ? AND source = 'journal'", (boot_id,))
                self.db.executemany("""
                    INSERT INTO computer_awake (boot_id, source, started, ended) VALUES (?, 'journal', ?, ?)
                """, [(boot_id, start, end) for start, end in spans])

    def awake_intervals(self, start, end):
        return self.db.execute("""
            SELECT boot_id, source, started, ended FROM computer_awake
            WHERE ended > ? AND started < ?
        """, (start, end)).fetchall()
