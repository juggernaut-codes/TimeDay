import tempfile
import unittest
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from timeday.tray import tray_pixmap


class TrayImageTests(unittest.TestCase):
    def test_embedded_icons_include_panel_sizes_and_argb_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icon.png"
            image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 2, 2)
            image.fill(0x12345678)
            image.savev(str(path), "png", [], [])
            images = tray_pixmap(path)
            self.assertEqual([width for width, _height, _data in images], [16, 22, 24, 32, 48])
            for width, height, data in images:
                self.assertEqual(width, height)
                self.assertEqual(len(data), width * height * 4)
                self.assertEqual(data[:4], bytes((0x78, 0x12, 0x34, 0x56)))
