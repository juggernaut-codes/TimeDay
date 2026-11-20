import unittest

from timeday.models import MIN_APP_SECONDS, summarize


class ActivityVisibilityTests(unittest.TestCase):
    def test_threshold_combines_samples_and_keeps_exactly_thirty_seconds(self):
        samples = [('Short', 0, 29.99), ('Exact', 0, 15), ('Exact', 30, 45), ('Long', 0, 60)]
        combined, series, ranked = summarize(samples, [0, 30, 60], MIN_APP_SECONDS)
        self.assertEqual(ranked, [('Long', 60), ('Exact', 30)])
        self.assertNotIn('Short', series)
        self.assertEqual(combined, [45, 45])
        self.assertEqual(len(samples), 4)

    def test_threshold_uses_only_time_inside_selected_period(self):
        combined, series, ranked = summarize([('Outside', -100, 20)], [0, 60], MIN_APP_SECONDS)
        self.assertEqual(combined, [0])
        self.assertEqual(series, {})
        self.assertEqual(ranked, [])
