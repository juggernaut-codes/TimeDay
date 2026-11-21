import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from timeday.events import occurrences
from timeday.store import Store


class ScheduleDragTests(unittest.TestCase):
    def test_swap_persists_with_durations_and_metadata(self):
        day = date(2026, 11, 20)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'calendar.sqlite3'
            store = Store(path)
            store.save_event('First', day, 480, 540, color=2, marker='book')
            store.save_event('Second', day + timedelta(days=1), 900, 1020, color=3)
            first, second = occurrences(*store.calendar(), day, day + timedelta(days=1))
            store.move_calendar_item(first, second.day, second.start, second)
            store.close()
            store = Store(path)
            self.addCleanup(store.close)
            items = {item.title: item for item in occurrences(*store.calendar(), day, day + timedelta(days=1))}
            self.assertEqual((items['First'].day, items['First'].start, items['First'].end),
                             (second.day, 900, 960))
            self.assertEqual((items['Second'].day, items['Second'].start, items['Second'].end),
                             (day, 480, 600))
            self.assertEqual((items['First'].color, items['First'].marker), (2, 'book'))

    def test_recurring_move_only_changes_selected_occurrence_and_can_move_again(self):
        day = date(2026, 11, 20)
        store = Store(':memory:')
        self.addCleanup(store.close)
        store.save_event('Daily', day, 1380, 1500, repeat=127)
        item = occurrences(*store.calendar(), day, day)[0]
        store.move_calendar_item(item, day + timedelta(days=1), 600)
        items = occurrences(*store.calendar(), day, day + timedelta(days=2))
        self.assertFalse(any(item.day == day for item in items))
        moved = next(item for item in items if item.original_day == day)
        self.assertEqual((moved.start, moved.end), (600, 720))
        store.move_calendar_item(moved, day, 1425)
        moved = next(item for item in occurrences(*store.calendar(), day, day + timedelta(days=2))
                     if item.original_day == day)
        self.assertEqual((moved.day, moved.start, moved.end), (day, 1425, 1545))
        self.assertEqual(len(store.calendar()[1]), 1)
        self.assertEqual(store.calendar()[0][0].start, 1380)

    def test_failed_swap_rolls_back_first_move(self):
        day = date(2026, 11, 20)
        store = Store(':memory:')
        self.addCleanup(store.close)
        store.save_event('First', day, 480, 540)
        store.save_event('Second', day, 900, 960)
        first, second = occurrences(*store.calendar(), day, day)
        store.db.execute("""CREATE TRIGGER reject_second BEFORE UPDATE ON calendar_events
            WHEN OLD.title = 'Second' BEGIN SELECT RAISE(ABORT, 'test'); END""")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            store.move_calendar_item(first, second.day, second.start, second)
        self.assertEqual(store.calendar()[0][0].start, 480)
