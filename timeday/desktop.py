"""Desktop launchers and per-user startup integration."""

import os
import sys
import tempfile
from pathlib import Path

from .i18n import tr
from . import APP_ICON, APP_ID


ENTRY_NAME = f"{APP_ID}.desktop"
MANAGED = "X-TimeDay-Managed=true"


def exec_argument(path):
    # Desktop entry values are unescaped once before Exec arguments are parsed.
    escaped = "".join(f"\\{char}" if char in '\\"$`' else char for char in str(path))
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


def write_atomic(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".timeday-") as directory:
        staged = Path(directory) / path.name
        staged.write_bytes(content)
        staged.chmod(mode)
        staged.replace(path)


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def desktop_directory():
    # Parse only the XDG desktop path, without executing the configuration as shell code.
    config = config_home() / "user-dirs.dirs"
    if config.exists():
        for line in config.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == "XDG_DESKTOP_DIR":
                value = value.strip()
                if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
                    break
                value = value[1:-1]
                for escaped, literal in ((r"\$", "$"), (r'\"', '"'), (r"\`", "`"), (r"\\", "\\")):
                    value = value.replace(escaped, literal)
                if value == "$HOME" or value.startswith("$HOME/"):
                    value = str(Path.home()) + value[5:]
                directory = Path(value)
                if directory.is_absolute():
                    return directory
                break
    return Path.home() / "Desktop"


def application_entry():
    entry = data_home() / "applications" / ENTRY_NAME
    if entry.is_file():
        return entry
    launcher = Path(__file__).resolve().parent.parent / "launch.py"
    if not launcher.is_file():
        raise FileNotFoundError(tr('Не найден файл запуска TimeDay. Повторите установку приложения.'))
    invocation = " ".join(exec_argument(path) for path in (Path(sys.executable), launcher))
    content = (
        "[Desktop Entry]\nType=Application\nName=TimeDay\n"
        f"Exec={invocation}\nIcon={APP_ICON}\nTerminal=false\n"
        f"Categories=Utility;\nStartupWMClass={APP_ID}\n{MANAGED}\n"
    )
    write_atomic(entry, content.encode())
    return entry


def integration_paths():
    return config_home() / "autostart" / ENTRY_NAME, desktop_directory() / ENTRY_NAME


def background_application_entry(entry=None):
    entry = entry or application_entry()
    # Read only the main group, never an Exec belonging to a desktop action.
    main_group = entry.read_text(encoding="utf-8").split("[Desktop Entry]", 1)[-1].split("\n[", 1)[0]
    invocation = next((line[5:] for line in main_group.splitlines() if line.startswith("Exec=")), None)
    if not invocation:
        raise ValueError(tr('Не найден файл запуска TimeDay. Повторите установку приложения.'))
    background = entry.with_name(f"{APP_ID}.background.desktop")
    content = (
        "[Desktop Entry]\nType=Application\nName=TimeDay\n"
        f"Exec={invocation} --background\nIcon={APP_ICON}\n"
        f"Terminal=false\nNoDisplay=true\nStartupNotify=false\n{MANAGED}\n"
    )
    write_atomic(background, content.encode())
    return background


def autostart_content(entry=None):
    background = background_application_entry(entry).resolve()
    return (
        "[Desktop Entry]\nType=Application\nName=TimeDay\n"
        f"Exec=/usr/bin/gio launch {exec_argument(background)}\nIcon={APP_ICON}\n"
        f"Terminal=false\nStartupNotify=false\n{MANAGED}\n"
    ).encode()


def migrate_autostart(entry=None):
    path = config_home() / "autostart" / ENTRY_NAME
    if (autostart_enabled() and not path.is_symlink()
            and MANAGED in path.read_text(encoding="utf-8")):
        content = autostart_content(entry)
        if path.read_bytes() != content:
            write_atomic(path, content)


def autostart_enabled():
    path = config_home() / "autostart" / ENTRY_NAME
    return path.is_file() and "Hidden=true" not in path.read_text(encoding="utf-8")


def set_autostart(enabled):
    path = config_home() / "autostart" / ENTRY_NAME
    if not enabled:
        if path.is_file():
            write_atomic(path, f"[Desktop Entry]\nType=Application\nName=TimeDay\nHidden=true\n{MANAGED}\n".encode())
        return
    write_atomic(path, autostart_content())


def apply_integrations(autostart, shortcut):
    startup, desktop = integration_paths()
    selected = [(startup, autostart, 0o644), (desktop, shortcut, 0o755)]
    for path, enabled, _mode in selected:
        if enabled and (path.exists() or path.is_symlink()):
            if path.is_symlink() or MANAGED not in path.read_text(encoding="utf-8"):
                raise FileExistsError(tr('Файл {0} уже существует. Выберите другое имя для этого файла.', path))
    content = None
    if autostart or shortcut:
        entry = application_entry().resolve()
        content = (
            "[Desktop Entry]\nType=Application\nName=TimeDay\n"
            f"Exec=/usr/bin/gio launch {exec_argument(entry)}\nIcon={APP_ICON}\n"
            f"Terminal=false\nStartupNotify=true\n{MANAGED}\n"
        ).encode()
    for path, enabled, mode in selected:
        if enabled:
            write_atomic(path, autostart_content() if path == startup else content, mode)
        elif path.is_file() and not path.is_symlink() and MANAGED in path.read_text(encoding="utf-8"):
            path.unlink()
    return desktop if shortcut else None
