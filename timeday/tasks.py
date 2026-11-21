"""Simple task editor and date selection dialogs."""

from datetime import datetime, time

from gi.repository import Gtk

from .i18n import tr
from .models import date_label
from .planner import CalendarPicker
from .widgets import accessible_label, localized_widget, set_translated
from .widgets import box, button, label, spacer, weak_callback


def deadline(day, value):
    # Validate the local time too: clocks may skip an hour on a DST transition.
    local = datetime.combine(day, value)
    stamp = int(local.timestamp())
    if local != datetime.fromtimestamp(stamp):
        raise ValueError(tr('Это время недоступно в выбранный день. Укажите другое время.'))
    return stamp


class TaskEditor(Gtk.Window):
    def __init__(self, parent, task, day, save):
        super().__init__(title=tr('Редактировать задачу') if task else tr('Новая задача'),
                         transient_for=parent, modal=True, default_width=455, resizable=False)
        set_translated(self, "set_title", tr('Редактировать задачу') if task else tr('Новая задача'))
        self.task = task
        self.day = task.day if task else day
        self.save = save
        content = box(True, 12, "dialog-content")
        self.set_child(content)

        content.append(label(tr('Название'), "strong"))
        self.title_entry = localized_widget(Gtk.Entry, placeholder_text=tr('Что нужно сделать?'), max_length=140)
        accessible_label(self.title_entry, tr('Название задачи'))
        set_translated(self.title_entry, 'set_text', task.title if task else "")
        content.append(self.title_entry)

        content.append(label(tr('Дата'), "strong"))
        self.date_button = localized_widget(Gtk.MenuButton, label=date_label(self.day), direction=Gtk.ArrowType.DOWN)
        self.date_button.set_halign(Gtk.Align.START)
        self.date_popup = Gtk.Popover()
        self.picker = CalendarPicker(self.day, self.select_day)
        self.picker.add_css_class("picker")
        self.date_popup.set_child(self.picker)
        self.date_button.set_popover(self.date_popup)
        content.append(self.date_button)

        self.deadline = localized_widget(Gtk.CheckButton, label=tr('Указать время дедлайна'))
        self.deadline.set_active(bool(task and task.due is not None))
        content.append(self.deadline)
        due_time = datetime.fromtimestamp(task.due).strftime("%H:%M") if task and task.due is not None else "18:00"
        self.time = localized_widget(Gtk.Entry, text=due_time, width_chars=7, max_length=5)
        self.time.set_halign(Gtk.Align.START)
        accessible_label(self.time, tr('Время дедлайна, часы и минуты'))
        self.time.set_visible(self.deadline.get_active())
        show_time = weak_callback(self.time.set_visible, self.time)
        self.deadline.connect("toggled", lambda item: show_time(item.get_active()))
        content.append(self.time)

        content.append(label(tr('Описание · необязательно'), "muted"))
        self.description = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        accessible_label(self.description, tr('Описание задачи'))
        set_translated(self.description.get_buffer(), 'set_text', task.description if task else "")
        viewport = Gtk.ScrolledWindow(min_content_height=76, max_content_height=108, propagate_natural_height=True)
        viewport.set_child(self.description)
        content.append(viewport)
        self.error = label("", "error-text", wrap=True)
        self.error.set_visible(False)
        content.append(self.error)

        actions = box(spacing=8)
        actions.append(spacer())
        actions.append(button(tr('Отмена'), weak_callback(self.close, self)))
        save_button = button(tr('Сохранить') if task else tr('Добавить'), self.commit, "primary")
        actions.append(save_button)
        content.append(actions)
        self.set_default_widget(save_button)
        self.title_entry.set_activates_default(True)
        self.title_entry.grab_focus()

    def select_day(self, day):
        self.day = day
        self.picker.select(day)
        set_translated(self.date_button, 'set_label', date_label(day))
        self.date_popup.popdown()

    def commit(self):
        try:
            due = None
            if self.deadline.get_active():
                try:
                    value = datetime.strptime(self.time.get_text(), "%H:%M").time()
                except ValueError as exc:
                    raise ValueError(tr('Укажите время в формате ЧЧ:ММ.')) from exc
                due = deadline(self.day, value)
            buffer = self.description.get_buffer()
            description = buffer.get_text(*buffer.get_bounds(), False)
            self.save(self.title_entry.get_text(), description.strip(), self.day, due, self.task)
        except (ValueError, OverflowError, OSError) as exc:
            set_translated(self.error, 'set_text', str(exc))
            self.error.set_visible(True)
            return
        self.close()


class MoveTask(Gtk.Window):
    def __init__(self, parent, task, move):
        super().__init__(title=tr('Перенести задачу'), transient_for=parent, modal=True, default_width=340)
        set_translated(self, "set_title", tr('Перенести задачу'))
        self.day = task.day
        self.task = task
        self.move = move
        content = box(True, 12, "dialog-content")
        self.set_child(content)
        content.append(label(task.title, "section-title", wrap=True))
        self.picker = CalendarPicker(self.day, self.select)
        content.append(self.picker)
        self.error = label("", "error-text", wrap=True)
        self.error.set_visible(False)
        content.append(self.error)
        row = box(spacing=6)
        row.append(spacer())
        row.append(button(tr('Отмена'), weak_callback(self.close, self)))
        self.confirm = button(tr('Перенести'), self.commit, "primary")
        self.confirm.set_sensitive(False)
        row.append(self.confirm)
        content.append(row)

    def select(self, day):
        self.day = day
        self.picker.select(day)
        self.confirm.set_sensitive(day != self.task.day)

    def commit(self):
        try:
            due = None
            if self.task.due is not None:
                when = datetime.fromtimestamp(self.task.due)
                due = deadline(self.day, time(when.hour, when.minute, when.second))
            self.move(self.task.title, self.task.description, self.day, due, self.task)
        except (ValueError, OverflowError, OSError) as exc:
            set_translated(self.error, 'set_text', str(exc))
            self.error.set_visible(True)
            return
        self.close()
