"""Place newly opened windows at the center of their X11 monitor."""

import logging

from gi.repository import GLib


def center_on_first_map(window):
    def mapped(widget):
        widget.disconnect(handler)
        attempts = 0

        def position():
            nonlocal attempts
            attempts += 1
            return center_window(widget, wait_for_manager=attempts < 30)

        GLib.timeout_add(16, position)

    handler = window.connect("map", mapped)


def center_window(window, wait_for_manager=False):
    surface = window.get_surface()
    if surface is None or window.is_maximized():
        return False
    try:
        import gi
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
        if not isinstance(surface, GdkX11.X11Surface):
            return False
        from Xlib import X, Xatom, display, error
        from Xlib.protocol.event import ClientMessage
    except (ImportError, ValueError):
        return False
    connection = None
    try:
        connection = display.Display()
        root = connection.screen().root
        native = connection.create_resource_object("window", surface.get_xid())
        state = native.get_full_property(connection.intern_atom("WM_STATE"), X.AnyPropertyType)
        if state is None and wait_for_manager:
            return True
        size = native.get_geometry()
        monitor = surface.get_display().get_monitor_at_surface(surface)
        geometry = monitor.get_geometry()
        scale = window.get_scale_factor()
        left, top = geometry.x * scale, geometry.y * scale
        right, bottom = left + geometry.width * scale, top + geometry.height * scale
        workarea = root.get_full_property(connection.intern_atom("_NET_WORKAREA"), Xatom.CARDINAL)
        desktop = root.get_full_property(connection.intern_atom("_NET_CURRENT_DESKTOP"), Xatom.CARDINAL)
        index = 4 * int(desktop.value[0]) if desktop is not None else 0
        if workarea is not None and len(workarea.value) >= index + 4:
            x, y, width, height = map(int, workarea.value[index:index + 4])
            left, top = max(left, x), max(top, y)
            right, bottom = min(right, x + width), min(bottom, y + height)
        x = left + max(0, (right - left - size.width) // 2)
        y = top + max(0, (bottom - top - size.height) // 2)
        message = ClientMessage(
            window=native, client_type=connection.intern_atom("_NET_MOVERESIZE_WINDOW"),
            data=(32, [X.StaticGravity | (1 << 8) | (1 << 9) | (2 << 12), x, y, 0, 0]),
        )
        root.send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        connection.flush()
    except (OSError, error.XError, error.DisplayError):
        logging.debug("Could not center the TimeDay window", exc_info=True)
    finally:
        if connection is not None:
            connection.close()
    return False
