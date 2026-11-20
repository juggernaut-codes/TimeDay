"""Local calendar dates, weekly rules and occurrence overrides."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .i18n import tr


DAILY = 0b1111111
WEEKDAYS_ONLY = 0b0011111


@dataclass(frozen=True)
class Event:
    id: int
    title: str
    day: date
    start: int
    end: int
    repeat: int = 0
    color: int = 0
    marker: str = ""

    def occurs(self, day):
        return day == self.day if not self.repeat else (
            day >= self.day and bool(self.repeat & (1 << day.weekday()))
        )


@dataclass(frozen=True)
class Occurrence:
    event: Event
    original_day: date
    title: str
    day: date
    start: int
    end: int

    color: int = 0
    marker: str = ""

    @classmethod
    def from_event(cls, event, day):
        return cls(event, day, event.title, day, event.start, event.end, event.color, event.marker)

    @property
    def starts_at(self):
        return datetime.combine(self.day, time()) + timedelta(minutes=self.start)


@dataclass(frozen=True)
class Override:
    event_id: int
    original_day: date
    title: str | None
    day: date | None
    start: int | None
    end: int | None
    color: int | None = None
    marker: str | None = None


def minute_text(minute):
    hours, minutes = divmod(minute, 60)
    return f"{hours:02d}:{minutes:02d}"


def time_range(start, end):
    if end > 1440:
        return tr('{0}–{1} (+1 день)', minute_text(start), minute_text(end - 1440))
    return f"{minute_text(start)}–{minute_text(end)}"


def minute_value(value, end=False):
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
        raise ValueError(tr('Введите время в формате ЧЧ:ММ.'))
    hours, minutes = map(int, parts)
    if end and hours == 24 and minutes == 0:
        return 1440
    if hours > 23 or minutes > 59:
        raise ValueError(tr('Введите время в диапазоне 00:00–23:59.'))
    return hours * 60 + minutes


def week_start(day):
    return day - timedelta(days=day.weekday())


def day_spans(items, day):
    """List the portions of calendar events visible on this date, in time order."""
    parts = []
    for item in items:
        if item.day == day:
            parts.append((item.start, min(item.end, 1440), item))
        elif item.end > 1440 and item.day.toordinal() + 1 == day.toordinal():
            parts.append((0, item.end - 1440, item))
    return sorted(parts, key=lambda span: (*span[:2], span[2].title.casefold()))


def occurrences(events, overrides, first, last):
    """Expand an inclusive range of start dates, including moved instances."""
    suppressed = {(item.event_id, item.original_day) for item in overrides}
    by_id = {event.id: event for event in events}
    result = []
    for event in events:
        day = max(first, event.day)
        if not event.repeat:
            if first <= event.day <= last:
                result.append(Occurrence.from_event(event, event.day))
            continue
        while day <= last:
            if event.occurs(day) and (event.id, day) not in suppressed:
                result.append(Occurrence.from_event(event, day))
            if day == last:
                break
            day += timedelta(days=1)
    for item in overrides:
        event = by_id.get(item.event_id)
        if item.day is not None and first <= item.day <= last and event and event.occurs(item.original_day):
            result.append(Occurrence(event, item.original_day, item.title, item.day, item.start, item.end,
                                     event.color if item.color is None else item.color,
                                     event.marker if item.marker is None else item.marker))
    return sorted(result, key=lambda item: (item.day, item.start, item.end, item.event.id, item.original_day))


def next_occurrence(events, overrides, now):
    """Find the next event even if its first date or override is far in the future."""
    candidates = []
    suppressed = {(item.event_id, item.original_day) for item in overrides}
    by_id = {event.id: event for event in events}
    for event in events:
        if not event.repeat:
            candidate = Occurrence.from_event(event, event.day)
            if candidate.starts_at >= now:
                candidates.append(candidate)
            continue
        day = max(event.day, now.date())
        # Every remaining exception can block at most one regularly scheduled occurrence.
        last = min(date.max.toordinal(), day.toordinal() + 7 * (len(suppressed) + 2))
        for ordinal in range(day.toordinal(), last + 1):
            current = date.fromordinal(ordinal)
            if event.occurs(current) and (event.id, current) not in suppressed:
                candidate = Occurrence.from_event(event, current)
                if candidate.starts_at >= now:
                    candidates.append(candidate)
                    break
    for item in overrides:
        event = by_id.get(item.event_id)
        if item.day is not None and event and event.occurs(item.original_day):
            candidate = Occurrence(event, item.original_day, item.title, item.day, item.start, item.end,
                                   event.color if item.color is None else item.color,
                                   event.marker if item.marker is None else item.marker)
            if candidate.starts_at >= now:
                candidates.append(candidate)
    return min(candidates, key=lambda item: (item.starts_at, item.event.id), default=None)


def event_lanes(spans):
    """Return (span, column, number of columns) for overlapping display intervals."""
    placed, cluster, ends = [], [], []
    for span in sorted(spans, key=lambda value: (value[0], value[1])):
        start, end = span[:2]
        if ends and start >= max(ends):
            placed.extend((item, lane, len(ends)) for item, lane in cluster)
            cluster, ends = [], []
        lane = next((index for index, old_end in enumerate(ends) if old_end <= start), len(ends))
        if lane == len(ends):
            ends.append(end)
        else:
            ends[lane] = end
        cluster.append((span, lane))
    placed.extend((item, lane, len(ends)) for item, lane in cluster)
    return placed


def compact_event_layout(spans, height, minimum=17):
    """Avoid overlap between readable event blocks at the current zoom level."""
    minimum = min(height, minimum)
    blocks = []
    for start, end, item in spans:
        top = min(round(start * height / 1440), height - minimum)
        bottom = min(height, max(top + minimum, round(end * height / 1440)))
        blocks.append((top, bottom, item))
    return event_lanes(blocks)
