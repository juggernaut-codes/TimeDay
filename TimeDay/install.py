"""Install TimeDay and its launcher for the current Linux user."""

import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from timeday import APP_ICON, APP_ID
from timeday.desktop import exec_argument, migrate_autostart, write_atomic

ROOT = Path(__file__).resolve().parent
LAUNCHER = "from timeday.cli import main\n\nraise SystemExit(main())\n"
CHECK_GTK = """
import sys
if sys.version_info < (3, 10):
    raise SystemExit(1)
import gi
gi.require_version('Gtk', '4.0')
gi.require_foreign('cairo')
"""


def desktop_python():
    for candidate in dict.fromkeys((Path("/usr/bin/python3"), Path(sys.executable))):
        try:
            probe = subprocess.run([candidate, "-c", CHECK_GTK], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return candidate
    raise RuntimeError("Нужен Python 3.10+ с GTK 4 и Cairo. Установите зависимости из README.md.")


def build_release(releases):
    releases.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=releases, prefix=".install-") as directory:
        staged = Path(directory) / "program"
        shutil.copytree(ROOT / "timeday", staged / "timeday", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (staged / "data").mkdir()
        shutil.copyfile(APP_ICON, staged / "data" / APP_ICON.name)
        shutil.copyfile(ROOT / "LICENSE", staged / "LICENSE")
        (staged / "launch.py").write_text(LAUNCHER, encoding="utf-8")
        digest = hashlib.sha256()
        for path in sorted(staged.rglob("*")):
            if path.is_file():
                digest.update(os.fsencode(path.relative_to(staged)) + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        release = releases / digest.hexdigest()
        if not release.exists():
            staged.rename(release)
    return release


def install(data_home, python):
    app_data = data_home / "timeday"
    app_data.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (app_data / ".install.lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entry_name = f"{APP_ID}.desktop"
        entry = (ROOT / "data" / entry_name).read_text(encoding="utf-8")
        release = build_release(app_data / "releases")
        icons = data_home / "icons" / "hicolor" / "128x128" / "apps"
        write_atomic(icons / APP_ICON.name, (release / "data" / APP_ICON.name).read_bytes())
        invocation = " ".join(exec_argument(path) for path in (python, release / "launch.py"))
        # An existing launcher keeps pointing at a complete release until this rename.
        write_atomic(data_home / "applications" / entry_name,
                     entry.replace("Exec=timeday", f"Exec={invocation}").encode())
        migrate_autostart(data_home / "applications" / entry_name)


def main():
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share").resolve()
    try:
        install(data_home, desktop_python())
    except (OSError, RuntimeError) as exc:
        print(f"Не удалось установить TimeDay: {exc}", file=sys.stderr)
        return 1
    print("TimeDay установлен. Откройте его из меню приложений.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
