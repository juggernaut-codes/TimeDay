"""Input inactivity, independent of foreground applications and PC power state."""

from .uptime import merge_intervals, local_midnight
from datetime import timedelta

IDLE_SECONDS = 10 * 60
SELF_NAMES = frozenset({'timeday', 'io.github.timeday.timeday'})


def is_self_app(name):
    return bool(name and name.casefold().removesuffix('.desktop') in SELF_NAMES)


class PresenceRecorder:
    def __init__(self, store):
        self.store = store
        self.previous = None

    def observe(self, sample):
        previous, self.previous = self.previous, sample
        if sample is None or previous is None:
            return
        elapsed = sample.steady - previous.steady
        if not 0 < elapsed <= 3 or abs(sample.wall - previous.wall - elapsed) > .5:
            return
        if previous.playing and not previous.locked:
            return
        start = previous.wall if previous.locked else previous.wall + max(0, IDLE_SECONDS - previous.idle)
        end = sample.wall
        if not sample.locked and sample.idle + .06 < previous.idle + elapsed:
            end = max(previous.wall, sample.wall - sample.idle)
        if end > start:
            self.store.record_idle(start, end)


def daily_idle(rows, days):
    spans = merge_intervals(rows)
    return {day: sum(max(0, min(end, local_midnight(day + timedelta(days=1))) -
                                 max(start, local_midnight(day))) for start, end in spans) for day in days}
