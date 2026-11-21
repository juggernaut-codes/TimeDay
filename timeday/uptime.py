"""Awake computer time by local calendar day, across Linux boots."""

import calendar
import json
import math
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path


@dataclass(frozen=True)
class AwakePoint:
    wall: float
    awake: float
    boot: float

    @classmethod
    def now(cls):
        return cls(time.time(), time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME))


def local_midnight(day):
    return datetime.combine(day, day_time.min).timestamp()


def shift_month(day, offset):
    index = (day.year - 1) * 12 + day.month - 1 + offset
    index = max(0, min(index, 9999 * 12 - 1))
    return date(index // 12 + 1, index % 12 + 1, 1)


def month_dates(month, today):
    last = calendar.monthrange(month.year, month.month)[1]
    return [date(month.year, month.month, day) for day in range(1, last + 1)
            if date(month.year, month.month, day) <= today]


def live_interval(previous, current):
    elapsed = current.wall - previous.wall
    awake = current.awake - previous.awake
    since_boot = current.boot - previous.boot
    if elapsed <= 0 or awake <= 0 or abs(elapsed - since_boot) > 2:
        return None
    if abs(since_boot - awake) < .1:
        return previous.wall, current.wall
    same_day = datetime.fromtimestamp(previous.wall).date() == datetime.fromtimestamp(current.wall).date()
    if same_day or awake <= 15:
        # The change in the two kernel clocks measures exactly how much time was asleep.
        return current.wall - awake, current.wall
    return None


def merge_intervals(spans):
    merged = []
    for start, end in sorted((start, end) for start, end in spans):
        if end <= start:
            continue
        if merged and merged[-1][1] >= start:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    return merged


def daily_awake(rows, days):
    result = {}
    for day in days:
        start = local_midnight(day)
        end = local_midnight(day + timedelta(days=1))
        by_boot = defaultdict(lambda: defaultdict(list))
        for boot_id, source, opened, closed in rows:
            if start < closed and opened < end:
                by_boot[boot_id][source].append((max(start, opened), min(end, closed)))
        total = 0
        for sources in by_boot.values():
            live, journal = (merge_intervals(sources[source]) for source in ("live", "journal"))
            live_seconds = sum(stop - begin for begin, stop in live)
            if journal:
                journal_seconds = sum(stop - begin for begin, stop in journal)
                captured_until = journal[-1][1]
                after_journal = sum(max(0, stop - max(begin, captured_until)) for begin, stop in live)
                live_seconds = max(live_seconds, journal_seconds + after_journal)
            total += live_seconds
        result[day] = total
    return result


def sleep_ranges(entries):
    pending = {}
    result = defaultdict(list)
    for entry in entries:
        message = str(entry.get("MESSAGE", "")).casefold()
        boot_id = entry.get("_BOOT_ID")
        stamp = entry.get("__REALTIME_TIMESTAMP")
        if not boot_id or stamp is None:
            continue
        stamp = int(stamp) / 1e6
        if "entering sleep state" in message or "pm: suspend entry" in message or "pm: hibernation entry" in message:
            pending.setdefault(boot_id, stamp)
        elif "returned from sleep state" in message or "pm: suspend exit" in message or "pm: hibernation exit" in message:
            start = pending.pop(boot_id, None)
            if start is not None and stamp > start:
                result[boot_id].append((start, stamp))
    return result


def journal_spans(first, last, awake, sleeps):
    if awake <= 0 or first > last:
        return []
    sleeps = merge_intervals((max(first, start), min(last, end)) for start, end in sleeps)
    asleep = sum(end - start for start, end in sleeps)
    if last - first - asleep > awake + 15:
        # Without sleep timestamps the duration can only be assigned if it falls on one date.
        if datetime.fromtimestamp(first).date() == datetime.fromtimestamp(last).date():
            return [(max(local_midnight(datetime.fromtimestamp(last).date()), last - awake), last)]
        return []
    start = last - awake - asleep
    spans = []
    for sleeping, waking in sleeps:
        if sleeping > start:
            spans.append((start, sleeping))
        start = max(start, waking)
    if last > start:
        spans.append((start, last))
    return spans


def journal_command(*args):
    try:
        result = subprocess.run(
            ("journalctl", "--system", "--quiet", "--no-pager", "--output=json", *args),
            capture_output=True, text=True, errors="replace", timeout=1.5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout


def json_lines(value):
    for line in value.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def journal_history(current_id, current, first_day=None, before_day=None):
    """Recover logged boots and sleep boundaries without requesting new privileges."""
    try:
        boots = json.loads(journal_command("--list-boots"))
    except ValueError:
        return {}
    today = datetime.fromtimestamp(current.wall).date()
    since = local_midnight(first_day or today - timedelta(days=8))
    until = min(current.wall, local_midnight(before_day)) if before_day else current.wall
    candidates = []
    for boot in boots:
        boot_id = boot["boot_id"]
        if boot_id == current_id:
            begin, end = current.wall - current.boot, current.wall
        else:
            begin, end = boot["first_entry"] / 1e6, boot["last_entry"] / 1e6
        if begin < until and end >= since:
            candidates.append((boot_id, begin, end))
    if not candidates:
        return {}
    sleepers = sleep_ranges(json_lines(journal_command(
        f"--since=@{int(min(begin for _, begin, _ in candidates))}",
        f"--until=@{int(max(end for _, _, end in candidates)) + 1}",
        "--grep=Entering sleep state|returned from sleep state|PM: (suspend|hibernation) (entry|exit)",
        "--output-fields=MESSAGE,_BOOT_ID",
        "SYSLOG_IDENTIFIER=systemd-sleep", "+", "_TRANSPORT=kernel",
    )))
    result = {}
    for boot_id, first, end in candidates:
        if boot_id == current_id:
            awake = current.awake
        else:
            last = next(json_lines(journal_command(
                f"--boot={boot_id}", "--lines=1", "--output-fields=_BOOT_ID",
            )), None)
            if last is None:
                continue
            end = int(last["__REALTIME_TIMESTAMP"]) / 1e6
            awake = int(last["__MONOTONIC_TIMESTAMP"]) / 1e6
        spans = journal_spans(first, end, awake, sleepers.get(boot_id, ()))
        if spans:
            result[boot_id] = spans
    return result


class ComputerUptime:
    def __init__(self, store, boot_id=None):
        self.store = store
        self.boot_id = boot_id or Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
        self.previous = None
        try:
            data = json.loads(store.setting("pc_awake_checkpoint", "{}"))
            if data["boot_id"] == self.boot_id:
                point = AwakePoint(**data["point"])
                if all(type(value) in (int, float) and math.isfinite(value)
                       for value in (point.wall, point.awake, point.boot)) and 0 <= point.awake <= point.boot:
                    self.previous = point
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            pass

    def checkpoint(self, point=None):
        point = point or AwakePoint.now()
        previous = self.previous or AwakePoint(point.wall - point.boot, 0, 0)
        interval = live_interval(previous, point)
        self.store.save_awake_checkpoint(self.boot_id, interval, json.dumps({
            "boot_id": self.boot_id, "point": asdict(point),
        }))
        self.previous = point

    def idle_totals(self, days):
        from .presence import daily_idle
        rows = self.store.idle_intervals(local_midnight(min(days)), local_midnight(max(days) + timedelta(days=1)))
        return daily_idle(rows, days)

    def totals(self, days):
        rows = self.store.awake_intervals(local_midnight(min(days)), local_midnight(max(days) + timedelta(days=1)))
        return daily_awake(rows, days)
