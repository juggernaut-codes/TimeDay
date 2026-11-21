"""Countdown and stopwatch with independent, persistable controls."""

import json
import math

from gi.repository import Gdk, Gtk

from .i18n import tr
from .models import Clock, clock_label, clock_state
from .widgets import accessible_label, set_translated
from .widgets import Segments, box, button, clear, label


class TimerRing(Gtk.DrawingArea):
    def __init__(self, accent):
        super().__init__(content_height=246, content_width=246)
        self.accent = accent
        self.fraction = 0
        self.set_draw_func(self.draw)

    def draw(self, _widget, cr, width, height):
        radius = min(width, height) / 2 - 9
        center = width / 2, height / 2
        cr.set_line_width(4)
        cr.arc(*center, radius, 0, math.tau)
        cr.set_source_rgb(.15, .15, .16)
        cr.stroke()
        if self.fraction > 0:
            color = Gdk.RGBA()
            color.parse(self.accent())
            cr.set_source_rgb(color.red, color.green, color.blue)
            cr.arc(*center, radius, -math.pi / 2, -math.pi / 2 + math.tau * self.fraction)
            cr.stroke()


class Timers(Gtk.Box):
    def __init__(self, store, accent, finished):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.store = store
        self.finished = finished
        state = clock_state(store.setting("clocks", "{}"))
        self.mode = state["mode"]
        self.countdown = Clock(state["duration"])
        self.countdown.saved = state["countdown"]
        self.stopwatch = Clock()
        self.stopwatch.saved = state["stopwatch"]
        self.laps = state["laps"]
        self.editing = False
        self.append(Segments(
            [("countdown", tr('Таймер')), ("stopwatch", tr('Секундомер'))],
            self.mode, self.change_mode,
        ))
        card = box(True, 16, "card")
        card.add_css_class("clock-card")
        self.append(card)
        face = Gtk.Overlay(halign=Gtk.Align.CENTER)
        self.ring = TimerRing(accent)
        self.ring_animation = None
        self.connect("map", lambda *_: self.refresh())
        self.connect("unmap", self.stop_ring_animation)
        face.set_child(self.ring)
        middle = box(True, 6)
        middle.set_halign(Gtk.Align.CENTER)
        middle.set_valign(Gtk.Align.CENTER)
        self.display = label("", "clock-face")
        self.display.set_halign(Gtk.Align.CENTER)
        middle.append(self.display)
        self.detail = label("", "muted")
        self.detail.set_halign(Gtk.Align.CENTER)
        middle.append(self.detail)
        face.add_overlay(middle)
        card.append(face)
        self.duration_row = box(spacing=9)
        self.duration_row.set_halign(Gtk.Align.CENTER)
        self.fields = []
        hours, rest = divmod(self.countdown.duration, 3600)
        minutes, seconds = divmod(rest, 60)
        for value, limit, caption in [(hours, 99, tr('Часы')), (minutes, 59, tr('Минуты')), (seconds, 59, tr('Секунды'))]:
            part = box(True, 4)
            field = Gtk.SpinButton.new_with_range(0, limit, 1)
            field.set_numeric(True)
            field.set_value(value)
            field.set_width_chars(2)
            accessible_label(field, caption)
            self.fields.append(field)
            part.append(field)
            part.append(label(caption, "muted"))
            self.duration_row.append(part)
            field.connect("value-changed", self.change_duration)
        card.append(self.duration_row)
        self.presets = box(spacing=5)
        self.presets.set_halign(Gtk.Align.CENTER)
        for minutes in (5, 15, 25, 50):
            self.presets.append(button(tr('{0} мин', minutes), lambda m=minutes: self.preset(m), "preset"))
        card.append(self.presets)
        controls = box(spacing=8)
        controls.set_halign(Gtk.Align.CENTER)
        self.reset_button = button(tr('Сбросить'), self.reset, "outline")
        self.start_button = button(tr('Старт'), self.start_pause, "primary")
        self.lap_button = button(tr('Круг'), self.lap, "outline")
        for widget in (self.reset_button, self.start_button, self.lap_button):
            controls.append(widget)
        card.append(controls)
        self.lap_list = box(True, 7, "card")
        self.append(self.lap_list)
        for index, elapsed in enumerate(self.laps):
            self.add_lap_row(index, elapsed)
        self.refresh()

    @property
    def clock(self):
        return self.countdown if self.mode == "countdown" else self.stopwatch

    def save(self):
        self.store.set_setting("clocks", json.dumps({
            "mode": self.mode, "duration": self.countdown.duration,
            "countdown": self.countdown.elapsed, "stopwatch": self.stopwatch.elapsed, "laps": self.laps,
        }))

    def checkpoint(self):
        if self.countdown.running or self.stopwatch.running:
            self.save()

    def change_mode(self, mode):
        self.mode = mode
        self.save()
        self.refresh()

    def change_duration(self, *_):
        if self.editing:
            return
        hours, minutes, seconds = (field.get_value_as_int() for field in self.fields)
        self.countdown.duration = hours * 3600 + minutes * 60 + seconds
        self.countdown.reset()
        self.save()
        self.refresh()

    def preset(self, minutes):
        self.editing = True
        for field, value in zip(self.fields, (0, minutes, 0)):
            field.set_value(value)
        self.editing = False
        self.change_duration()

    def start_pause(self):
        self.check_countdown()
        clock = self.clock
        if clock.running:
            clock.pause()
        else:
            if self.mode == "countdown":
                if self.duration_row.get_sensitive():
                    for field in self.fields:
                        field.update()
                if not self.countdown.remaining:
                    self.countdown.reset()
            clock.start()
        self.save()
        self.refresh()

    def reset(self):
        self.clock.reset()
        if self.mode == "stopwatch":
            self.laps.clear()
            clear(self.lap_list)
        self.save()
        self.refresh()

    def lap(self):
        if not self.stopwatch.running:
            return
        elapsed = self.stopwatch.elapsed
        self.laps.append(elapsed)
        self.add_lap_row(len(self.laps) - 1, elapsed)
        self.save()
        self.refresh()

    def add_lap_row(self, index, total):
        split = total - (self.laps[index - 1] if index else 0)
        row = box(spacing=12, css="task-row")
        title = label(tr('Круг {0}', index + 1), "strong")
        title.set_hexpand(True)
        row.append(title)
        row.append(label(clock_label(split), "mono"))
        row.append(label(clock_label(total), "muted"))
        self.lap_list.prepend(row)

    def check_countdown(self):
        if self.countdown.check_finished():
            self.save()
            self.finished()

    def refresh(self):
        self.check_countdown()
        if not self.get_mapped() and self.get_parent() is not None:
            return
        clock = self.clock
        countdown = self.mode == "countdown"
        shown = clock.remaining if countdown else clock.elapsed
        text = clock_label(shown)
        if self.display.get_text() != text:
            set_translated(self.display, 'set_text', text)
        finished = countdown and bool(clock.duration) and not shown
        paused = clock.saved > 0 and not finished
        if countdown and not clock.duration:
            state = tr('Укажите длительность')
        elif finished:
            state = tr('Время вышло')
        elif clock.running:
            state = tr('Идёт отсчёт')
        elif paused:
            state = tr('На паузе')
        else:
            state = tr('Готов к запуску')
        set_translated(self.detail, 'set_text', state)
        set_translated(self.start_button, 'set_label', tr('Пауза') if clock.running else tr('Продолжить') if paused else tr('Старт'))
        self.duration_row.set_visible(countdown)
        self.presets.set_visible(countdown)
        editable = not self.countdown.running and (self.countdown.saved == 0 or not self.countdown.remaining)
        self.duration_row.set_sensitive(editable)
        self.presets.set_sensitive(editable)
        self.lap_button.set_visible(not countdown)
        self.lap_button.set_sensitive(self.stopwatch.running)
        self.lap_list.set_visible(not countdown and bool(self.laps))
        self.refresh_ring()
        animate = self.get_mapped() and clock.running and self.get_settings().get_property("gtk-enable-animations")
        if animate and self.ring_animation is None:
            self.ring_animation = self.add_tick_callback(self.animate_ring)
        elif not animate:
            self.stop_ring_animation()

    def refresh_ring(self):
        clock = self.clock
        countdown = self.mode == "countdown"
        if countdown and clock.duration:
            self.ring.fraction = 1 - clock.elapsed / clock.duration
        elif not countdown:
            self.ring.fraction = (clock.elapsed % 60) / 60
        else:
            self.ring.fraction = 0
        self.ring.queue_draw()

    def animate_ring(self, _widget, _clock):
        if not self.clock.running or not self.get_settings().get_property("gtk-enable-animations"):
            self.ring_animation = None
            return False
        self.refresh_ring()
        return True

    def stop_ring_animation(self, *_):
        if self.ring_animation is not None:
            self.remove_tick_callback(self.ring_animation)
            self.ring_animation = None
