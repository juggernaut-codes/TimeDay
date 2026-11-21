import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from timeday.desktop import (
    ENTRY_NAME, MANAGED, apply_integrations, autostart_enabled, desktop_directory, integration_paths,
    migrate_autostart, set_autostart,
)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.config = self.root / "config"
        self.data = self.root / "data % с пробелом"
        self.config.mkdir()
        self.desktop = self.root / "Рабочий стол"
        (self.config / "user-dirs.dirs").write_text(
            f'XDG_DESKTOP_DIR="{self.desktop}"\n', encoding="utf-8",
        )
        environment = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config), "XDG_DATA_HOME": str(self.data)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_selected_integrations_follow_the_canonical_launcher(self):
        self.assertEqual(desktop_directory(), self.desktop)
        self.assertEqual(apply_integrations(True, True), self.desktop / ENTRY_NAME)
        startup, shortcut = integration_paths()
        self.assertTrue(startup.is_file())
        self.assertTrue(shortcut.is_file())
        self.assertEqual(shortcut.stat().st_mode & 0o777, 0o755)
        self.assertIn("/usr/bin/gio launch", startup.read_text())
        self.assertIn("%%", startup.read_text())
        self.assertIn(str(self.data / "applications").replace("%", "%%"), shortcut.read_text())
        self.assertIn(MANAGED, shortcut.read_text())
        background = self.data / "applications" / ENTRY_NAME.replace(".desktop", ".background.desktop")
        self.assertIn(".background.desktop", startup.read_text())
        self.assertNotIn(".background.desktop", shortcut.read_text())
        self.assertIn(" --background\n", background.read_text())
        self.assertIn("NoDisplay=true", background.read_text())
        self.assertNotIn("--background", (self.data / "applications" / ENTRY_NAME).read_text())
        # Updating the canonical entry leaves both integration files valid.
        entry = self.data / "applications" / ENTRY_NAME
        entry.write_text("[Desktop Entry]\nType=Application\nName=TimeDay\nExec=/bin/true\n")
        before = startup.read_bytes()
        apply_integrations(True, True)
        self.assertEqual(startup.read_bytes(), before)

    def test_deselected_options_remove_only_managed_files(self):
        apply_integrations(True, True)
        startup, shortcut = integration_paths()
        self.assertIsNone(apply_integrations(False, False))
        self.assertFalse(startup.exists())
        self.assertFalse(shortcut.exists())
        shortcut.write_text("user-owned launcher")
        apply_integrations(False, False)
        self.assertEqual(shortcut.read_text(), "user-owned launcher")

    def test_conflict_is_reported_before_creating_integrations(self):
        startup, shortcut = integration_paths()
        shortcut.parent.mkdir()
        shortcut.write_text("user-owned launcher")
        with self.assertRaises(FileExistsError):
            apply_integrations(True, True)
        self.assertFalse(startup.exists())
        self.assertEqual(shortcut.read_text(), "user-owned launcher")

    def test_desktop_path_expands_only_home(self):
        (self.config / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Рабочий стол"\n')
        with patch("timeday.desktop.Path.home", return_value=self.root):
            self.assertEqual(desktop_directory(), self.root / "Рабочий стол")

    def test_autostart_switch_does_not_create_or_remove_desktop_shortcut(self):
        apply_integrations(False, True)
        _startup, shortcut = integration_paths()
        original = shortcut.read_bytes()
        self.assertFalse(autostart_enabled())
        set_autostart(True)
        self.assertTrue(autostart_enabled())
        set_autostart(False)
        self.assertFalse(autostart_enabled())
        self.assertEqual(shortcut.read_bytes(), original)

    def test_migration_updates_managed_autostart_and_follows_new_release(self):
        apply_integrations(True, True)
        startup, shortcut = integration_paths()
        original_shortcut = shortcut.read_bytes()
        startup.write_text(f"[Desktop Entry]\nExec=old-command\n{MANAGED}\n")
        entry = self.data / "applications" / ENTRY_NAME
        entry.write_text('[Desktop Entry]\nExec="/new release/python" "/new release/launch.py"\n')
        migrate_autostart()
        background = entry.with_name(ENTRY_NAME.replace(".desktop", ".background.desktop"))
        self.assertIn('Exec="/new release/python" "/new release/launch.py" --background\n', background.read_text())
        self.assertIn(".background.desktop", startup.read_text())
        self.assertEqual(shortcut.read_bytes(), original_shortcut)

    def test_migration_preserves_disabled_and_user_owned_autostart(self):
        startup, _shortcut = integration_paths()
        startup.parent.mkdir(parents=True)
        for content in (f"[Desktop Entry]\nHidden=true\n{MANAGED}\n", "[Desktop Entry]\nExec=my-command\n"):
            startup.write_text(content)
            migrate_autostart()
            self.assertEqual(startup.read_text(), content)
