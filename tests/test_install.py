import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        desktop = Path(config.name) / 'Desktop'
        desktop.mkdir()
        (Path(config.name) / 'user-dirs.dirs').write_text(f'XDG_DESKTOP_DIR="{desktop}"\n')
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_update_migrates_old_desktop_copy_to_stable_launcher(self):
        from timeday.desktop import ENTRY_NAME, desktop_directory
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            python = Path(sys.executable)
            install.install(data, python)
            shortcut = desktop_directory() / ENTRY_NAME
            shortcut.write_bytes((data / 'applications' / ENTRY_NAME).read_bytes())
            shortcut.chmod(0o755)
            with patch.object(install, 'LAUNCHER', install.LAUNCHER + '# update\n'):
                install.install(data, python)
            self.assertIn('/usr/bin/gio launch', shortcut.read_text())
            self.assertNotIn('/releases/', shortcut.read_text())
            self.assertEqual(shortcut.stat().st_mode & 0o777, 0o755)
            stable = shortcut.read_bytes()
            install.install(data, python)
            self.assertEqual(shortcut.read_bytes(), stable)
            shortcut.write_text('custom launcher')
            install.install(data, python)
            self.assertEqual(shortcut.read_text(), 'custom launcher')

    def test_update_migrates_enabled_autostart_to_background(self):
        from timeday.desktop import ENTRY_NAME, MANAGED, config_home

        startup = config_home() / "autostart" / ENTRY_NAME
        startup.parent.mkdir()
        startup.write_text(f"[Desktop Entry]\nExec=old-command\n{MANAGED}\n")
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            install.install(data, Path(sys.executable))
            self.assertIn(".background.desktop", startup.read_text())
            background = data / "applications" / ENTRY_NAME.replace(".desktop", ".background.desktop")
            self.assertIn("--background", background.read_text())
            self.assertIn("/releases/", background.read_text())

    def test_reinstall_reuses_release_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            install.install(data, Path(sys.executable))
            app_data = data / "timeday"
            database = app_data / "timeday.sqlite3"
            database.write_bytes(b"existing data")
            install.install(data, Path(sys.executable))
            [release] = (app_data / "releases").iterdir()
            entry = (data / "applications" / f"{install.APP_ID}.desktop").read_text(encoding="utf-8")
            self.assertIn(install.exec_argument(release / "launch.py"), entry)
            self.assertTrue((release / "timeday" / "style.css").is_file())
            self.assertTrue((release / "data" / install.APP_ICON.name).is_file())
            self.assertEqual((release / "LICENSE").read_bytes(), (install.ROOT / "LICENSE").read_bytes())
            self.assertEqual(database.read_bytes(), b"existing data")

    def test_failed_update_leaves_previous_launcher_working(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            python = Path(sys.executable)
            install.install(data, python)
            [previous] = (data / "timeday" / "releases").iterdir()
            entry = data / "applications" / f"{install.APP_ID}.desktop"
            existing_entry = entry.read_bytes()
            atomic_write = install.write_atomic

            def fail_publishing_entry(path, content):
                if path == entry:
                    raise OSError("test write failure")
                atomic_write(path, content)

            with patch.object(install, "LAUNCHER", install.LAUNCHER + "\n# updated version\n"):
                with patch.object(install, "write_atomic", side_effect=fail_publishing_entry):
                    with self.assertRaisesRegex(OSError, "test write failure"):
                        install.install(data, python)
                self.assertEqual(entry.read_bytes(), existing_entry)
                self.assertTrue((previous / "launch.py").is_file())
                install.install(data, python)
            self.assertNotEqual(entry.read_bytes(), existing_entry)
