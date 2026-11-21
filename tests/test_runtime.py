import os
import tempfile
import unittest
from pathlib import Path

from timeday.runtime import ProcessApps, RuntimeRecorder, RuntimeSample, is_runtime_app
from timeday.store import Store


class RuntimeTests(unittest.TestCase):
    def test_windowless_and_duplicate_processes_are_identified_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def add(pid, name, state="S", started="100"):
                process = root / str(pid)
                process.mkdir(exist_ok=True)
                fields = [state] + ["0"] * 18 + [started]
                (process / "stat").write_text(f"{pid} ({name}) {' '.join(fields)}")
                return process

            scanner = ProcessApps({"discord": "Discord"}, {}, proc=root)
            add(10, "discord")
            add(11, "discord")
            add(12, "python3")
            add(13, "discord", state="Z")
            binary = add(14, "worker")
            os.symlink("/opt/Discord/discord", binary / "exe")
            add(15, "frescod")
            add(16, "desktop-helper")
            self.assertEqual(scanner.snapshot({12: "Window app"}).apps, {"Discord", "Window app"})
            self.assertEqual(scanner.snapshot({15: "Fresco", 16: "Portal"}).apps, {"Discord", "Window app"})
            self.assertEqual(scanner.snapshot({}).apps, {"Discord", "Window app"})
            add(12, "python3", started="200")
            self.assertEqual(scanner.snapshot({}).apps, {"Discord"})

    def test_system_names_and_desktop_aliases_are_excluded(self):
        for name in ("Cinnamon (Wayland session)", "Csd-automount", "Nemo", "Files",
                     "org.freedesktop.IBus.Panel.Emojier.desktop", "Portal", "Fresco",
                     "Update Manager", "Geoclue Demo agent", "Local Desktop Helper",
                     "TimeDay", "io.github.timeday.TimeDay.desktop"):
            self.assertFalse(is_runtime_app(name, {"local desktop helper"}), name)
        for name in ("Brave", "Discord", "Image Viewer", "Terminal"):
            self.assertTrue(is_runtime_app(name), name)

    def test_simultaneous_intervals_are_stored_separately_and_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.sqlite3"
            store = Store(path)
            recorder = RuntimeRecorder(store)
            for wall, steady, apps in [
                (100, 10, {"Discord"}),
                (102, 12, {"Discord", "Brave"}),
                (104, 14, {"Discord", "Brave"}),
                (106, 16, {"Brave"}),
                (500, 18, {"Brave"}),  # suspend / wall-clock discontinuity
                (502, 20, {"Brave"}),
            ]:
                recorder.observe(RuntimeSample(wall, steady, frozenset(apps)))
            store.record_activity("Discord", 101, 102)
            store.close()
            reopened = Store(path)
            try:
                self.assertCountEqual([tuple(row) for row in reopened.runtime(100, 600)], [
                    ("Discord", 100, 105), ("Brave", 101, 106), ("Brave", 500, 502),
                ])
                self.assertEqual([tuple(row) for row in reopened.runtime(102, 104)], [
                    ("Discord", 102, 104), ("Brave", 102, 104),
                ])
                self.assertEqual([tuple(row) for row in reopened.activity(100, 600)], [("Discord", 101, 102)])
            finally:
                reopened.close()
