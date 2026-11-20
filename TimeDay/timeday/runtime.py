"""Sample the lifetime of desktop applications independently of window focus."""

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .presence import is_self_app


SYSTEM_NAMES = frozenset({
    "cinnamon", "cinnamon (wayland session)", "muffin", "mate-panel", "xfce4-panel",
    "gnome-shell", "org.gnome.shell", "xfdesktop", "plasmashell",
    "files", "файлы", "nemo", "nemo-desktop", "nautilus", "org.gnome.nautilus", "caja", "thunar",
    "geoclue demo agent", "geoclue-demo-agent", "portal", "ibus", "ibus wayland", "emoji choice",
    "events and tasks reminders", "evolution-alarm-notify", "blueman-applet", "nm-applet",
    "update manager", "system information", "mintupdate", "mintreport", "mintwelcome",
    "gnome-control-center", "org.gnome.settings", "fresco", "frescod", "io.github.dibbayajyotiroy.fresco",
})
SYSTEM_PREFIXES = (
    "cinnamon-", "csd-", "org.cinnamon.settings", "org.freedesktop.ibus.",
    "ibus-", "xdg-desktop-portal", "org.freedesktop.portal.", "polkit-",
    "org.gnome.evolution-alarm-notify", "mintupdate-", "mintreport-", "xapp-sn-watcher",
)


def is_runtime_app(name, excluded=()):
    key = name.casefold().removesuffix(".desktop")
    return not is_self_app(name) and key not in excluded and key not in SYSTEM_NAMES and not key.startswith(SYSTEM_PREFIXES)


@dataclass(frozen=True)
class RuntimeSample:
    wall: float
    steady: float
    apps: frozenset


class ProcessApps:
    def __init__(self, executables, names, proc="/proc", *, excluded=(), steam=None):
        self.executables = executables
        self.names = names
        self.proc = Path(proc)
        self.flatpak = shutil.which("flatpak") if self.proc == Path("/proc") else None
        self.uid = os.getuid()
        self.excluded = excluded
        self.window_names = {}
        self.exe_names = {}
        self.steam = steam

    def snapshot(self, window_pids):
        """Return recognized applications as a set, including ones with no visible window."""
        windows, executables = {}, {}
        result = set()
        try:
            entries = self.proc.iterdir()
            for directory in entries:
                if not directory.name.isdecimal():
                    continue
                try:
                    if directory.stat().st_uid != self.uid:
                        continue
                    pid = int(directory.name)
                    stat = (directory / "stat").read_text(errors="replace")
                    prefix, _, fields = stat.rpartition(")")
                    fields = fields.split()
                    if len(fields) < 20 or fields[0] in {"Z", "X"}:
                        continue
                    comm = prefix.partition("(")[2].casefold()
                    if not is_runtime_app(comm):
                        continue
                    identity = (pid, fields[19])
                    name = window_pids.get(pid) or self.window_names.get(identity)
                    if name:
                        windows[identity] = name
                    else:
                        name = self.executables.get(comm)
                        if not name:
                            key = (*identity, comm)
                            if key in self.exe_names:
                                name = self.exe_names[key]
                            else:
                                exe_path = None
                                try:
                                    exe_path = os.readlink(directory / "exe").removesuffix(" (deleted)")
                                    exe = os.path.basename(exe_path)
                                    name = self.executables.get(exe.casefold())
                                except OSError:
                                    pass
                                if self.steam is not None and name is None:
                                    name = self.steam.process_name(directory, exe_path, comm)
                            executables[key] = name
                    if name and is_runtime_app(name, self.excluded):
                        result.add(name)
                except OSError:
                    # The process can exit before its metadata is read.
                    continue
        except OSError:
            return None
        self.window_names, self.exe_names = windows, executables
        result.update(self.flatpak_apps())
        return RuntimeSample(time.time(), time.monotonic(), frozenset(result))

    def flatpak_apps(self):
        if not self.flatpak:
            return ()
        try:
            completed = subprocess.run(
                (self.flatpak, "ps", "--columns=application"), capture_output=True, text=True,
                errors="replace", timeout=.4, check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        return [self.names.get(app.casefold(), app) for app in completed.stdout.split()
                if "." in app and app.isascii() and is_runtime_app(app, self.excluded)
                and is_runtime_app(self.names.get(app.casefold(), app), self.excluded)]


class RuntimeRecorder:
    def __init__(self, store):
        self.store = store
        self.previous = None
        self.current = None

    def observe(self, sample):
        previous, self.previous = self.previous, sample
        self.current = sample.apps if sample else None
        if sample is None or previous is None:
            return
        elapsed = sample.steady - previous.steady
        wall = sample.wall - previous.wall
        if not 0 < elapsed <= 7 or wall <= 0 or abs(elapsed - wall) > .5:
            return
        # For starts and exits between two samples, estimate the boundary halfway.
        middle = previous.wall + wall / 2
        records = []
        for name in previous.apps | sample.apps:
            start = previous.wall if name in previous.apps else middle
            end = sample.wall if name in sample.apps else middle
            records.append((name, start, end))
        self.store.record_runtime(records)
