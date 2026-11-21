"""Entry point for the desktop application and local database export."""

import argparse
import sqlite3
import sys

from .i18n import set_language, tr
from . import APP_VERSION
from .backup import backup_database
from .store import database_path


def main():
    path = database_path()
    if path.is_file():
        try:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                row = connection.execute("SELECT value FROM settings WHERE key = 'language'").fetchone()
                set_language(row[0] if row else "ru")
            finally:
                connection.close()
        except sqlite3.Error:
            pass
    parser = argparse.ArgumentParser(prog="timeday", description=tr('Планировщик TimeDay'))
    parser.add_argument("--version", action="version", version=f"TimeDay {APP_VERSION}")
    parser.add_argument("--backup", metavar=tr('ФАЙЛ'), help=tr('сохранить резервную копию базы TimeDay'))
    parser.add_argument("--background", action="store_true", help=tr('запустить в системном трее'))
    options = parser.parse_args()
    if options.backup is not None:
        try:
            backup_database(database_path(), options.backup)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(tr('Не удалось сохранить резервную копию: {0}', exc), file=sys.stderr)
            return 1
        print(tr('Резервная копия сохранена: {0}', options.backup))
        return 0

    from .app import main as run_desktop

    return run_desktop(background=options.background)
