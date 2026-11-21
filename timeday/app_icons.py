"""Resolve tracked X11 application names to the real desktop-entry icons."""

import hashlib
import os
import weakref
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from . import APP_ID
from .runtime import is_runtime_app
from .steam import SteamLibrary
from .desktop import write_atomic


ALIASES = {
    "brave-browser": "Brave", "com.brave.browser": "Brave", "discord": "Discord",
    "com.discordapp.discord": "Discord", "firefox": "Firefox", "org.mozilla.firefox": "Firefox",
    "org.chromium.chromium": "Chromium", "resolve": "DaVinci Resolve",
    "davinci-resolve": "DaVinci Resolve", "com.blackmagicdesign.resolve": "DaVinci Resolve",
    APP_ID.casefold(): "TimeDay", "timeday": "TimeDay",
    "steam": "Steam", "steamwebhelper": "Steam", "com.valvesoftware.steam": "Steam",
}
PROCESS_ALIASES = {
    "brave": "Brave", "brave-browser": "Brave", "discord": "Discord",
    "firefox": "Firefox", "firefox-bin": "Firefox", "chromium": "Chromium",
    "resolve": "DaVinci Resolve", "spotify": "Spotify",
    "steam": "Steam", "steamwebhelper": "Steam",
}
GENERIC_EXECUTABLES = {
    "bash", "env", "flatpak", "java", "mono", "node", "perl", "python", "python3", "sh", "wine",
    "wine64", "xdg-open", "electron", "gapplication", "gtk-launch", "snap",
}

ICON_NAMES = {
    "Brave": ("brave-browser", "com.brave.Browser"),
    "Discord": ("discord", "com.discordapp.Discord"),
    "Firefox": ("firefox", "org.mozilla.firefox"),
    "Chromium": ("chromium", "org.chromium.Chromium"),
    "DaVinci Resolve": ("DaVinciResolve", "davinci-resolve", "com.blackmagicdesign.resolve"),
    "TimeDay": (APP_ID, "preferences-system-time"),
    "Steam": ("steam", "com.valvesoftware.Steam"),
}


class AppIcons:
    def __init__(self, display):
        self.theme = Gtk.IconTheme.get_for_display(display)
        self.steam = SteamLibrary()
        self.names = ALIASES.copy()
        self.icons = {}
        self.window_icons = {}
        self.images = weakref.WeakSet()
        self.cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "timeday/icons"
        self.executables = PROCESS_ALIASES.copy()
        runtime_entries = set()
        hidden_entries = set()
        for entry in Gio.AppInfo.get_all():
            if not isinstance(entry, Gio.DesktopAppInfo):
                continue
            name = entry.get_display_name()
            gicon = entry.get_icon()
            executable = Path(entry.get_executable() or "").name
            desktop_id = (entry.get_id() or "").removesuffix(".desktop")
            window_class = entry.get_startup_wm_class()
            flatpak_id = entry.get_string("X-Flatpak")
            game_id = self.steam.register_shortcut(entry.get_commandline(), name)
            canonical = name if game_id else next(
                (self.names[key.casefold()] for key in (desktop_id, window_class, executable)
                 if key and key.casefold() in ALIASES),
                name,
            )
            categories = set((entry.get_categories() or "").split(";"))
            visible_app = (
                entry.should_show()
                and not categories.intersection({"Settings", "Screensaver"})
                and all(is_runtime_app(key) for key in (desktop_id, executable, canonical))
            )
            if (visible_app and executable and executable.casefold() not in GENERIC_EXECUTABLES
                    and not executable.casefold().startswith(("python3.", "electron", "wine-"))):
                self.executables.setdefault(executable.casefold(), canonical)
            keys = (desktop_id, window_class, name, entry.get_name(), flatpak_id)
            entries = runtime_entries if visible_app else hidden_entries
            entries.update(key.casefold() for key in (*keys, canonical) if key)
            if executable.casefold() in self.executables:
                keys += (executable,)
            for key in filter(None, keys):
                registered = self.names.setdefault(key.casefold(), canonical)
                if gicon is not None:
                    self.icons.setdefault(key.casefold(), gicon)
                    self.icons.setdefault(registered.casefold(), gicon)
            if game_id:
                self.names[f"steam_app_{game_id}"] = name
                if gicon is not None:
                    self.icons[name.casefold()] = gicon
        self.runtime_excluded = frozenset(hidden_entries - runtime_entries)
        for appid, title in self.steam.games.items():
            self.names[f"steam_app_{appid}"] = title

    def canonical_name(self, name):
        resolved = self.steam.resolve(name)
        return resolved if resolved != name else self.names.get(name.casefold(), name)

    def window_name(self, name, title):
        return self.steam.window_name(name, title)

    def samples(self, rows):
        from .presence import is_self_app
        return [(self.canonical_name(app), start, end) for app, start, end in rows
                if not is_self_app(app) and not is_self_app(self.canonical_name(app))]

    def cached_icon(self, name):
        digest = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()
        return self.cache / f"{digest}.png"

    def accept_window_icon(self, name, image):
        width, height, pixels = image
        rgba = bytearray(width * height * 4)
        for index, pixel in enumerate(pixels):
            rgba[index * 4:index * 4 + 4] = bytes((
                (pixel >> 16) & 255, (pixel >> 8) & 255, pixel & 255, (pixel >> 24) & 255,
            ))
        if not any(rgba[3::4]):
            return False
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB, True, 8, width, height, width * 4,
            )
            success, png = pixbuf.save_to_bufferv("png", [], [])
            if not success:
                return False
            name = self.canonical_name(name)
            self.window_icons[name.casefold()] = Gio.BytesIcon.new(GLib.Bytes.new(png))
            try:
                write_atomic(self.cached_icon(name), png, 0o600)
            except OSError:
                pass
            for image in list(self.images):
                if self.canonical_name(image._app_name) == name:
                    image.set_from_gicon(self.lookup(name))
            return True
        except GLib.Error:
            return False

    def lookup(self, name):
        name = self.canonical_name(name)
        gicon = self.icons.get(name.casefold())
        if isinstance(gicon, Gio.FileIcon) and gicon.get_file().query_exists(None):
            return gicon
        if isinstance(gicon, Gio.ThemedIcon) and any(self.theme.has_icon(key) for key in gicon.get_names()):
            return gicon
        canonical = self.names.get(name.casefold(), name)
        for key in ICON_NAMES.get(canonical, (name, name.casefold())):
            if self.theme.has_icon(key):
                return Gio.ThemedIcon.new(key)
        appid = self.steam.game_ids.get(name.casefold())
        if appid:
            icon_name = f"steam_icon_{appid}"
            if self.theme.has_icon(icon_name):
                return Gio.ThemedIcon.new(icon_name)
            filename = self.steam.icon_file(appid)
            if filename:
                return Gio.FileIcon.new(Gio.File.new_for_path(str(filename)))
        if name == "Steam":
            filename = self.steam.client_icon()
            if filename:
                return Gio.FileIcon.new(Gio.File.new_for_path(str(filename)))
        if name.casefold() in self.window_icons:
            return self.window_icons[name.casefold()]
        cached = self.cached_icon(name)
        if cached.is_file():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(cached)))
        return Gio.ThemedIcon.new("application-x-executable-symbolic")

    def image(self, name, size=22):
        widget = Gtk.Image()
        self.bind_image(widget, name)
        widget.set_pixel_size(size)
        widget.set_valign(Gtk.Align.CENTER)
        return widget

    def bind_image(self, widget, name):
        widget._app_name = name
        self.images.add(widget)
        widget.set_from_gicon(self.lookup(name))
