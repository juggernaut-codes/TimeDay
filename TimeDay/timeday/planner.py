"""Calendar and day-by-day planner."""

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
import time
import weakref

from gi.repository import Gtk

from .i18n import language, tr
from .models import MONTH_NAMES, date_label
from .widgets import accessible_label, localized_widget, set_translated, translated_dropdown
from .widgets import Segments, box, button, clear, empty_state, icon_button, label, spacer, truncate, weak_callback


TASKS_PER_PAGE = 50


class CalendarPicker(Gtk.Box):
    def __init__(self, selected, picked):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.selected = selected
        self.month = selected.replace(day=1)
        self.picked = weak_callback(picked)
        self.marked = frozenset()
        self.rendered = None
        head = box(spacing=5)
        self.heading = localized_widget(Gtk.MenuButton, has_frame=False, always_show_arrow=False)
        self.heading.add_css_class("calendar-heading")
        self.heading.set_hexpand(True)
        self.heading.set_halign(Gtk.Align.START)
        set_translated(self.heading, 'set_tooltip_text', tr('Перейти к месяцу и году'))
        self.jump = Gtk.Popover()
        jump_fields = box(True, 8, "picker")
        self.month_field = translated_dropdown(list(MONTH_NAMES))
        accessible_label(self.month_field, tr('Месяц'))
        self.year_field = Gtk.SpinButton.new_with_range(1, 9999, 1)
        self.year_field.set_numeric(True)
        accessible_label(self.year_field, tr('Год'))
        jump_fields.append(self.month_field)
        jump_fields.append(self.year_field)
        jump_fields.append(button(tr('Перейти'), self.jump_to_month, "primary"))
        self.jump.set_child(jump_fields)
        self.heading.set_popover(self.jump)
        head.append(self.heading)
        head.append(spacer())
        change_month = weak_callback(self.change_month)
        head.append(icon_button("go-previous-symbolic", tr('Предыдущий месяц'), lambda: change_month(-1)))
        head.append(icon_button("go-next-symbolic", tr('Следующий месяц'), lambda: change_month(1)))
        self.append(head)
        self.grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=3, row_spacing=3)
        self.append(self.grid)
        self.refresh()

    def change_month(self, delta):
        index = (self.month.year - 1) * 12 + self.month.month - 1 + delta
        if 0 <= index < 9999 * 12:
            self.month = date(index // 12 + 1, index % 12 + 1, 1)
            self.refresh()

    def jump_to_month(self):
        self.year_field.update()
        self.month = date(self.year_field.get_value_as_int(), self.month_field.get_selected() + 1, 1)
        self.jump.popdown()
        self.refresh()

    def select(self, day, reveal=True):
        self.selected = day
        if reveal:
            self.month = day.replace(day=1)
        self.refresh()

    def set_marks(self, tasks):
        self.marked = frozenset(task.day for task in tasks)
        self.refresh()

    def refresh(self):
        today = date.today()
        marks = frozenset(day for day in self.marked if day.year == self.month.year and day.month == self.month.month)
        signature = language(), today, self.selected, self.month, marks
        if self.rendered == signature:
            return
        self.rendered = signature
        set_translated(self.heading, 'set_label', f"{MONTH_NAMES[self.month.month - 1]} {self.month.year}")
        self.year_field.set_value(self.month.year)
        self.month_field.set_selected(self.month.month - 1)
        clear(self.grid)
        picked = self.picked
        for col, name in enumerate((tr('Пн'), tr('Вт'), tr('Ср'), tr('Чт'), tr('Пт'), tr('Сб'), tr('Вс'))):
            item = label(name, "faint")
            item.set_halign(Gtk.Align.CENTER)
            self.grid.attach(item, col, 0, 1, 1)
        for row, week in enumerate(calendar.Calendar().monthdayscalendar(self.month.year, self.month.month), 1):
            for col, number in enumerate(week):
                if not number:
                    continue
                day = self.month.replace(day=number)
                item = button(str(number), lambda value=day: picked(value), "calendar-day")
                accessible_label(item, date_label(day))
                if day == self.selected:
                    item.add_css_class("selected")
                elif day == today:
                    item.add_css_class("today")
                if day in marks:
                    item.add_css_class("has-task")
                self.grid.attach(item, col, row, 1, 1)


class Planner(Gtk.Box):
    def __init__(self, quick_add, edit, toggle, move, delete):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.quick_add, self.edit, self.toggle, self.move, self.delete = quick_add, edit, toggle, move, delete
        self.day = date.today()
        self.last_today = self.day
        self.mode = "today"
        self.tasks = ()
        self.relevant = ()
        self.page = 0
        self.view = None
        self.rendered = None

        self.sections = Segments(
            [("today", tr('Сегодня')), ("tomorrow", tr('Завтра')), ("upcoming", tr('Предстоящие'))],
            "today", self.select_section,
        )
        self.append(self.sections)
        layout = box(spacing=16)
        self.append(layout)
        list_card = box(True, 10, "card")
        list_card.set_hexpand(True)
        list_card.set_valign(Gtk.Align.START)
        layout.append(list_card)
        title = box(spacing=4)
        self.caption = label("", "section-title")
        self.caption.set_hexpand(True)
        title.append(self.caption)
        self.previous = icon_button("go-previous-symbolic", tr('Предыдущий день'), lambda: self.shift(-1))
        self.next = icon_button("go-next-symbolic", tr('Следующий день'), lambda: self.shift(1))
        title.append(self.previous)
        title.append(self.next)
        list_card.append(title)
        self.entry = localized_widget(Gtk.Entry, placeholder_text=tr('Добавить задачу…'), max_length=140, hexpand=True)
        accessible_label(self.entry, tr('Название новой задачи'))
        self.entry.connect("activate", lambda *_: self.add())
        self.entry.connect("changed", lambda *_: self.submit.set_sensitive(bool(self.entry.get_text().strip())))
        self.submit = icon_button("list-add-symbolic", tr('Добавить задачу'), self.add, "outline")
        self.submit.set_sensitive(False)
        self.inline = box(spacing=6)
        self.inline.append(self.entry)
        self.inline.append(self.submit)
        list_card.append(self.inline)
        self.rows = box(True, 6)
        list_card.append(self.rows)
        self.pagination = box(spacing=8)
        self.pagination.set_halign(Gtk.Align.CENTER)
        self.previous_page = icon_button("go-previous-symbolic", tr('Предыдущая страница'), lambda: self.change_page(-1))
        self.page_number = label("", "muted")
        self.next_page = icon_button("go-next-symbolic", tr('Следующая страница'), lambda: self.change_page(1))
        for widget in (self.previous_page, self.page_number, self.next_page):
            self.pagination.append(widget)
        # Keep the controls above the rows so each page starts at the same scroll position.
        list_card.insert_child_after(self.pagination, self.inline)

        calendar_card = box(True, 8, "card")
        calendar_card.set_hexpand(False)
        calendar_card.set_valign(Gtk.Align.START)
        layout.append(calendar_card)
        self.calendar = CalendarPicker(self.day, self.select_day)
        calendar_card.append(self.calendar)
        self.refresh()

    @property
    def edit_day(self):
        return self.day if self.mode != "upcoming" else date.today() + timedelta(days=2)

    def add(self):
        text = self.entry.get_text().strip()
        if text:
            self.quick_add(self.day, text)
            set_translated(self.entry, 'set_text', "")

    def shift(self, delta):
        try:
            day = self.day + timedelta(days=delta)
        except OverflowError:
            return
        self.select_day(day)

    def select_day(self, day):
        today = date.today()
        self.mode = "today" if day == today else "tomorrow" if day == today + timedelta(days=1) else "date"
        self.day = day
        self.sections.select(self.mode if self.mode != "date" else None)
        self.refresh()

    def select_section(self, mode):
        self.mode = mode
        offset = {"today": 0, "tomorrow": 1, "upcoming": 2}[mode]
        self.day = date.today() + timedelta(days=offset)
        self.refresh()

    def update(self, tasks):
        changed = tasks is not self.tasks
        if changed:
            self.tasks = tasks
            self.calendar.set_marks(tasks)
        if date.today() != self.last_today:
            self.last_today = date.today()
            if self.mode != "date":
                self.select_section(self.mode)
            else:
                self.calendar.refresh()
        self.refresh(changed)

    def change_page(self, offset):
        self.page += offset
        self.refresh()

    def refresh(self, changed=False):
        if self.calendar.selected != self.day:
            self.calendar.select(self.day)
        upcoming = self.mode == "upcoming"
        self.inline.set_visible(not upcoming)
        self.previous.set_visible(not upcoming)
        self.next.set_visible(not upcoming)
        set_translated(self.caption, 'set_text', tr('Предстоящие задачи') if upcoming else date_label(self.day))
        view = self.mode, self.day
        if view != self.view:
            self.view = view
            self.page = 0
            changed = True
        if changed:
            self.relevant = [task for task in self.tasks if task.day >= self.day] if upcoming else [
                task for task in self.tasks if task.day == self.day
            ]
        pages = max(1, (len(self.relevant) + TASKS_PER_PAGE - 1) // TASKS_PER_PAGE)
        self.page = max(0, min(self.page, pages - 1))
        self.pagination.set_visible(pages > 1)
        self.previous_page.set_sensitive(self.page > 0)
        self.next_page.set_sensitive(self.page < pages - 1)
        set_translated(self.page_number, 'set_text', tr('Страница {0} из {1}', self.page + 1, pages))
        start = self.page * TASKS_PER_PAGE
        relevant = self.relevant[start:start + TASKS_PER_PAGE]
        now = time.time()
        signature = (
            self.mode, self.day,
            [(task, not task.done and task.due is not None and task.due <= now) for task in relevant],
        )
        if signature == self.rendered:
            return
        self.rendered = signature
        clear(self.rows)
        if not relevant:
            self.rows.append(empty_state(
                "view-list-symbolic", tr('Нет запланированных задач'),
                tr('Выберите день или добавьте задачу.') if upcoming else tr('Добавьте задачу на этот день.'),
            ))
            return
        groups = defaultdict(list)
        for task in relevant:
            groups[task.day].append(task)
        for day, tasks in groups.items():
            if upcoming:
                self.rows.append(button(
                    date_label(day), lambda value=day: self.select_day(value), "day-group",
                ))
            for task in tasks:
                self.rows.append(task_row(task, now, self.toggle, self.edit, self.move, self.delete))


def task_row(task, now, toggle, edit, move, delete, compact=False):
    row = box(spacing=10, css="task-row")
    checkbox = localized_widget(Gtk.CheckButton, active=task.done)
    set_translated(checkbox, 'set_tooltip_text', tr('Вернуть в список') if task.done else tr('Отметить выполненной'))
    accessible_label(checkbox, tr('Выполнить: {0}', task.title))
    checkbox.set_valign(Gtk.Align.CENTER)
    checkbox.connect("toggled", lambda item: toggle(task, item.get_active()))
    row.append(checkbox)
    body = box(True, 4)
    body.set_hexpand(True)
    body.set_valign(Gtk.Align.CENTER)
    title = truncate(label(task.title, "done" if task.done else "strong"), 32)
    set_translated(title, 'set_tooltip_text', task.title)
    body.append(title)
    if not compact and task.description:
        body.append(truncate(label(task.description.splitlines()[0], "muted"), 44))
    if task.due is not None:
        when = datetime.fromtimestamp(task.due)
        late = not task.done and task.due <= now
        body.append(label(f"{tr('Просрочено · ') if late else ''}{when:%H:%M}", "overdue" if late else "muted"))
    row.append(body)
    menu = localized_widget(Gtk.MenuButton, icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
    set_translated(menu, 'set_tooltip_text', tr('Действия с задачей'))
    popup = Gtk.Popover()
    menu_reference = weakref.ref(menu)
    actions = box(True, 2, "task-menu")
    for caption, action, css in (
        (tr('Редактировать'), edit, None), (tr('Перенести'), move, None), (tr('Удалить'), delete, "danger"),
    ):
        def invoke(callback=action):
            owner = menu_reference()
            if owner is not None:
                owner.get_popover().popdown()
            callback(task)
        actions.append(button(caption, invoke, css))
    popup.set_child(actions)
    menu.set_popover(popup)
    row.append(menu)
    return row
