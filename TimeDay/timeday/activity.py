"""EWMH foreground tracking, with a short heartbeat for idle detection and persistence."""

import ctypes as c
import os
import time
from dataclasses import dataclass

from .i18n import tr
from .presence import is_self_app


NON_APPLICATION_WINDOWS = ("DESKTOP", "DOCK", "NOTIFICATION")
LOCK_CLASSES = {"cinnamon-screensaver", "light-locker", "xscreensaver", "xfce4-screensaver"}


def window_icon(values):
    best, score, offset = None, None, 0
    while offset + 2 <= len(values):
        width, height = map(int, values[offset:offset + 2])
        pixels = width * height
        offset += 2
        if width <= 0 or height <= 0 or pixels > len(values) - offset:
            break
        if width <= 1024 and height <= 1024:
            candidate = abs(max(width, height) - 64)
            if score is None or candidate < score:
                best, score = (width, height, values[offset:offset + pixels]), candidate
        offset += pixels
    return best


class ScreenInfo(c.Structure):
    _fields_ = [
        ("window", c.c_ulong), ("state", c.c_int), ("kind", c.c_int),
        ("til_or_since", c.c_ulong), ("idle", c.c_ulong), ("event_mask", c.c_ulong),
    ]


@dataclass(frozen=True)
class Sample:
    wall: float
    steady: float
    app: str | None
    idle: float
    playing: bool = False
    locked: bool = False


class X11Monitor:
    def __init__(self, names=None, resolve_name=None, accept_icon=None, resolve_window=None):
        self.names = names or {}
        self.resolve_name = resolve_name or (lambda name: name)
        self.accept_icon = accept_icon
        self.resolve_window = resolve_window
        self.checked_icons = {}
        self.display = None
        self.idle_display = None
        self.problem = None
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland" or (not session and os.environ.get("WAYLAND_DISPLAY")):
            self.problem = tr('Для записи активности войдите в сеанс X11.')
            return
        try:
            from Xlib import X, Xatom, display, error
            self.xlib, self.types, self.errors = X, Xatom, error
        except ImportError:
            self.problem = tr('Для мониторинга установите python3-xlib.')
            return
        try:
            self.x11 = c.CDLL("libX11.so.6")
            self.xss = c.CDLL("libXss.so.1")
        except OSError:
            self.problem = tr('Для мониторинга нужны libx11-6 и libxss1.')
            return
        self.x11.XOpenDisplay.argtypes = [c.c_char_p]
        self.x11.XOpenDisplay.restype = c.c_void_p
        self.x11.XDefaultRootWindow.argtypes = [c.c_void_p]
        self.x11.XDefaultRootWindow.restype = c.c_ulong
        self.x11.XCloseDisplay.argtypes = [c.c_void_p]
        self.xss.XScreenSaverQueryInfo.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(ScreenInfo)]
        try:
            self.display = display.Display()
            self.root = self.display.screen().root
            self.active_atom = self.display.intern_atom("_NET_ACTIVE_WINDOW")
            self.client_atom = self.display.intern_atom("_NET_CLIENT_LIST")
            self.window_type = self.display.intern_atom("_NET_WM_WINDOW_TYPE")
            self.pid_atom = self.display.intern_atom("_NET_WM_PID")
            self.icon_atom = self.display.intern_atom("_NET_WM_ICON")
            self.title_atom = self.display.intern_atom("_NET_WM_NAME")
            self.ignored_types = {
                self.display.intern_atom(f"_NET_WM_WINDOW_TYPE_{kind}") for kind in NON_APPLICATION_WINDOWS
            }
            self.root.change_attributes(event_mask=X.PropertyChangeMask)
            self.display.flush()
        except (error.DisplayError, OSError):
            self.problem = tr('Нет соединения с X11.')
            self.close()
            return
        self.idle_display = self.x11.XOpenDisplay(None)
        if self.idle_display:
            self.idle_root = self.x11.XDefaultRootWindow(self.idle_display)
            if self._idle_info() is not None:
                return
        self.problem = tr('X11 не предоставляет данные о простое.')
        self.close()

    def close(self):
        if self.display:
            try:
                self.display.close()
            except self.errors.ConnectionClosedError:
                pass
            self.display = None
        if self.idle_display:
            self.x11.XCloseDisplay(self.idle_display)
            self.idle_display = None

    def fileno(self):
        return self.display.fileno()

    def drain_focus_events(self):
        count = 0
        while self.display.pending_events():
            event = self.display.next_event()
            if event.type == self.xlib.PropertyNotify and event.atom == self.active_atom:
                count += 1
        return count

    def _idle_info(self):
        info = ScreenInfo()
        if self.xss.XScreenSaverQueryInfo(self.idle_display, self.idle_root, c.byref(info)):
            return info
        return None

    def sample(self, locked=False):
        info = self._idle_info()
        if info is None:
            return None
        app = None
        if not locked and info.state not in (1, 2):  # ScreenSaverOn / ScreenSaverCycle
            app = self._active_app()
        return Sample(time.time(), time.monotonic(), app, info.idle / 1000, locked=locked or info.state in (1, 2))

    def _window_ids(self, atom):
        prop = self.root.get_full_property(atom, self.types.WINDOW)
        return list(prop.value) if prop is not None else []

    def _active_app(self):
        active = self._window_ids(self.active_atom)
        return self._window_app(active[0]) if active and active[0] else None

    def background_apps(self):
        if not self.display:
            return []
        foreground = self._active_app()
        names = {self._window_app(window) for window in self._window_ids(self.client_atom)}
        return sorted(names - {None, foreground}, key=str.casefold)

    def window_processes(self):
        """Link running PIDs to names for desktop apps with unfamiliar executables."""
        result = {}
        windows = self._window_ids(self.client_atom)
        self.checked_icons = {xid: stamp for xid, stamp in self.checked_icons.items() if xid in windows}
        for xid in windows:
            name = self._window_app(xid)
            if not name:
                continue
            try:
                window = self.display.create_resource_object("window", xid)
                prop = window.get_full_property(self.pid_atom, self.types.CARDINAL)
                if prop is not None and len(prop.value) and prop.value[0] > 0:
                    result[int(prop.value[0])] = name
            except self.errors.BadWindow:
                continue
        return result

    def _window_app(self, window_id):
        try:
            window = self.display.create_resource_object("window", window_id)
            types = window.get_full_property(self.window_type, self.types.ATOM)
            if types is not None and self.ignored_types.intersection(types.value):
                return None
            classes = tuple(filter(None, window.get_wm_class() or ()))
            if not classes or LOCK_CLASSES.intersection(name.casefold() for name in classes):
                return None
            for identity in reversed(classes):
                if identity.casefold() in self.names:
                    name = self.names[identity.casefold()]
                    break
            else:
                name = self.resolve_name(classes[-1])
            if self.resolve_window is not None:
                title = window.get_full_property(self.title_atom, self.xlib.AnyPropertyType)
                caption = title.value if title is not None else window.get_wm_name()
                if isinstance(caption, bytes):
                    caption = caption.decode("utf-8", errors="replace")
                name = self.resolve_window(name, caption)
            if is_self_app(name):
                return None
            if self.accept_icon is not None and time.monotonic() >= self.checked_icons.get(window_id, 0):
                prop = window.get_property(self.icon_atom, self.types.CARDINAL, 0, 1024 * 1024)
                pixels = window_icon(prop.value) if prop is not None else None
                if pixels is not None and self.accept_icon(name, pixels):
                    self.checked_icons[window_id] = float("inf")
                else:
                    self.checked_icons[window_id] = time.monotonic() + 5
            return name
        except self.errors.BadWindow:
            return None


class ActivityRecorder:
    def __init__(self, store):
        self.store = store
        self.previous = None

    def observe(self, sample, idle_limit):
        previous, self.previous = self.previous, sample
        if sample is None or previous is None or previous.app is None or is_self_app(previous.app):
            return
        elapsed = sample.steady - previous.steady
        wall_elapsed = sample.wall - previous.wall
        if not 0 < elapsed <= 3 or wall_elapsed <= 0 or abs(wall_elapsed - elapsed) > .5:
            return
        cut = previous.wall + max(0, idle_limit - previous.idle)
        renewed = sample.idle + .06 < previous.idle + elapsed
        if renewed and previous.app == sample.app:
            input_at = max(previous.wall, sample.wall - sample.idle)
            if input_at <= cut:
                cut = sample.wall
            else:
                self.store.record_activity(previous.app, previous.wall, min(cut, sample.wall))
                self.store.record_activity(previous.app, input_at, sample.wall)
                return
        self.store.record_activity(previous.app, previous.wall, min(cut, sample.wall))
