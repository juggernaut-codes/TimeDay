"""Day agenda, optional week grid and event editor."""

from datetime import date, datetime, timedelta
from pathlib import Path

from gi.repository import Gdk, Gtk

from .i18n import TranslatedNames, tr
from .events import (
    DAILY, WEEKDAYS_ONLY, compact_event_layout, day_spans, minute_text, minute_value, occurrences,
    time_range, week_start,
)
from .models import MONTHS, WEEKDAYS, date_label
from .planner import CalendarPicker
from .widgets import accessible_label, localized_widget, set_translated, translated_dropdown
from .widgets import Segments, box, button, clear, icon, icon_button, label, spacer, truncate, weak_callback


MIN_GRID_HEIGHT = 240
GUTTER = 51
DAY_MINIMUM = 54
SHORT_DAYS = TranslatedNames(("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"))
COLORS = TranslatedNames(("Основной", "Зелёный", "Оранжевый", "Синий", "Розовый", "Красный"))
MARKERS = ("", "book", "sport", "work", "target", "art", "music", "coffee", "star")
LEGACY_MARKERS = dict(zip(("📚", "🏋️", "💼", "🎯", "🎨", "🎵", "☕", "⭐"), MARKERS[1:]))
MARKER_NAMES = TranslatedNames(("Без значка", "Учёба", "Спорт", "Работа", "Цель", "Творчество", "Музыка", "Перерыв", "Важное"))


def marker_key(value):
    return LEGACY_MARKERS.get(value, value) if value in MARKERS or value in LEGACY_MARKERS else ""


def marker_icon(value, size=16):
    image = Gtk.Image.new_from_file(str(Path(__file__).with_name("event_icons") / f"{marker_key(value)}.svg"))
    image.set_pixel_size(size)
    image.set_valign(Gtk.Align.CENTER)
    return image


REPETITIONS = TranslatedNames(("Не повторять", "Каждый день", "По будням", "Выбрать дни недели"))


def week_caption(first):
    last = date.fromordinal(min(date.max.toordinal(), first.toordinal() + 6))
    if first.month == last.month:
        return f"{first.day}–{last.day} {MONTHS[last.month - 1]} {last.year}"
    start = f"{first.day} {MONTHS[first.month - 1]}"
    if first.year != last.year:
        start += f" {first.year}"
    return f"{start} – {date_label(last)}"


class EventEditor(Gtk.Window):
    def __init__(self, parent, day, start, item, store, changed):
        super().__init__(title=tr('Событие') if item else tr('Новое событие'),
                         transient_for=parent, modal=True, default_width=440, resizable=False)
        set_translated(self, "set_title", tr('Событие') if item else tr('Новое событие'))
        self.item, self.store, self.changed = item, store, changed
        self.day = day
        form = box(True, 12, "dialog-content")
        self.set_child(form)
        self.scope = None
        if item and item.event.repeat:
            form.append(label(tr('Изменить'), "strong"))
            self.scope = translated_dropdown([tr('Только это событие'), tr('Вся серия')])
            load = weak_callback(self.load)
            self.scope.connect("notify::selected", lambda *_: load())
            form.append(self.scope)
        form.append(label(tr('Название'), "strong"))
        self.title_entry = localized_widget(Gtk.Entry, placeholder_text=tr('Например, тренировка'), max_length=140)
        form.append(self.title_entry)
        appearance = box(spacing=9)
        for caption, field in ((tr('Цвет'), 'color'), (tr('Значок'), 'marker')):
            column = box(True, 4)
            column.set_hexpand(True)
            column.append(label(caption, "strong"))
            dropdown = translated_dropdown(COLORS if field == 'color' else MARKER_NAMES)
            accessible_label(dropdown, caption)
            setattr(self, field, dropdown)
            column.append(dropdown)
            appearance.append(column)
        form.append(appearance)
        self.marker_buttons = []
        choices = box(spacing=4)
        for index, value in enumerate(MARKERS):
            toggle = Gtk.ToggleButton()
            toggle.set_child(marker_icon(value) if value else icon("edit-clear-symbolic"))
            if self.marker_buttons:
                toggle.set_group(self.marker_buttons[0])
            set_translated(toggle, 'set_tooltip_text', MARKER_NAMES[index])
            accessible_label(toggle, MARKER_NAMES[index])
            toggle.connect("clicked", lambda _button, selected=index: self.marker.set_selected(selected))
            choices.append(toggle)
            self.marker_buttons.append(toggle)
        self.marker.connect("notify::selected", lambda *_: self.marker_buttons[self.marker.get_selected()].set_active(True))
        self.marker_buttons[0].set_active(True)
        form.append(choices)
        times = box(spacing=9)
        date_field = box(True, 4)
        date_field.append(label(tr('Дата'), "strong"))
        self.date_button = localized_widget(Gtk.MenuButton, direction=Gtk.ArrowType.DOWN)
        self.popover = Gtk.Popover()
        self.picker = CalendarPicker(day, self.select_day)
        self.picker.add_css_class("picker")
        self.popover.set_child(self.picker)
        self.date_button.set_popover(self.popover)
        date_field.append(self.date_button)
        times.append(date_field)
        self.start_entry = self.time_field(tr('Начало'), times)
        self.end_entry = self.time_field(tr('Конец'), times)
        form.append(times)
        self.overnight = label(tr('Окончание на следующий день'), "muted")
        self.overnight.set_visible(False)
        form.append(self.overnight)

        self.repeat_panel = localized_widget(Gtk.Expander, label=tr('Повтор'))
        self.repeat_panel.add_css_class("event-repeat")
        self.repeat_group = box(True, 7)
        self.repeat = translated_dropdown(REPETITIONS)
        self.repeat_group.append(self.repeat)
        self.weekdays = box(spacing=4)
        self.day_toggles = [localized_widget(Gtk.ToggleButton, label=name) for name in SHORT_DAYS]
        for index, toggle in enumerate(self.day_toggles):
            toggle.add_css_class("weekday-toggle")
            accessible_label(toggle, WEEKDAYS[index])
            self.weekdays.append(toggle)
        self.repeat_group.append(self.weekdays)
        update_repeat = weak_callback(self.update_repeat)
        self.repeat.connect("notify::selected", lambda *_: update_repeat())
        self.repeat_panel.set_child(self.repeat_group)
        form.append(self.repeat_panel)
        self.error = label("", "error-text", wrap=True)
        self.error.set_visible(False)
        form.append(self.error)

        actions = box(spacing=6)
        if item:
            actions.append(button(tr('Удалить'), self.confirm_delete, "danger"))
        actions.append(spacer())
        actions.append(button(tr('Отмена'), weak_callback(self.close, self)))
        save_button = button(tr('Сохранить') if item else tr('Добавить'), self.commit, "primary")
        actions.append(save_button)
        form.append(actions)
        self.set_default_widget(save_button)
        self.title_entry.set_activates_default(True)
        self.title_entry.grab_focus()
        if item:
            self.load()
        else:
            self.select_day(day)
            set_translated(self.start_entry, 'set_text', minute_text(start))
            set_translated(self.end_entry, 'set_text', minute_text(min(start + 60, 1440)))
            self.day_toggles[day.weekday()].set_active(True)
            self.update_repeat()
        update_overnight = weak_callback(self.update_overnight)
        for entry in (self.start_entry, self.end_entry):
            entry.connect("changed", lambda *_: update_overnight())

    @staticmethod
    def time_field(caption, row):
        column = box(True, 4)
        column.append(label(caption, "strong"))
        entry = localized_widget(Gtk.Entry, width_chars=5, max_width_chars=5, max_length=5)
        accessible_label(entry, caption)
        column.append(entry)
        row.append(column)
        return entry

    @property
    def single(self):
        return self.scope is not None and self.scope.get_selected() == 0

    def load(self):
        item = self.item
        source = item.event if not self.single else item
        self.error.set_visible(False)
        set_translated(self.title_entry, 'set_text', source.title)
        self.color.set_selected(source.color)
        self.marker.set_selected(MARKERS.index(marker_key(source.marker)))
        self.select_day(source.day)
        set_translated(self.start_entry, 'set_text', minute_text(source.start))
        set_translated(self.end_entry, 'set_text', minute_text(source.end if source.end <= 1440 else source.end - 1440))
        mask = item.event.repeat if not self.single else 0
        self.repeat.set_selected({0: 0, DAILY: 1, WEEKDAYS_ONLY: 2}.get(mask, 3))
        for index, toggle in enumerate(self.day_toggles):
            toggle.set_active(bool(mask & (1 << index)) if mask else index == self.day.weekday())
        self.update_repeat()
        self.update_overnight()

    def select_day(self, day):
        self.day = day
        set_translated(self.date_button, 'set_label', date_label(day))
        self.picker.select(day)
        self.popover.popdown()

    def update_repeat(self):
        selection = self.repeat.get_selected()
        self.repeat_panel.set_visible(not self.single)
        set_translated(self.repeat_panel, 'set_label', tr('Повтор') if not selection else tr('Повтор · {0}', REPETITIONS[selection]))
        self.weekdays.set_visible(selection == 3)

    def update_overnight(self):
        try:
            overnight = minute_value(self.end_entry.get_text(), True) < minute_value(self.start_entry.get_text())
        except ValueError:
            overnight = False
        self.overnight.set_visible(overnight)

    def commit(self):
        try:
            title = self.title_entry.get_text()
            start = minute_value(self.start_entry.get_text())
            end = minute_value(self.end_entry.get_text(), True)
            if end == start:
                raise ValueError(tr('Начало и конец должны различаться.'))
            if end < start:
                end += 1440
            if self.single:
                self.store.save_occurrence(self.item.event.id, self.item.original_day, title, self.day, start, end,
                                           color=self.color.get_selected(), marker=MARKERS[self.marker.get_selected()])
            else:
                choice = self.repeat.get_selected()
                mask = (0, DAILY, WEEKDAYS_ONLY)[choice] if choice < 3 else sum(
                    1 << index for index, toggle in enumerate(self.day_toggles) if toggle.get_active()
                )
                if choice == 3 and not mask:
                    raise ValueError(tr('Выберите хотя бы один день недели.'))
                self.store.save_event(title, self.day, start, end, mask, self.item.event.id if self.item else None,
                                      color=self.color.get_selected(), marker=MARKERS[self.marker.get_selected()])
        except ValueError as exc:
            set_translated(self.error, 'set_text', str(exc))
            self.error.set_visible(True)
            return
        day = self.day
        if self.scope is not None and not self.single and day == self.item.event.day:
            day = self.item.day
        self.changed(day)
        self.close()

    def confirm_delete(self):
        text = (tr('Удалить это событие?') if self.single else tr('Удалить всю серию?')) if self.scope is not None else tr('Удалить событие?')
        dialog = localized_widget(Gtk.MessageDialog, transient_for=self, modal=True, text=text, secondary_text=self.item.title)
        dialog.add_button(tr('Отмена'), Gtk.ResponseType.CANCEL)
        dialog.add_button(tr('Удалить'), Gtk.ResponseType.ACCEPT).add_css_class("danger")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        def respond(message, response):
            message.close()
            if response == Gtk.ResponseType.ACCEPT:
                if self.single:
                    self.store.save_occurrence(self.item.event.id, self.item.original_day)
                else:
                    self.store.delete_event(self.item.event.id)
                self.changed(self.item.day)
                self.close()

        dialog.connect("response", respond)
        dialog.present()


class DayColumn(Gtk.Overlay):
    def __init__(self, new, edit, accent, owner):
        super().__init__(hexpand=True)
        self.owner = owner
        self.preview = None
        self.new, self.edit, self.accent = new, edit, accent
        self.day = date.today()
        self.today = False
        self.blocks = []
        self.grid = Gtk.DrawingArea(content_width=DAY_MINIMUM)
        self.grid.set_draw_func(self.draw)
        self.grid.connect("resize", lambda _area, width, height: self.place(width, height))
        click = Gtk.GestureClick(button=1)
        click.connect("released", self.add_at)
        self.add_controller(click)
        self.set_child(self.grid)
        self.layer = Gtk.Fixed()
        self.add_overlay(self.layer)
        self.set_clip_overlay(self.layer, True)
        self.line = Gtk.DrawingArea(can_target=False)
        self.line.set_draw_func(self.draw_preview)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        target.connect("accept", lambda *_: self.owner.dragged is not None)
        target.connect("enter", self.drag_motion)
        target.connect("motion", self.drag_motion)
        target.connect("leave", lambda *_: self.clear_preview())
        target.connect("drop", self.drop_event)
        self.add_controller(target)
        self.add_overlay(self.line)

    def destination(self, x, y):
        hit = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while hit is not None and hit is not self:
            item = getattr(hit, "schedule_item", None)
            if item is not None:
                return item.day, item.start, item
            hit = hit.get_parent()
        minute = min(1425, max(0, round(y * 1440 / max(1, self.grid.get_height()) / 15) * 15))
        return self.day, minute, None

    def clear_preview(self):
        self.preview = None
        self.line.queue_draw()
        self.line.set_visible(False)

    def drag_motion(self, _target, x, y):
        item = self.owner.dragged
        if item is None:
            return Gdk.DragAction(0)
        day, minute, other = self.destination(x, y)
        self.preview = (minute, minute + item.end - item.start)
        self.line.set_visible(True)
        self.line.queue_draw()
        return Gdk.DragAction.MOVE

    def drop_event(self, _target, value, x, y):
        item = self.owner.dragged
        if value != "timeday-event" or item is None:
            return False
        day, minute, other = self.destination(x, y)
        self.owner.end_drag()
        if other == item or (day == item.day and minute == item.start):
            return False
        self.owner.move(item, day, minute, other)
        return True

    def add_at(self, _gesture, _count, x, y):
        hit = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while hit is not None and hit is not self:
            if isinstance(hit, Gtk.Button):
                return
            hit = hit.get_parent()
        height = self.grid.get_height()
        if height <= 0:
            return
        minute = min(1425, max(0, int(y * 1440 / height) // 15 * 15))
        self.new(self.day, minute)

    def show_day(self, day, spans):
        self.day = day
        self.today = day == date.today()
        self.line.set_visible(False)
        self.grid.queue_draw()
        clear(self.layer)
        self.blocks = []
        for start, end, item in spans:
            card = Gtk.Button()
            card.add_css_class("event-block")
            card.add_css_class(f"event-tier-{item.color}")
            content = box(spacing=10)
            content.set_valign(Gtk.Align.CENTER)
            details = box(True)
            details.set_hexpand(True)
            details.set_valign(Gtk.Align.CENTER)
            title = truncate(label(item.title, "strong"), 24)
            title.set_hexpand(True)
            details.append(title)
            when = truncate(label(time_range(start, end), "event-time"), 16)
            when.set_visible(False)
            details.append(when)
            content.append(details)
            if marker_key(item.marker):
                emblem = marker_icon(item.marker, 28)
                emblem.add_css_class("schedule-event-icon")
                content.append(emblem)
            card.set_child(content)
            card.schedule_item = item
            source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            source.set_button(1)
            source.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value("timeday-event"))
            source.connect("drag-begin", lambda src, _drag, selected=item, widget=card: self.owner.begin_drag(selected, src, widget))
            source.connect("drag-end", lambda *_: self.owner.end_drag())
            card.add_controller(source)
            tooltip = f"{item.title}\n{date_label(item.day)}, {time_range(item.start, item.end)}"
            set_translated(card, 'set_tooltip_text', tooltip)
            accessible_label(card, tooltip)
            card.connect("clicked", lambda _card, selected=item: self.edit(selected))
            self.blocks.append((start, end, (card, when)))
            self.layer.put(card, 0, 0)
        self.place(self.grid.get_width(), self.grid.get_height())

    def place(self, width, height):
        if width <= 0 or height <= 0:
            return
        for (top, bottom, (card, when)), lane, count in compact_event_layout(self.blocks, height):
            left = round(lane * width / count)
            right = round((lane + 1) * width / count)
            cell = right - left
            card_height = bottom - top - 1
            when.set_visible(card_height >= 32 and cell >= 76)
            emblem = card.get_child().get_last_child()
            if isinstance(emblem, Gtk.Image):
                emblem.set_visible(card_height >= 24 and cell >= 90)
                emblem.set_pixel_size(max(16, min(32, card_height - 6)))
            card.set_size_request(max(1, cell), card_height)
            self.layer.move(card, left, top + 1)

    def draw(self, _area, cr, width, height):
        if self.today:
            cr.set_source_rgb(.085, .085, .085)
        elif self.day.weekday() >= 5:
            cr.set_source_rgb(.041, .041, .046)
        else:
            cr.set_source_rgb(.051, .051, .055)
        cr.paint()
        cr.set_line_width(1)
        for hour in range(24):
            y = round(hour * height / 24) + .5
            shade = .145 if hour % 2 == 0 else .105
            cr.set_source_rgb(shade, shade, shade + .013)
            cr.move_to(0, y)
            cr.line_to(width, y)
            cr.stroke()
        cr.set_source_rgb(.17, .17, .18)
        cr.move_to(.5, 0)
        cr.line_to(.5, height)
        cr.stroke()

    def draw_preview(self, _area, cr, width, height):
        color = Gdk.RGBA()
        color.parse(self.accent())
        if self.preview is not None:
            start, end = self.preview
            top = start * height / 1440
            bottom = min(1440, end) * height / 1440
            cr.set_source_rgba(color.red, color.green, color.blue, .28)
            cr.rectangle(2, top, max(1, width - 4), max(4, bottom - top))
            cr.fill_preserve()
            cr.set_source_rgba(color.red, color.green, color.blue, 1)
            cr.set_line_width(2)
            cr.stroke()
            cr.set_font_size(12)
            cr.move_to(7, max(14, min(height - 4, top + 15)))
            cr.show_text(str(time_range(start, end)))


class WeekView(Gtk.Box):
    def __init__(self, new, edit, accent, move):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.move = move
        self.dragged = None
        self.drag_widget = None
        self.accent = accent
        self.week = week_start(date.today())
        self.today = date.today()
        panel = box(True, css="week-frame")
        panel.set_vexpand(True)
        self.append(panel)
        dates = box(css="week-heading")
        panel.append(dates)
        corner = label(tr('Время'), "faint")
        corner.set_size_request(GUTTER, -1)
        corner.set_halign(Gtk.Align.CENTER)
        dates.append(corner)
        daystrip = box()
        daystrip.set_homogeneous(True)
        daystrip.set_hexpand(True)
        dates.append(daystrip)
        self.headers = []
        for index, day_name in enumerate(SHORT_DAYS):
            cell = box(spacing=5, css="week-day")
            cell.set_size_request(DAY_MINIMUM, -1)
            content = box(spacing=5)
            content.set_hexpand(True)
            content.set_halign(Gtk.Align.CENTER)
            title = label(day_name, "muted")
            num = label("", "strong")
            content.append(title)
            content.append(num)
            cell.append(content)
            daystrip.append(cell)
            self.headers.append((cell, num))

        body = box()
        body.set_vexpand(True)
        panel.append(body)
        self.ruler = Gtk.DrawingArea(content_height=MIN_GRID_HEIGHT, content_width=GUTTER, can_target=False)
        self.ruler.set_draw_func(self.draw_ruler)
        body.append(self.ruler)
        columns = box()
        columns.set_hexpand(True)
        columns.set_homogeneous(True)
        self.days = [DayColumn(new, edit, accent, self) for _ in SHORT_DAYS]
        for day in self.days:
            columns.append(day)
        body.append(columns)

    def begin_drag(self, item, source, card):
        self.dragged = item
        self.drag_widget = card
        source.set_icon(Gtk.WidgetPaintable.new(card), 0, 0)
        card.add_css_class("event-dragging")

    def end_drag(self):
        self.dragged = None
        if self.drag_widget is not None:
            self.drag_widget.remove_css_class("event-dragging")
        self.drag_widget = None
        for column in self.days:
            column.clear_preview()

    def show_week(self, week, items):
        self.week = week
        self.today = date.today()
        self.ruler.queue_draw()
        for index, column in enumerate(self.days):
            ordinal = self.week.toordinal() + index
            cell, num = self.headers[index]
            if ordinal > date.max.toordinal():
                cell.set_visible(False)
                column.set_visible(False)
                continue
            cell.set_visible(True)
            column.set_visible(True)
            day = date.fromordinal(ordinal)
            set_translated(num, 'set_text', f"{day.day:02d}")
            if day == self.today:
                cell.add_css_class("week-today")
            else:
                cell.remove_css_class("week-today")
            column.show_day(day, day_spans(items, day))

    def tick(self):
        if not self.get_mapped():
            return
        for column in self.days:
            if column.today:
                column.line.queue_draw()
                self.ruler.queue_draw()

    def draw_ruler(self, _area, cr, width, height):
        cr.set_source_rgb(.051, .051, .056)
        cr.paint()
        cr.select_font_face("DejaVu Sans")
        cr.set_font_size(10)
        cr.set_source_rgb(.60, .60, .65)
        step = 2 if height >= 360 else 3
        for hour in range(0, 24, step):
            cr.move_to(8, hour * height / 24 + 11)
            cr.show_text(f"{hour:02d}:00")
        cr.move_to(8, height - 4)
        cr.show_text("23:59")
        cr.set_source_rgb(.15, .15, .17)
        cr.move_to(width - .5, 0)
        cr.line_to(width - .5, height)
        cr.stroke()
        if 0 <= (self.today - self.week).days < 7:
            now = datetime.now()
            position = (now.hour * 60 + now.minute + now.second / 60) * height / 1440
            y = max(14, min(height - 33, position - 9))
            color = Gdk.RGBA()
            color.parse(self.accent())
            cr.set_source_rgba(color.red, color.green, color.blue, .22)
            cr.rectangle(3, y, width - 6, 18)
            cr.fill()
            cr.set_source_rgb(color.red, color.green, color.blue)
            cr.move_to(8, y + 13)
            cr.show_text(f"{now:%H:%M}")


class ScheduleView(Gtk.Box):
    def __init__(self, store, new, edit, accent):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, vexpand=True)
        self.store, self.edit = store, edit
        self.today = self.day = date.today()
        self.agenda_signature = None
        self.items = []

        toolbar = box(spacing=6)
        toolbar.append(icon_button("go-previous-symbolic", tr('Предыдущая неделя'), lambda: self.shift(-7), "outline"))
        toolbar.append(icon_button("go-next-symbolic", tr('Следующая неделя'), lambda: self.shift(7), "outline"))
        self.range = localized_widget(Gtk.MenuButton, has_frame=False)
        self.range.add_css_class("schedule-range")
        set_translated(self.range, 'set_tooltip_text', tr('Выбрать дату'))
        self.picker = CalendarPicker(self.day, self.pick_day)
        self.picker.add_css_class("picker")
        self.date_popup = Gtk.Popover()
        self.date_popup.set_child(self.picker)
        self.range.set_popover(self.date_popup)
        toolbar.append(self.range)
        toolbar.append(button(tr('Сегодня'), lambda: self.focus_day(date.today()), "outline"))
        toolbar.append(spacer())
        self.layout = Segments([("day", tr('Список')), ("week", tr('Сетка недели'))], "day", self.change_layout)
        toolbar.append(self.layout)
        self.append(toolbar)

        self.views = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=260, interpolate_size=True,
            vhomogeneous=False, vexpand=True,
        )
        self.append(self.views)
        agenda = box(True, 14)
        self.views.add_named(agenda, "day")
        self.day_strip = box(spacing=6)
        self.day_strip.set_homogeneous(True)
        self.day_tabs = []
        group = None
        for index, name in enumerate(SHORT_DAYS):
            tab = localized_widget(Gtk.ToggleButton, )
            tab.add_css_class("schedule-day")
            if group is not None:
                tab.set_group(group)
            group = tab
            face = box(True, 3)
            face.set_halign(Gtk.Align.CENTER)
            weekday = label(name, "schedule-weekday")
            weekday.set_halign(Gtk.Align.CENTER)
            face.append(weekday)
            number = label("", "schedule-date")
            number.set_halign(Gtk.Align.CENTER)
            face.append(number)
            mark = box(css="schedule-mark")
            mark.set_halign(Gtk.Align.CENTER)
            face.append(mark)
            tab.set_child(face)
            tab.connect("clicked", lambda _tab, offset=index: self.pick_tab(offset))
            self.day_tabs.append((tab, number, mark))
            self.day_strip.append(tab)
        agenda.append(self.day_strip)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        agenda.append(scroller)
        self.agenda_panel = box(True, 10, "card")
        self.agenda_panel.set_valign(Gtk.Align.START)
        scroller.set_child(self.agenda_panel)
        self.day_title = label("", "section-title")
        self.agenda_panel.append(self.day_title)
        self.rows = box(True, 3)
        self.agenda_panel.append(self.rows)

        self.week_grid = WeekView(new, edit, accent, self.move_event)
        self.views.add_named(self.week_grid, "week")
        self.views.set_visible_child_name("day")
        self.refresh()

    def move_event(self, item, day, minute, other=None):
        self.store.move_calendar_item(item, day, minute, other)
        self.refresh()

    def shift(self, offset):
        ordinal = self.day.toordinal() + offset
        if 1 <= ordinal <= date.max.toordinal():
            self.focus_day(date.fromordinal(ordinal))

    def pick_tab(self, index):
        self.focus_day(week_start(self.day) + timedelta(days=index))

    def pick_day(self, day):
        self.date_popup.popdown()
        self.focus_day(day)

    def focus_day(self, day):
        self.day = day
        self.refresh()

    def open_day(self, day):
        self.layout.select("day")
        self.views.set_visible_child_name("day")
        self.focus_day(day)

    def change_layout(self, mode):
        self.views.set_visible_child_name(mode)
        if mode == "week":
            self.week_grid.show_week(week_start(self.day), self.items)
        else:
            self.refresh_agenda()

    def refresh(self):
        self.today = date.today()
        first = week_start(self.day)
        last = date.fromordinal(min(date.max.toordinal(), first.toordinal() + 6))
        previous = date.fromordinal(max(1, first.toordinal() - 1))
        self.items = occurrences(*self.store.calendar(), previous, last)
        set_translated(self.range, 'set_label', week_caption(first))
        self.picker.select(self.day)
        for index, (tab, number, mark) in enumerate(self.day_tabs):
            ordinal = first.toordinal() + index
            tab.set_sensitive(ordinal <= date.max.toordinal())
            if not tab.get_sensitive():
                set_translated(number, 'set_text', "—")
                mark.set_opacity(0)
                continue
            day = date.fromordinal(ordinal)
            tab.set_active(day == self.day)
            if day == self.today:
                tab.add_css_class("schedule-current")
            else:
                tab.remove_css_class("schedule-current")
            set_translated(number, 'set_text', str(day.day))
            count = len(day_spans(self.items, day))
            mark.set_opacity(1 if count else 0)
            caption = f"{WEEKDAYS[index]}, {date_label(day)}"
            set_translated(tab, 'set_tooltip_text', tr('{0} · Событий: {1}', caption, count))
            accessible_label(tab, tr('{0}. Событий: {1}', caption, count))
        self.refresh_agenda()
        if self.layout.selected == "week":
            self.week_grid.show_week(first, self.items)

    def refresh_agenda(self):
        now = datetime.now()
        minute = now.hour * 60 + now.minute if self.day == now.date() else None
        spans = [(start, end, item, minute is not None and start <= minute < end)
                 for start, end, item in day_spans(self.items, self.day)]
        signature = (self.day, now.date(), spans)
        if self.agenda_signature == signature:
            return
        self.agenda_signature = signature
        if self.day == self.today:
            heading = tr('Сегодня')
        elif self.day.toordinal() == self.today.toordinal() + 1:
            heading = tr('Завтра')
        else:
            heading = WEEKDAYS[self.day.weekday()]
        set_translated(self.day_title, 'set_text', f"{heading} · {date_label(self.day)}")
        clear(self.rows)
        if not spans:
            text = label(tr('На этот день событий нет.'), "muted")
            text.add_css_class("agenda-empty")
            self.rows.append(text)
        for start, end, item, active in spans:
            self.rows.append(self.event_row(start, end, item, active))

    def event_row(self, start, end, item, active):
        row = Gtk.Button()
        row.add_css_class("agenda-event")
        row.add_css_class(f"event-tier-{item.color}")
        row.connect("clicked", lambda *_: self.edit(item))
        content = box(spacing=13)
        when = label(time_range(start, end), "agenda-time")
        when.set_width_chars(12)
        when.set_valign(Gtk.Align.CENTER)
        content.append(when)
        if marker_key(item.marker):
            content.append(marker_icon(item.marker))
        title = label(item.title, "strong", wrap=True)
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        content.append(title)
        if active:
            badge = label(tr('Сейчас'), "pill")
            badge.set_valign(Gtk.Align.CENTER)
            content.append(badge)
        if item.event.repeat:
            repeat = icon("media-playlist-repeat-symbolic", "muted", 15)
            set_translated(repeat, 'set_tooltip_text', tr('Повторяется'))
            content.append(repeat)
        content.append(icon("go-next-symbolic", "faint", 15))
        row.set_child(content)
        accessible_label(row, f"{time_range(start, end)}. {item.title}")
        return row

    def tick(self):
        if self.today != date.today():
            if self.day == self.today:
                self.day = date.today()
            self.refresh()
        elif self.get_mapped() and self.layout.selected == "day":
            self.refresh_agenda()
        self.week_grid.tick()
