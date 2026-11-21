"""Clock and calendar logic shared by the desktop views."""

import json
import math
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time as midnight, timedelta

from .i18n import TranslatedNames, tr


MAX_COUNTDOWN = 100 * 3600 - 1


def clock_state(value):
    try:
        state = json.loads(value)
    except (ValueError, RecursionError):
        state = {}
    if not isinstance(state, dict):
        state = {}

    def seconds(value, maximum, default=0):
        return value if type(value) in (int, float) and 0 <= value <= maximum else default

    mode = state.get("mode")
    duration = int(seconds(state.get("duration"), MAX_COUNTDOWN, 25 * 60))
    stopwatch = seconds(state.get("stopwatch"), 2**53)
    laps = []
    if isinstance(state.get("laps"), list):
        for value in state["laps"]:
            elapsed = seconds(value, stopwatch, None)
            if elapsed is not None and (not laps or elapsed >= laps[-1]):
                laps.append(elapsed)
    return {
        "mode": mode if mode in ("countdown", "stopwatch") else "countdown",
        "duration": duration, "countdown": seconds(state.get("countdown"), duration),
        "stopwatch": stopwatch, "laps": laps,
    }


def elapsed_clock():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class Clock:
    def __init__(self, duration=None, now=elapsed_clock):
        self.duration = duration
        self.now = now
        self.started = None
        self.saved = 0.0

    @property
    def running(self):
        return self.started is not None

    @property
    def elapsed(self):
        value = self.saved + (self.now() - self.started if self.running else 0)
        return min(value, self.duration) if self.duration is not None else value

    @property
    def remaining(self):
        return max(0, math.ceil(self.duration - self.elapsed))

    def start(self):
        if not self.running and (self.duration is None or self.remaining > 0):
            self.started = self.now()

    def pause(self):
        self.saved = self.elapsed
        self.started = None

    def reset(self):
        self.saved = 0.0
        self.started = None

    def check_finished(self):
        if self.duration is not None and self.running and self.remaining == 0:
            self.pause()
            return True
        return False


MONTH_NAMES = TranslatedNames((
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
))
MONTHS = TranslatedNames((
    "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
    "сентября", "октября", "ноября", "декабря",
))
WEEKDAYS = TranslatedNames(("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"))


def date_label(day):
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def clock_label(seconds):
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def duration_label(seconds):
    if 0 < seconds < 1:
        return tr('<1 с')
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return tr('{0} ч {1:02d} мин', hours, minutes)
    if minutes:
        return tr('{0} мин', minutes)
    return tr('{0} с', seconds)


def period_bins(period, today=None):
    today = today or date.today()
    tomorrow = today + timedelta(days=1)
    end = datetime.combine(tomorrow, midnight.min).timestamp()
    if period == "day":
        start = datetime.combine(today, midnight.min).timestamp()
        edges = list(range(int(start), int(end), 3600)) + [end]
        labels = [datetime.fromtimestamp(edge).strftime("%H:%M") for edge in edges[:-1]]
        return edges, labels

    days = 7 if period == "week" else 30
    dates = [tomorrow - timedelta(days=days - index) for index in range(days + 1)]
    edges = [datetime.combine(day, midnight.min).timestamp() for day in dates]
    return edges, [day.strftime("%d.%m") for day in dates[:-1]]


MIN_APP_SECONDS = 30


def summarize(samples, edges, minimum_seconds=0):
    series = {}
    totals = defaultdict(float)
    for app, started, ended in samples:
        if app not in series:
            series[app] = [0.0] * (len(edges) - 1)
        buckets = series[app]
        index = max(0, bisect_right(edges, started) - 1)
        while index < len(buckets) and edges[index] < ended:
            seconds = max(0, min(ended, edges[index + 1]) - max(started, edges[index]))
            buckets[index] += seconds
            totals[app] += seconds
            index += 1
    if minimum_seconds:
        series = {app: values for app, values in series.items() if totals[app] >= minimum_seconds}
        totals = {app: seconds for app, seconds in totals.items() if app in series}
    apps = sorted(totals, key=lambda app: (-totals[app], app.casefold()))
    combined = [sum(bucket) for bucket in zip(*series.values())] if series else [0] * (len(edges) - 1)
    return combined, series, [(app, totals[app]) for app in apps]
