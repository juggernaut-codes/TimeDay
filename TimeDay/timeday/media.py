"""Nonblocking MPRIS playback detection on the user's desktop session bus."""

import time
from gi.repository import Gio, GLib


class MediaPlayback:
    def __init__(self):
        self.players = {}
        self.next_poll = 0
        self.pending = False
        self.bus = None
        Gio.bus_get(Gio.BusType.SESSION, None, self._connected)

    def _connected(self, _source, result):
        try:
            self.bus = Gio.bus_get_finish(result)
        except GLib.Error:
            pass

    def playing(self):
        now = time.monotonic()
        if self.bus and now >= self.next_poll and not self.pending:
            self.next_poll = now + 3
            self.pending = True
            self.bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                          'org.freedesktop.DBus', 'ListNames', None, GLib.VariantType.new('(as)'),
                          Gio.DBusCallFlags.NO_AUTO_START, 1000, None, self._names)
        return any(status and now - stamp < 10 for status, stamp in self.players.values())

    def _names(self, connection, result):
        self.pending = False
        try:
            names = connection.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        names = {name for name in names if name.startswith('org.mpris.MediaPlayer2.')}
        self.players = {name: value for name, value in self.players.items() if name in names}
        for name in names:
            connection.call(name, '/org/mpris/MediaPlayer2', 'org.freedesktop.DBus.Properties',
                            'Get', GLib.Variant('(ss)', ('org.mpris.MediaPlayer2.Player', 'PlaybackStatus')),
                            GLib.VariantType.new('(v)'), Gio.DBusCallFlags.NO_AUTO_START,
                            1000, None, self._status, name)

    def _status(self, connection, result, name):
        try:
            status = connection.call_finish(result).unpack()[0]
            self.players[name] = (status == 'Playing', time.monotonic())
        except GLib.Error:
            self.players.pop(name, None)
