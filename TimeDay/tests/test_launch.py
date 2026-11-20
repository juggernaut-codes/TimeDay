import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import GLib

from timeday.app import TimeDay


class LaunchTests(unittest.TestCase):
    def command(self, background):
        options = GLib.VariantDict.new(None)
        if background:
            options.insert_value("background", GLib.Variant("b", True))
        return SimpleNamespace(get_options_dict=lambda: options)

    def app(self, completed=True):
        settings = {"onboarding_completed": "1" if completed else "0", "usage_purpose": "focus"}
        return SimpleNamespace(startup_failed=False, window=None, launch_screen=None,
                               settings=lambda key, default: settings.get(key, default), activate=Mock())

    def test_background_starts_services_without_presenting_window(self):
        app = self.app()
        with patch("timeday.app.TimeDayWindow") as window:
            self.assertEqual(TimeDay.do_command_line(app, self.command(True)), 0)
            window.assert_called_once_with(app)
            window.return_value.navigate.assert_called_once()
            window.return_value.present.assert_not_called()
        app.activate.assert_not_called()

    def test_manual_launch_activates_existing_background_instance(self):
        app = self.app()
        app.window = Mock()
        self.assertEqual(TimeDay.do_command_line(app, self.command(False)), 0)
        app.activate.assert_called_once_with()

    def test_repeated_background_launch_preserves_existing_window(self):
        app = self.app()
        app.window = Mock()
        with patch("timeday.app.TimeDayWindow") as window:
            TimeDay.do_command_line(app, self.command(True))
            window.assert_not_called()
        app.window.present.assert_not_called()
        app.window.set_visible.assert_not_called()
        app.activate.assert_not_called()

    def test_background_waits_for_first_run_setup(self):
        app = self.app(completed=False)
        with patch("timeday.app.TimeDayWindow") as window:
            TimeDay.do_command_line(app, self.command(True))
            window.assert_not_called()
        app.activate.assert_not_called()

    def test_background_does_not_interrupt_welcome(self):
        app = self.app()
        app.launch_screen = Mock()
        with patch("timeday.app.TimeDayWindow") as window:
            TimeDay.do_command_line(app, self.command(True))
            window.assert_not_called()
        app.launch_screen.present.assert_not_called()

    def test_failed_startup_reports_failure(self):
        app = self.app()
        app.startup_failed = True
        self.assertEqual(TimeDay.do_command_line(app, self.command(True)), 1)
        app.activate.assert_not_called()
