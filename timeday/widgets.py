"""GTK widgets shared across TimeDay views."""

import math
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .i18n import TranslatedText, render, tr
from . import DEFAULT_ACCENT
from .models import duration_label


_translated_widgets = weakref.WeakSet()


def weak_callback(callback, owner=None):
    owner = owner if owner is not None else getattr(callback, "__self__", None)
    if not isinstance(owner, GObject.Object):
        return callback
    reference = weakref.ref(owner)
    name = callback.__name__

    def invoke(*args, **kwargs):
        target = reference()
        if target is not None:
            return getattr(target, name)(*args, **kwargs)

    return invoke


def remember_text(widget, key, value):
    if not hasattr(widget, "_translated_text"):
        widget._translated_text = {}
    kind, field = key
    logical = field
    if kind == "setter":
        logical = field.removeprefix("set_")
        if logical == "text" and isinstance(widget, Gtk.Label):
            logical = "label"
    elif kind == "accessible":
        logical = "accessible-label"
    if isinstance(value, TranslatedText):
        widget._translated_text[logical] = (kind, field, value)
        _translated_widgets.add(widget)
    else:
        widget._translated_text.pop(logical, None)


def set_translated(widget, setter, value):
    getattr(widget, setter)(value)
    remember_text(widget, ("setter", setter), value)


def localized_widget(factory, *args, **properties):
    widget = factory(*args, **properties)
    for key, value in properties.items():
        remember_text(widget, ("property", key), value)
    return widget


def accessible_label(widget, value):
    widget.update_property([Gtk.AccessibleProperty.LABEL],
                           [GObject.Value(GObject.TYPE_STRING, str(value))])
    remember_text(widget, ("accessible", "label"), value)


class DropdownText(GObject.Object):
    text = GObject.Property(type=str)


def dropdown_factory():
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item):
        item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))

    def bind(_factory, item):
        item._text_binding = item.get_item().bind_property(
            "text", item.get_child(), "label", GObject.BindingFlags.SYNC_CREATE,
        )

    def unbind(_factory, item):
        item._text_binding.unbind()

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    factory.connect("unbind", unbind)
    return factory


def translated_dropdown(values):
    values = list(values)
    if not any(isinstance(value, TranslatedText) for value in values):
        return Gtk.DropDown.new_from_strings(values)
    items = Gio.ListStore.new(DropdownText)
    for value in values:
        items.append(DropdownText(text=value))
    expression = Gtk.PropertyExpression.new(DropdownText, None, "text")
    widget = Gtk.DropDown(model=items, expression=expression)
    widget.set_factory(dropdown_factory())
    widget.set_list_factory(dropdown_factory())
    widget._translated_options = values
    widget._translated_text = {}
    _translated_widgets.add(widget)
    return widget


def refresh_translations():
    for widget in list(_translated_widgets):
        for kind, key, original in list(widget._translated_text.values()):
            value = render(original.message, original.values)
            if kind == "setter":
                getattr(widget, key)(value)
            elif kind == "property":
                widget.set_property(key, value)
            else:
                widget.update_property([Gtk.AccessibleProperty.LABEL],
                                       [GObject.Value(GObject.TYPE_STRING, str(value))])
        if hasattr(widget, "_translated_options"):
            for index, original in enumerate(widget._translated_options):
                widget.get_model().get_item(index).text = str(tr(original))


def box(vertical=False, spacing=0, css=None):
    orientation = Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
    widget = Gtk.Box(orientation=orientation, spacing=spacing)
    if css:
        widget.add_css_class(css)
    return widget


def label(value="", css=None, wrap=False):
    widget = localized_widget(Gtk.Label, label=value, xalign=0, wrap=wrap)
    if css:
        widget.add_css_class(css)
    if wrap:
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


def icon(name, css=None, size=16):
    widget = Gtk.Image.new_from_icon_name(name)
    widget.set_pixel_size(size)
    if css:
        widget.add_css_class(css)
    return widget


def badge(name, css="metric-icon"):
    widget = icon(name, css)
    widget.set_valign(Gtk.Align.CENTER)
    widget.set_halign(Gtk.Align.START)
    return widget


def button(text, callback, css=None, icon_name=None):
    widget = Gtk.Button()
    if icon_name:
        content = box(spacing=8)
        content.append(icon(icon_name))
        content.append(label(text))
        if css != "nav":
            content.set_halign(Gtk.Align.CENTER)
        widget.set_child(content)
    else:
        set_translated(widget, 'set_label', text)
    if css:
        widget.add_css_class(css)
    invoke = weak_callback(callback)
    widget.connect("clicked", lambda *_: invoke())
    return widget


def icon_button(name, tooltip, callback, css=None):
    widget = Gtk.Button.new_from_icon_name(name)
    set_translated(widget, 'set_tooltip_text', tooltip)
    accessible_label(widget, tooltip)
    if css:
        widget.add_css_class(css)
    invoke = weak_callback(callback)
    widget.connect("clicked", lambda *_: invoke())
    return widget


def spacer(horizontal=True):
    widget = box()
    widget.set_hexpand(horizontal)
    widget.set_vexpand(not horizontal)
    return widget


def clear(container):
    while child := container.get_first_child():
        container.remove(child)


def truncate(widget, width=16):
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_max_width_chars(width)
    return widget


def empty_state(icon_name, title, message, width=40):
    content = box(True, 7, "empty")
    content.set_halign(Gtk.Align.CENTER)
    emblem = badge(icon_name, "empty-icon")
    emblem.set_halign(Gtk.Align.CENTER)
    content.append(emblem)
    for text, css in [(title, "strong"), (message, "muted")]:
        item = label(text, css, wrap=True)
        item.set_justify(Gtk.Justification.CENTER)
        item.set_halign(Gtk.Align.CENTER)
        item.set_max_width_chars(width)
        content.append(item)
    return content


class Segments(Gtk.Box):
    def __init__(self, options, selected, changed):
        super().__init__(spacing=2)
        self.add_css_class("segment")
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.CENTER)
        self.selected = selected
        self.changed = changed
        self.syncing = False
        self.buttons = {}
        group = None
        for key, title in options:
            widget = localized_widget(Gtk.ToggleButton, label=title)
            if group is not None:
                widget.set_group(group)
            group = widget
            widget.set_active(key == selected)
            widget.connect("toggled", self._toggled, key)
            self.buttons[key] = widget
            self.append(widget)
        self._styles()

    def _styles(self):
        for key, item in self.buttons.items():
            if key == self.selected:
                item.add_css_class("active")
            else:
                item.remove_css_class("active")

    def _toggled(self, widget, key):
        if self.syncing:
            return
        if widget.get_active() and self.selected != key:
            self.selected = key
            self._styles()
            self.changed(key)
        elif key == self.selected and not widget.get_active():
            # A click on the current option must leave one segment selected.
            GLib.idle_add(self._ensure_selection)

    def _ensure_selection(self):
        if self.selected is not None:
            self.buttons[self.selected].set_active(True)
        return False

    def select(self, key):
        self.selected = key
        self.syncing = True
        for name, item in self.buttons.items():
            item.set_active(name == key)
        self.syncing = False
        self._styles()


class ActivityChart(Gtk.DrawingArea):
    def __init__(self, height=190):
        super().__init__(content_height=height, hexpand=True)
        self.values = []
        self.labels = []
        self.tooltip_labels = []
        self.tooltip_values = None
        self.idle_values = []
        self.accent = DEFAULT_ACCENT
        self.display_ratios = []
        self.target_ratios = []
        self.step = 900
        self.hovered = None
        self.animation = None
        self.connect("unmap", self.stop_animation)
        self.set_draw_func(self.draw)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._tooltip)
        pointer = Gtk.EventControllerMotion()
        pointer.connect("motion", self._motion)
        pointer.connect("leave", self._leave)
        self.add_controller(pointer)

    def set_data(self, values, labels, accent, animate=False, tooltip_labels=None, tooltip_values=None, idle_values=None):
        values, labels = list(values), list(labels)
        self.idle_values = list(idle_values) if idle_values is not None else []
        self.tooltip_labels = labels if tooltip_labels is None else tooltip_labels
        self.tooltip_values = tooltip_values
        changed = self.values != values or self.labels != labels
        if not changed:
            self.accent = accent
            self.queue_draw()
            return
        same_labels = self.labels == labels
        previous = dict(zip(self.labels, self.display_ratios))
        initial = self.display_ratios[:] if same_labels else [previous.get(name, 0) for name in labels]
        self.values, self.labels, self.accent = values, labels, accent
        peak = max(values, default=0)
        self.step = next((step for step in (15, 60, 300, 900, 1800, 3600) if step * 4 >= peak),
                         math.ceil(peak / 14400) * 3600) if peak else 900
        self.target_ratios = [seconds / (self.step * 4) for seconds in values]
        if self.animation is not None:
            self.remove_tick_callback(self.animation)
            self.animation = None
        enabled = self.get_mapped() and self.get_settings().get_property("gtk-enable-animations")
        if values and enabled and (animate or same_labels):
            self.display_ratios = initial
            start = self.get_frame_clock().get_frame_time()
            self.animation = self.add_tick_callback(self._animate, (start, initial))
        else:
            self.display_ratios = self.target_ratios[:]
        accessible_label(self, tr('График активности. Всего {0}', duration_label(sum(values))))
        self.queue_draw()

    def _animate(self, _widget, clock, state):
        start, initial = state
        progress = min(1, (clock.get_frame_time() - start) / 360_000)
        if not self.get_settings().get_property("gtk-enable-animations"):
            progress = 1
        eased = progress * progress * (3 - 2 * progress)
        self.display_ratios = [old + (target - old) * eased for old, target in zip(initial, self.target_ratios)]
        self.queue_draw()
        if progress == 1:
            self.animation = None
        return progress < 1

    def stop_animation(self, *_):
        if self.animation is not None:
            self.remove_tick_callback(self.animation)
            self.animation = None
        self.display_ratios = self.target_ratios[:]

    def _index(self, x, y):
        if not self.values or y < 10 or y > self.get_height() - 24:
            return None
        width = self.get_width() - 58
        if width <= 0:
            return None
        index = int((x - 48) / width * len(self.values))
        return index if x >= 48 and 0 <= index < len(self.values) else None

    def _motion(self, _controller, x, y):
        hovered = self._index(x, y)
        if hovered != self.hovered:
            self.hovered = hovered
            self.queue_draw()

    def _leave(self, *_):
        self.hovered = None
        self.queue_draw()

    def _tooltip(self, _widget, x, y, keyboard, tooltip):
        index = None if keyboard else self._index(x, y)
        if index is None:
            return False
        reading = self.tooltip_values[index] if self.tooltip_values else duration_label(self.values[index])
        set_translated(tooltip, 'set_text', f"{self.tooltip_labels[index]}   ·   {reading}")
        return True

    def draw(self, _widget, cr, width, height):
        if not self.values:
            return
        left, right, top, bottom = 48, width - 10, 12, height - 26
        span = bottom - top
        color = Gdk.RGBA()
        color.parse(self.accent)
        cr.select_font_face("DejaVu Sans")
        cr.set_font_size(10)
        for level in range(5):
            y = bottom - span * level / 4
            value = self.step * level
            caption = tr('{0:g} ч', value / 3600) if value >= 3600 else (
                tr('{0:g} мин', value // 60) if value >= 60 else tr('{0:g} с', value)
            )
            cr.set_source_rgb(.60, .60, .63)
            cr.move_to(left - 9 - cr.text_extents(caption).width, y + 3)
            cr.show_text(caption)
            cr.set_source_rgb(.17, .17, .18)
            cr.set_line_width(1)
            cr.move_to(left, y)
            cr.line_to(right, y)
            cr.stroke()

        slots = len(self.values)
        pitch = (right - left) / slots
        bar = min(32, pitch * .58)
        tick_step = max(1, math.ceil(slots / max(2, (right-left) // 53)))
        for index, ratio in enumerate(self.display_ratios):
            center = left + (index + .5) * pitch
            if index == self.hovered:
                cr.set_source_rgba(color.red, color.green, color.blue, .10)
                cr.rectangle(center - pitch / 2, top, pitch, span)
                cr.fill()
            if ratio > 0:
                bar_height = max(2, span * ratio)
                cr.set_source_rgba(color.red, color.green, color.blue, .98 if index == self.hovered else .79)
                x, y = center - bar / 2, bottom - bar_height
                radius = min(3, bar / 2, bar_height / 2)
                cr.new_sub_path()
                cr.arc(x + bar - radius, y + radius, radius, -math.pi / 2, 0)
                cr.arc(x + bar - radius, bottom - radius, radius, 0, math.pi / 2)
                cr.arc(x + radius, bottom - radius, radius, math.pi / 2, math.pi)
                cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.fill_preserve()
                if self.idle_values and self.values[index] > 0:
                    idle_height = bar_height * min(1, max(0, self.idle_values[index] / self.values[index]))
                    if idle_height > 0:
                        cr.save()
                        cr.clip()
                        cr.set_source_rgb(.91, .64, .34)
                        cr.rectangle(x, y, bar, idle_height)
                        cr.fill()
                        cr.restore()
                cr.new_path()
            if index % tick_step == 0:
                caption = self.labels[index]
                cr.set_source_rgb(.60, .60, .63)
                cr.move_to(center - cr.text_extents(caption).width / 2, height - 7)
                cr.show_text(caption)
