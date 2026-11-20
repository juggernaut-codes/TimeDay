import tempfile
import unittest
import struct
from pathlib import Path

from timeday.runtime import ProcessApps
from timeday.steam import SteamLibrary, read_vdf
from timeday.steam_appinfo import read_appinfo
from timeday.activity import window_icon


class SteamTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.client = self.root / "Steam"
        self.library = self.root / "Other library"
        (self.client / "steamapps").mkdir(parents=True)
        (self.library / "steamapps").mkdir(parents=True)
        (self.client / "steamapps/libraryfolders.vdf").write_text(
            '"libraryfolders" { "1" { "path" "' + str(self.library) + '" } }',
        )
        (self.library / "steamapps/appmanifest_730.acf").write_text(
            '"AppState" { "appid" "730" "name" "Counter-Strike 2" "installdir" "CS 2" }',
        )
        self.steam = SteamLibrary([self.client])

    def test_external_library_and_window_aliases(self):
        self.assertEqual(self.steam.resolve("Steam_app_730"), "Counter-Strike 2")
        self.assertEqual(self.steam.resolve("steam_app_730.desktop"), "Counter-Strike 2")
        self.assertEqual(self.steam.resolve("Steam_app_999"), "Steam_app_999")
        self.assertEqual(self.steam.register_shortcut("steam steam://rungameid/42", "My game"), "42")
        self.assertEqual(self.steam.resolve("steam_app_42"), "My game")

    def test_proton_game_is_recognized_without_a_window(self):
        proc = self.root / "proc"
        proc.mkdir()
        for pid, comm in ((10, "cs2.exe"), (11, "wineserver")):
            directory = proc / str(pid)
            directory.mkdir()
            fields = ["S"] + ["0"] * 18 + ["100"]
            (directory / "stat").write_text(f"{pid} ({comm}) {' '.join(fields)}")
            (directory / "environ").write_bytes(b"SteamAppId=730\0OTHER=value\0")
        scanner = ProcessApps({}, {}, proc=proc, steam=self.steam)
        self.assertEqual(scanner.snapshot({}).apps, {"Counter-Strike 2"})
        (proc / "10/stat").write_text("10 (cs2.exe) " + " ".join(["Z"] + ["0"] * 18 + ["100"]))
        self.assertEqual(scanner.snapshot({}).apps, set())

    def test_native_executable_and_hashed_icon_cache(self):
        directory = self.root / "process"
        directory.mkdir()
        executable = self.library / "steamapps/common/CS 2/game/bin/linux64/cs2"
        self.assertEqual(self.steam.process_name(directory, executable), "Counter-Strike 2")
        icon = self.client / "appcache/librarycache/730" / ("a" * 40 + ".jpg")
        icon.parent.mkdir(parents=True)
        icon.touch()
        self.assertEqual(self.steam.icon_file("730"), icon)

    def test_malformed_manifest_is_ignored(self):
        path = self.root / "broken.vdf"
        path.write_text('"AppState" { "appid" "1"')
        self.assertEqual(read_vdf(path), {})

    def test_binary_catalog_resolves_arbitrary_uninstalled_app_ids(self):
        fields = ("appinfo", "common", "name", "icon")
        for version in (39, 40, 41):
            with self.subTest(version=version):
                def key(value):
                    return struct.pack("<I", fields.index(value)) if version == 41 else value.encode() + b"\0"

                records = bytearray()
                for appid in (135791, 246802):
                    data = (b"\x00" + key("appinfo") + b"\x00" + key("common")
                            + b"\x01" + key("name") + f"Game {appid}".encode() + b"\0"
                            + b"\x01" + key("icon") + b"a" * 40 + b"\0\x08\x08\x08")
                    size = (60 if version >= 40 else 40)
                    data = b"\0" * size + data
                    records.extend(struct.pack("<II", appid, len(data)) + data)
                records.extend(struct.pack("<I", 0))
                header = struct.pack("<II", 0x07564400 | version, 1)
                table = b""
                if version == 41:
                    header += struct.pack("<Q", 16 + len(records))
                    table = struct.pack("<I", len(fields)) + b"".join(value.encode() + b"\0" for value in fields)
                path = self.client / "appcache/appinfo.vdf"
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(header + records + table)
                catalog = read_appinfo(path)
                self.assertEqual(set(catalog), {"135791", "246802"})
                steam = SteamLibrary([self.client])
                for appid in catalog:
                    self.assertEqual(steam.resolve(f"Steam_app_{appid}"), f"Game {appid}")

    def test_unknown_steam_window_supplies_its_game_name(self):
        self.assertEqual(self.steam.window_name("Steam_app_7654321", "My new game"), "My new game")
        self.assertEqual(self.steam.resolve("Steam_app_7654321"), "My new game")
        self.assertEqual(self.steam.window_name("Firefox", "Some page"), "Firefox")

    def test_window_icons_are_selected_independently_of_game_id(self):
        small = [16, 16] + [0xFF123456] * (16 * 16)
        preferred = [64, 64] + [0xFFAABBCC] * (64 * 64)
        width, height, pixels = window_icon(small + preferred)
        self.assertEqual((width, height, pixels[0]), (64, 64, 0xFFAABBCC))
        self.assertIsNone(window_icon([9999999, 9999999, 0]))
        self.assertEqual(window_icon(small + [128, 128, 0])[:2], (16, 16))
