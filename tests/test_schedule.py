import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from timeday.events import (
    DAILY, WEEKDAYS_ONLY, Event, compact_event_layout, day_spans, event_lanes, minute_value,
    next_occurrence, occurrences,
)
from timeday.store import Store


MONDAY = date(2026, 11, 16)


class ScheduleTests(unittest.TestCase):
    def test_partial_occurrence_edit_inherits_series_fields(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        event_id = store.save_event('Training', MONDAY, 540, 600, DAILY)
        store.save_occurrence(event_id, MONDAY, day=MONDAY, color=2)
        items = occurrences(*store.calendar(), MONDAY, MONDAY)
        self.assertEqual([(i.title, i.start, i.end, i.color) for i in items],
                         [('Training', 540, 600, 2)])
        upcoming = next_occurrence(*store.calendar(), datetime(2026, 11, 16, 8))
        self.assertEqual((upcoming.title, upcoming.start, upcoming.end), ('Training', 540, 600))

    def test_appearance_persists_for_series_and_single_occurrence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calendar.sqlite3"
            store = Store(path)
            event_id = store.save_event("Training", MONDAY, 540, 600, DAILY, color=3, marker="🏋️")
            store.save_occurrence(event_id, MONDAY, "Training", MONDAY, 540, 600, color=1, marker="⭐")
            store.close()
            store = Store(path)
            try:
                items = occurrences(*store.calendar(), MONDAY, MONDAY + timedelta(days=1))
                self.assertEqual([(i.color, i.marker) for i in items], [(1, "⭐"), (3, "🏋️")])
                upcoming = next_occurrence(*store.calendar(), datetime(2026, 11, 16, 8))
                self.assertEqual((upcoming.color, upcoming.marker), (1, "⭐"))
                store.save_event("Training", MONDAY, 540, 600, DAILY, event_id, color=4, marker="📚")
                items = occurrences(*store.calendar(), MONDAY, MONDAY + timedelta(days=1))
                self.assertEqual([(i.color, i.marker) for i in items], [(1, "⭐"), (4, "📚")])
            finally:
                store.close()

    def test_weekday_and_selected_day_recurrence_respect_start_date(self):
        wednesday = MONDAY + timedelta(days=2)
        friday = MONDAY + timedelta(days=4)
        items = occurrences([
            Event(1, "Weekdays", wednesday, 540, 600, WEEKDAYS_ONLY),
            Event(2, "Selected days", wednesday, 600, 630, (1 << 0) | (1 << 4)),
        ], [], MONDAY, MONDAY + timedelta(days=7))
        self.assertEqual([i.day for i in items if i.event.id == 1], [
            wednesday, wednesday + timedelta(days=1), friday, MONDAY + timedelta(days=7),
        ])
        self.assertEqual([i.day for i in items if i.event.id == 2], [friday, MONDAY + timedelta(days=7)])

    def test_moved_occurrences_persist_and_delete_with_series(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calendar.sqlite3"
            first = Store(path)
            event_id = first.save_event("Training", MONDAY, 900, 960, DAILY)
            target = MONDAY + timedelta(days=10)
            first.save_occurrence(event_id, MONDAY, "Moved training", target, 1380, 1470)
            first.save_occurrence(event_id, MONDAY + timedelta(days=2))
            first.close()
            reopened = Store(path)
            try:
                before_move = occurrences(*reopened.calendar(), MONDAY, MONDAY + timedelta(days=3))
                self.assertEqual([i.day for i in before_move], [
                    MONDAY + timedelta(days=1), MONDAY + timedelta(days=3),
                ])
                day = occurrences(*reopened.calendar(), target, target)
                self.assertEqual([i.title for i in day], ["Training", "Moved training"])
                self.assertEqual((day[1].start, day[1].end, day[1].original_day), (1380, 1470, MONDAY))
                reopened.delete_event(event_id)
                self.assertEqual(reopened.calendar(), ([], []))
            finally:
                reopened.close()

    def test_next_event_across_many_skips_and_distant_one_off_events(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        far = MONDAY + timedelta(days=800)
        once = store.save_event("Distant", far, 540, 600)
        self.assertEqual(next_occurrence(*store.calendar(), datetime(2026, 11, 16, 15)).event.id, once)
        weekly = store.save_event("Weekly", MONDAY, 540, 600, 1)
        for offset in range(0, 91, 7):
            store.save_occurrence(weekly, MONDAY + timedelta(days=offset))
        upcoming = next_occurrence(*store.calendar(), datetime(2026, 11, 16, 15))
        self.assertEqual(upcoming.day, MONDAY + timedelta(days=91))
        self.assertEqual(upcoming.event.id, weekly)

    def test_overlap_layout_shares_width_only_among_colliding_events(self):
        spans = [(540, 600, "first"), (555, 580, "second"), (581, 620, "third"), (630, 660, "fourth")]
        self.assertEqual([(span[2], lane, count) for span, lane, count in event_lanes(spans)], [
            ("first", 0, 2), ("second", 1, 2), ("third", 1, 2), ("fourth", 0, 1),
        ])

    def test_agenda_orders_events_and_shows_the_overnight_continuation(self):
        tuesday = MONDAY + timedelta(days=1)
        items = occurrences([
            Event(1, "Evening", MONDAY, 1410, 1470),
            Event(2, "Lunch", tuesday, 720, 780),
            Event(3, "Morning", tuesday, 540, 600),
        ], [], MONDAY, tuesday)
        self.assertEqual([(start, end, item.title) for start, end, item in day_spans(items, tuesday)], [
            (0, 30, "Evening"), (540, 600, "Morning"), (720, 780, "Lunch"),
        ])

    def test_compact_week_keeps_short_events_visible_inside_the_grid(self):
        spans = [(0, 3, "Midnight"), (2, 5, "Overlap"), (1438, 1440, "Late")]
        for height in (240, 425, 575):
            layout = compact_event_layout(spans, height)
            self.assertEqual([entry[2] for entry in layout], [2, 2, 1])
            self.assertEqual(layout[0][0][0], 0)
            self.assertEqual(layout[-1][0][1], height)
            self.assertTrue(all(0 <= top < end <= height and end - top >= 17
                                for (top, end, _), _, _ in layout))

    def test_end_of_day_and_invalid_minutes(self):
        self.assertEqual(minute_value("09:30"), 570)
        self.assertEqual(minute_value("24:00", end=True), 1440)
        for value in ("24:00", "12:60", "12", "zz:00"):
            with self.assertRaises(ValueError):
                minute_value(value)
