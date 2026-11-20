import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from timeday.activity import ActivityRecorder, Sample
from timeday.app_icons import AppIcons
from timeday.presence import PresenceRecorder, daily_idle
from timeday.store import Store
from timeday.uptime import local_midnight


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.recorder = PresenceRecorder(self.store)

    def spans(self):
        return [tuple(row) for row in self.store.idle_intervals(0, 10000)]

    def test_threshold_and_input_resume_clip_interval(self):
        for sample in (Sample(1000, 100, None, 599), Sample(1002, 102, None, 601),
                       Sample(1004, 104, None, .5)):
            self.recorder.observe(sample)
        self.assertEqual(self.spans(), [(1001, 1003.5)])

    def test_playback_prevents_idle_and_pause_restores_detection(self):
        for sample in (Sample(1000, 100, 'Firefox', 800, playing=True),
                       Sample(1002, 102, 'Firefox', 802, playing=True),
                       Sample(1004, 104, 'Firefox', 804), Sample(1006, 106, 'Firefox', 806)):
            self.recorder.observe(sample)
        self.assertEqual(self.spans(), [(1004, 1006)])

    def test_suspend_clock_jump_and_missing_samples_do_not_inflate_idle(self):
        for sample in (Sample(1000, 100, None, 800), Sample(5000, 102, None, 802),
                       None, Sample(5002, 104, None, 804), Sample(5008, 110, None, 810)):
            self.recorder.observe(sample)
        self.assertEqual(self.spans(), [])

    def test_locked_session_is_idle_even_with_player_running(self):
        self.recorder.observe(Sample(1000, 100, None, 0, playing=True, locked=True))
        self.recorder.observe(Sample(1002, 102, None, 2, playing=True, locked=True))
        self.assertEqual(self.spans(), [(1000, 1002)])

    def test_daily_idle_splits_midnight_and_merges_overlaps(self):
        day = date(2026, 11, 19)
        midnight = local_midnight(day)
        result = daily_idle([(midnight - 60, midnight + 60), (midnight, midnight + 120)],
                            [day - timedelta(days=1), day])
        self.assertEqual(list(result.values()), [60, 120])

    def test_idle_persists_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'presence.sqlite3'
            store = Store(path)
            store.record_idle(100, 110)
            store.record_idle(110, 120)
            store.close()
            store = Store(path)
            try:
                self.assertEqual([tuple(row) for row in store.idle_intervals(105, 115)], [(105, 115)])
            finally:
                store.close()

    def test_timeday_is_hidden_in_old_activity_and_not_recorded(self):
        icons = SimpleNamespace(canonical_name=lambda name: 'TimeDay' if name == 'alias' else name)
        rows = [('TimeDay', 1, 2), ('io.github.timeday.TimeDay', 1, 2), ('alias', 1, 2), ('Firefox', 1, 2)]
        self.assertEqual(AppIcons.samples(icons, rows), [('Firefox', 1, 2)])
        recorder = ActivityRecorder(self.store)
        recorder.observe(Sample(1000, 100, 'TimeDay', 0), 600)
        recorder.observe(Sample(1002, 102, 'Firefox', 0), 600)
        self.assertEqual(self.store.activity(0, 2000), [])

    def test_background_media_does_not_inflate_foreground_app_activity(self):
        recorder = ActivityRecorder(self.store)
        recorder.observe(Sample(1000, 100, 'Firefox', 800, playing=True), 600)
        recorder.observe(Sample(1002, 102, 'Firefox', 802, playing=True), 600)
        self.assertEqual(self.store.activity(0, 2000), [])
