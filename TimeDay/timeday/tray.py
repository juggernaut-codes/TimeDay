"""StatusNotifier icon and native panel menu for Cinnamon."""

import logging
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

from .i18n import tr


WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/org/timeday/TrayMenu"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
ITEM_XML = """
<node><interface name="org.kde.StatusNotifierItem">
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <property name="Category" type="s" access="read"/>
  <property name="Id" type="s" access="read"/>
  <property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/>
  <property name="IconName" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="OverlayIconName" type="s" access="read"/>
  <property name="ItemIsMenu" type="b" access="read"/>
  <property name="Menu" type="o" access="read"/>
  <property name="WindowId" type="u" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <signal name="NewToolTip"/>
</interface></node>
"""


def tray_pixmap(filename):
    images = []
    for size in (16, 22, 24, 32, 48):
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(filename), size, size, True)
        if not pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        width, height = pixbuf.get_width(), pixbuf.get_height()
        stride = pixbuf.get_rowstride()
        pixels = pixbuf.get_pixels()
        rgba = b"".join(pixels[y * stride:y * stride + width * 4] for y in range(height))
        # StatusNotifier expects ARGB32 in network byte order.
        argb = bytearray(rgba)
        argb[0::4] = rgba[3::4]
        argb[1::4] = rgba[0::4]
        argb[2::4] = rgba[1::4]
        argb[3::4] = rgba[2::4]
        images.append((width, height, bytes(argb)))
    return images


class Tray:
    def __init__(self, connection, icon, activate, quit_app, icon_file=None):
        self.connection = connection
        self.activate = activate
        self.menu = None
        self.watch = 0
        self.registration = 0
        self.ready = False
        if connection is None:
            return
        try:
            gi.require_version("Dbusmenu", "0.4")
            from gi.repository import Dbusmenu
        except (ImportError, ValueError):
            logging.warning(tr('Для меню в трее установите gir1.2-dbusmenu-glib-0.4.'))
            return
        pixmap = tray_pixmap(icon_file) if icon_file is not None else []
        if pixmap:
            icon = ""
        self.properties = {
            "Category": ("s", "ApplicationStatus"), "Id": ("s", "timeday"),
            "Title": ("s", "TimeDay"), "Status": ("s", "Active"),
            "IconName": ("s", icon), "IconPixmap": ("a(iiay)", pixmap),
            "IconThemePath": ("s", str(Path(icon_file).parent) if icon_file is not None else ""),
            "AttentionIconName": ("s", ""), "OverlayIconName": ("s", ""),
            "ItemIsMenu": ("b", False), "Menu": ("o", MENU_PATH), "WindowId": ("u", 0),
            "ToolTip": ("(sa(iiay)ss)", (icon, pixmap, "TimeDay", tr('Планировщик и таймер'))),
        }
        self.menu = Dbusmenu.Server.new(MENU_PATH)
        self.root = Dbusmenu.Menuitem.new()
        self.entries = {}
        for title, action in [("Открыть TimeDay", activate), ("Выйти", quit_app)]:
            item = Dbusmenu.Menuitem.new()
            item.property_set("label", tr(title))
            item.property_set_bool("visible", True)
            item.property_set_bool("enabled", True)
            item.connect("item-activated", lambda _item, _timestamp, callback=action: callback())
            self.root.child_append(item)
            self.entries[title] = item
        self.menu.set_root(self.root)
        interface = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        self.registration = connection.register_object(
            ITEM_PATH, interface, self._call, self._property, None,
        )
        self.watch = Gio.bus_watch_name_on_connection(
            connection, WATCHER, Gio.BusNameWatcherFlags.NONE,
            self._appeared, self._vanished,
        )

    def translate(self):
        if self.menu is None:
            return
        for title, item in self.entries.items():
            item.property_set("label", tr(title))
        signature, tooltip = self.properties["ToolTip"]
        self.properties["ToolTip"] = signature, (*tooltip[:3], tr("Планировщик и таймер"))
        self.connection.emit_signal(None, ITEM_PATH, ITEM_INTERFACE, "NewToolTip", None)

    def _appeared(self, bus, _name, owner):
        self.owner = owner
        bus.call(
            owner, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (ITEM_PATH,)), None, Gio.DBusCallFlags.NONE, 3000,
            None, self._registered, owner,
        )

    def _registered(self, bus, result, owner):
        try:
            bus.call_finish(result)
            if self.watch and owner == self.owner:
                self.ready = True
        except GLib.Error as exc:
            logging.warning(tr('Не удалось зарегистрировать значок TimeDay: %s'), exc.message)

    def _vanished(self, *_):
        self.owner = None
        self.ready = False

    def _property(self, _bus, _sender, _path, _interface, name):
        return GLib.Variant(*self.properties[name])

    def _call(self, _bus, _sender, _path, _interface, method, _params, invocation):
        invocation.return_value(None)
        if method in ("Activate", "SecondaryActivate"):
            self.activate()

    def stop(self):
        if self.watch:
            Gio.bus_unwatch_name(self.watch)
            self.watch = 0
        if self.registration:
            self.connection.unregister_object(self.registration)
            self.registration = 0
        self.menu = None
