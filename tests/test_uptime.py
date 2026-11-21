import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from timeday.store import Store
from timeday.uptime import (
    AwakePoint, ComputerUptime, daily_awake, journal_history, journal_spans, live_interval,
    local_midnight, month_dates, shift_month,
)


DAY = date(2026, 11, 17)
HOUR = 3600


class UptimeTests(unittest.TestCase):
    def test_failed_checkpoint_keeps_last_persisted_point(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        uptime = ComputerUptime(store, 'test')
        start = local_midnight(DAY)
        uptime.checkpoint(AwakePoint(start + 100, 100, 100))
        previous = uptime.previous
        with patch.object(store, 'save_awake_checkpoint', side_effect=sqlite3.OperationalError('locked')):
            with self.assertRaises(sqlite3.OperationalError):
                uptime.checkpoint(AwakePoint(start + 110, 110, 110))
        self.assertEqual(uptime.previous, previous)
        uptime.checkpoint(AwakePoint(start + 120, 120, 120))
        self.assertEqual(uptime.totals([DAY])[DAY], 120)

    def test_invalid_checkpoint_is_replaced(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        point = AwakePoint(local_midnight(DAY) + HOUR, HOUR, HOUR)
        for invalid in ("null", "[]", '{"boot_id":"test","point":{"wall":"bad","awake":0,"boot":0}}',
                        '{"boot_id":"test","point":{"wall":NaN,"awake":0,"boot":0}}'):
            with self.subTest(invalid=invalid):
                store.set_setting("pc_awake_checkpoint", invalid)
                uptime = ComputerUptime(store, "test")
                self.assertIsNone(uptime.previous)
                uptime.checkpoint(point)
                self.assertEqual(ComputerUptime(store, "test").previous, point)
                self.assertEqual(uptime.totals([DAY])[DAY], HOUR)

    def test_two_boots_sum_to_five_hours_and_survive_reopening(self):
        start = local_midnight(DAY)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "timeday.sqlite3"
            store = Store(path)
            ComputerUptime(store, "morning").checkpoint(AwakePoint(start + 14 * HOUR, 2 * HOUR, 2 * HOUR))
            store.close()
            store = Store(path)
            evening = ComputerUptime(store, "evening")
            evening.checkpoint(AwakePoint(start + 18 * HOUR, 2 * HOUR, 2 * HOUR))
            store.close()
            store = Store(path)
            self.addCleanup(store.close)
            same_boot = ComputerUptime(store, "evening")
            same_boot.checkpoint(AwakePoint(start + 19 * HOUR, 3 * HOUR, 3 * HOUR))
            self.assertEqual(same_boot.totals([DAY])[DAY], 5 * HOUR)

    def test_sleep_is_removed_from_kernel_elapsed_time(self):
        start = local_midnight(DAY) + 12 * HOUR
        before = AwakePoint(start, 0, 0)
        after = AwakePoint(start + 5 * HOUR, 3 * HOUR, 5 * HOUR)
        self.assertEqual(live_interval(before, after), (start + 2 * HOUR, start + 5 * HOUR))

    def test_an_awake_boot_crossing_midnight_is_split_by_date(self):
        following = DAY + timedelta(days=1)
        boundary = local_midnight(following)
        rows = [("boot", "live", boundary - HOUR, boundary + 2 * HOUR)]
        self.assertEqual(daily_awake(rows, [DAY, following]), {DAY: HOUR, following: 2 * HOUR})

    def test_journal_history_excludes_logged_sleep_between_days(self):
        boundary = local_midnight(DAY)
        first, last = boundary - 2 * HOUR, boundary + 3 * HOUR
        sleeps = [(boundary - HOUR, boundary + HOUR)]
        spans = journal_spans(first, last, 3 * HOUR, sleeps)
        self.assertEqual(spans, [(first, boundary - HOUR), (boundary + HOUR, last)])
        self.assertEqual(journal_spans(first, last, 3 * HOUR, []), [])

    def test_overlap_with_journal_does_not_double_count_but_live_suffix_counts(self):
        start = local_midnight(DAY) + 12 * HOUR
        rows = [
            ("boot", "journal", start, start + 2 * HOUR),
            ("boot", "live", start + HOUR, start + 3 * HOUR),
            ("other", "live", start + 4 * HOUR, start + 6 * HOUR),
        ]
        self.assertEqual(daily_awake(rows, [DAY])[DAY], 5 * HOUR)

    def test_calendar_month_boundaries_and_leap_day(self):
        current = date(2024, 3, 2)
        february = shift_month(current, -1)
        self.assertEqual(shift_month(date(2024, 1, 2), -1), date(2023, 12, 1))
        self.assertEqual(month_dates(february, current)[-1], date(2024, 2, 29))
        self.assertEqual(month_dates(current, current), [date(2024, 3, 1), current])

    def test_loading_an_older_month_recovers_only_boots_that_overlap_it(self):
        month = date(2026, 10, 1)
        historical = local_midnight(date(2026, 10, 14)) + 8 * HOUR
        now = local_midnight(DAY) + 17 * HOUR
        boots = [
            {"boot_id": "old", "first_entry": int(historical * 1e6),
             "last_entry": int((historical + 4 * HOUR) * 1e6)},
            {"boot_id": "current", "first_entry": int((now - HOUR) * 1e6),
             "last_entry": int(now * 1e6)},
        ]

        def command(*args):
            if "--list-boots" in args:
                return json.dumps(boots)
            if "--boot=old" in args:
                return json.dumps({
                    "__REALTIME_TIMESTAMP": int((historical + 4 * HOUR) * 1e6),
                    "__MONOTONIC_TIMESTAMP": 4 * HOUR * 1_000_000,
                })
            return ""

        with patch("timeday.uptime.journal_command", side_effect=command):
            self.assertEqual(
                journal_history("current", AwakePoint(now, HOUR, HOUR), month, shift_month(month, 1)),
                {"old": [(historical, historical + 4 * HOUR)]},
            )
