"""Create a standalone snapshot of the local TimeDay database."""

import errno
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

from .i18n import tr


BUSY_CODES = (getattr(sqlite3, "SQLITE_BUSY", 5), getattr(sqlite3, "SQLITE_LOCKED", 6))


def publish_copy(staged, destination):
    try:
        # Link the verified file into place in one step without replacing an existing file.
        os.link(staged, destination)
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS):
            raise
        # Filesystems such as FAT lack hard links. Reserve the new name exclusively.
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        try:
            staged.replace(destination)
        except OSError:
            destination.unlink()
            raise


def backup_database(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).expanduser().absolute()
    target = destination.resolve()
    reserved = {source, *(Path(f"{source}{suffix}") for suffix in ("-wal", "-shm", "-journal"))}
    if target in reserved:
        raise ValueError(tr('Выберите отдельный файл для резервной копии.'))
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(tr('Такой файл уже есть. Укажите новое имя резервной копии.'))

    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".timeday-backup-") as directory:
        staged = Path(directory) / "backup.sqlite3"
        deadline = time.monotonic() + 5

        def check_lock(status, *_):
            if status in BUSY_CODES and time.monotonic() >= deadline:
                raise sqlite3.OperationalError(tr('База TimeDay занята. Повторите попытку позже.'))

        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(staged)) as copy:
                original.backup(copy, progress=check_lock)
                copy.execute("PRAGMA journal_mode = DELETE").fetchone()
                if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise sqlite3.DatabaseError(tr('Резервная копия не прошла проверку целостности.'))
                tasks = copy.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'")
                if tasks.fetchone() is None:
                    raise sqlite3.DatabaseError(tr('Файл не содержит базу TimeDay.'))
        staged.chmod(0o600)
        publish_copy(staged, destination)
