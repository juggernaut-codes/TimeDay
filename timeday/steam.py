"""Resolve local Steam game metadata without network access."""

import os
import re
import time
import json
from pathlib import Path

from .steam_appinfo import read_appinfo
from .desktop import write_atomic


STEAM_WINDOW = re.compile(r"steam_app_(\d+)(?:\.desktop)?$", re.IGNORECASE)
STEAM_COMMAND = re.compile(r"steam://(?:rungameid|run)/(\d+)|-applaunch\s+(\d+)", re.IGNORECASE)
STEAM_HELPERS = frozenset({
    "wineserver", "services.exe", "winedevice.exe", "svchost.exe", "explorer.exe", "rpcss.exe",
    "plugplay.exe", "steam.exe", "steamservice.exe", "pressure-vessel", "pv-bwrap",
})


def read_vdf(path):
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    tokens = re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', value)
    root, stack, key = {}, [], None
    current = root
    for quoted, brace in tokens:
        if brace == "{":
            if key is None:
                return {}
            child = {}
            current[key] = child
            stack.append(current)
            current, key = child, None
        elif brace == "}":
            if not stack:
                return {}
            current, key = stack.pop(), None
        else:
            text = quoted.replace(r'\"', '"').replace(r"\\", "\\")
            if key is None:
                key = text
            else:
                current[key] = text
                key = None
    return root if not stack else {}


class SteamLibrary:
    def __init__(self, roots=None):
        home = Path.home()
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
        candidates = roots if roots is not None else (
            home / ".steam/steam", home / ".steam/root", home / ".steam/debian-installation",
            data / "Steam", home / ".var/app/com.valvesoftware.Steam/data/Steam",
        )
        self.roots = tuple(dict.fromkeys(Path(path).resolve() for path in candidates if Path(path).is_dir()))
        self.name_cache = None
        self.learned_names = {}
        if roots is None:
            cache = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
            self.name_cache = cache / "timeday/steam_names.json"
            try:
                names = json.loads(self.name_cache.read_text(encoding="utf-8"))
                if isinstance(names, dict):
                    self.learned_names = {key: value for key, value in names.items()
                                          if key.isdecimal() and isinstance(value, str) and value.strip()}
            except (OSError, ValueError):
                pass
        self.games = dict(self.learned_names)
        self.game_ids = {}
        self.install_dirs = {}
        self.catalog = {}
        self.catalog_versions = {}
        self.next_refresh = 0
        self.refresh()

    def refresh(self):
        if time.monotonic() < self.next_refresh:
            return
        self.next_refresh = time.monotonic() + 15
        games, install_dirs = dict(self.games), dict(self.install_dirs)
        libraries = set(self.roots)
        for root in self.roots:
            appinfo = root / "appcache/appinfo.vdf"
            try:
                state = appinfo.stat()
                version = state.st_mtime_ns, state.st_size
                if self.catalog_versions.get(appinfo) != version:
                    self.catalog.update(read_appinfo(appinfo))
                    self.catalog_versions[appinfo] = version
            except OSError:
                pass
            folders = read_vdf(root / "steamapps/libraryfolders.vdf").get("libraryfolders", {})
            if not isinstance(folders, dict):
                continue
            for index, folder in folders.items():
                path = folder.get("path") if isinstance(folder, dict) else folder
                if index.isdecimal() and isinstance(path, str):
                    libraries.add(Path(path))
        for appid, fields in self.catalog.items():
            games[appid] = fields["name"]
        for library in libraries:
            for manifest in (library / "steamapps").glob("appmanifest_*.acf"):
                state = read_vdf(manifest).get("AppState", {})
                if not isinstance(state, dict):
                    continue
                appid, title, directory = state.get("appid"), state.get("name"), state.get("installdir")
                if isinstance(appid, str) and appid.isdecimal() and isinstance(title, str) and title:
                    games[appid] = title
                    if isinstance(directory, str) and directory:
                        install_dirs[(library / "steamapps/common" / directory).resolve()] = appid
        self.games, self.install_dirs = games, install_dirs
        self.game_ids = {title.casefold(): appid for appid, title in games.items()}

    def register_shortcut(self, command, title):
        match = STEAM_COMMAND.search(command or "")
        if match:
            appid = match.group(1) or match.group(2)
            self.games[appid] = title
            self.game_ids[title.casefold()] = appid
            return appid
        return None

    def resolve(self, name):
        match = STEAM_WINDOW.fullmatch(name)
        if match:
            self.refresh()
            return self.games.get(match.group(1), name)
        return name

    def window_name(self, identity, title):
        resolved = self.resolve(identity)
        if resolved == identity and isinstance(title, str) and title.strip():
            match = STEAM_WINDOW.fullmatch(identity)
            if match:
                resolved = title.strip()
                self.games[match.group(1)] = resolved
                self.game_ids[resolved.casefold()] = match.group(1)
                self.learned_names[match.group(1)] = resolved
                if self.name_cache is not None:
                    try:
                        content = json.dumps(self.learned_names, ensure_ascii=False).encode("utf-8")
                        write_atomic(self.name_cache, content, 0o600)
                    except OSError:
                        pass
        return resolved

    def process_name(self, directory, executable=None, comm=""):
        if comm in STEAM_HELPERS:
            return None
        try:
            environment = (directory / "environ").read_bytes().split(b"\0")
        except OSError:
            environment = ()
        for item in environment:
            key, _, value = item.partition(b"=")
            if key in (b"SteamAppId", b"SteamGameId") and value.isdigit() and value != b"0":
                appid = value.decode("ascii")
                self.refresh()
                if appid in self.games:
                    return self.games[appid]
        if executable:
            path = Path(executable)
            for installed, appid in self.install_dirs.items():
                if path.is_relative_to(installed):
                    return self.games[appid]
        return None

    def icon_file(self, appid):
        for root in self.roots:
            cache = root / "appcache/librarycache"
            candidates = [cache / f"{appid}_icon.jpg", cache / f"{appid}_icon.png"]
            fields = self.catalog.get(appid, {})
            for key in ("icon", "clienticon"):
                icon_hash = fields.get(key, "")
                if re.fullmatch(r"[0-9a-fA-F]{40}", icon_hash):
                    candidates.extend((cache / appid / f"{icon_hash}.jpg", root / "steam/games" / f"{icon_hash}.ico"))
            folder = cache / appid
            if folder.is_dir():
                candidates.extend(folder.glob("*_icon.*"))
                candidates.extend((folder / "icon.jpg", folder / "icon.png"))
                candidates.extend(path for path in folder.glob("*.jpg") if re.fullmatch(r"[0-9a-f]{40}", path.stem))
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
        return None

    def client_icon(self):
        for root in self.roots:
            for relative in ("appcache/steam_icon.png", "deb-installer/icons/256/steam.png",
                             "deb-installer/steam-launcher/icons/256/steam.png"):
                candidate = root / relative
                if candidate.is_file():
                    return candidate
        return None
