"""Today dashboard and application activity analysis."""

import hashlib
from datetime import date, datetime, time, timedelta

from gi.repository import Gdk, Gtk, Pango

from .i18n import tr
from .events import time_range
from .models import MIN_APP_SECONDS, MONTHS, date_label, duration_label, period_bins, summarize
from .runtime import is_runtime_app
from .widgets import accessible_label, set_translated, translated_dropdown
from .widgets import ActivityChart, Segments, box, button, clear, icon, label, spacer, truncate


PERIODS = [("day", "День"), ("week", "Неделя"), ("month", "Месяц")]
COLORS = (
    "#a88cf7", "#60a5fa", "#54cdb0", "#e7aa65", "#f085ad", "#7bc7f1", "#b9cf70",
    "#f1957b", "#c3a2fe", "#7bc4d1", "#93a0ed", "#f1cc74", "#d680db",
)


def app_color(name):
    return COLORS[int.from_bytes(hashlib.blake2s(name.encode(), digest_size=2).digest(), "big") % len(COLORS)]


def source(cr, value, alpha=1.0):
    color = Gdk.RGBA()
    color.parse(value)
    cr.set_source_rgba(color.red, color.green, color.blue, alpha)


def page_heading(title, description, action=None, eyebrow=None):
    header = box(spacing=12, css="hero")
    content = box(True, 5)
    content.set_hexpand(True)
    if eyebrow is not None:
        content.append(eyebrow)
    content.append(label(title, "title"))
    if description:
        content.append(label(description, "subtitle", wrap=True))
    header.append(content)
    if action:
        action.set_valign(Gtk.Align.CENTER)
        header.append(action)
    return header


def section_heading(title, action=None):
    row = box(spacing=8)
    caption = label(title, "section-title")
    caption.set_hexpand(True)
    row.append(caption)
    if action:
        row.append(action)
    return row


def short_date(day):
    return f"{day.day} {MONTHS[day.month - 1]}"


class ColorStrip(Gtk.DrawingArea):
    def __init__(self, timeline=False):
        super().__init__(hexpand=True, content_height=50 if timeline else 18)
        self.timeline = timeline
        self.parts = []
        self.ticks = []
        self.selected = None
        self.set_draw_func(self.draw)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self.tooltip)

    def distribution(self, ranked):
        total = sum(seconds for _, seconds in ranked)
        self.parts = []
        offset = 0
        for name, seconds in ranked:
            end = offset + seconds / total
            self.parts.append((offset, end, name, duration_label(seconds)))
            offset = end
        self.queue_draw()

    def history(self, rows, start, end, selected=None):
        span = end - start
        day = datetime.fromtimestamp(start).date()
        self.ticks = [
            ((datetime.combine(day, time(hour)).timestamp() - start) / span, f"{hour:02d}:00")
            for hour in (0, 6, 12, 18)
        ] + [(1, "24:00")]
        self.selected = selected
        self.parts = [
            ((opened - start) / span, (closed - start) / span, name,
             f"{datetime.fromtimestamp(opened):%H:%M}–{datetime.fromtimestamp(closed):%H:%M}"
             f" · {duration_label(closed - opened)}")
            for name, opened, closed in rows
        ]
        self.queue_draw()

    def tooltip(self, _widget, x, y, keyboard, tooltip):
        if keyboard or y > (24 if self.timeline else 16):
            return False
        width = self.get_width()
        if not width:
            return False
        for start, end, name, detail in reversed(self.parts):
            if start <= x / width < end:
                set_translated(tooltip, 'set_text', f"{name} · {detail}")
                return True
        return False

    def draw(self, _area, cr, width, _height):
        height = 23 if self.timeline else 14
        radius = 4
        if width < 2 * radius:
            return
        cr.save()
        cr.arc(radius, radius, radius, 3.1416, 4.7124)
        cr.arc(width - radius, radius, radius, 4.7124, 6.2832)
        cr.arc(width - radius, height - radius, radius, 0, 1.5708)
        cr.arc(radius, height - radius, radius, 1.5708, 3.1416)
        cr.close_path()
        cr.clip()
        source(cr, "#242427")
        cr.paint()
        for start, end, name, _detail in self.parts:
            opacity = .34 if self.selected and self.selected != name else .94
            source(cr, app_color(name), opacity)
            pixels = (end - start) * width
            cr.rectangle(start * width, 0, max(.4, pixels - (.85 if pixels > 5 else 0)), height)
            cr.fill()
        cr.restore()
        if self.timeline:
            cr.select_font_face("DejaVu Sans")
            cr.set_font_size(10)
            source(cr, "#a3a3ab")
            for fraction, tick in self.ticks:
                text_width = cr.text_extents(tick).width
                x = (width - 1) * fraction
                cr.move_to(max(0, min(x - text_width / 2, width - text_width - 1)), 42)
                cr.show_text(tick)


class AppRow(Gtk.Button):
    def __init__(self, name, icons, open_app):
        super().__init__()
        self.add_css_class("ranking-row")
        self.connect("clicked", lambda *_: open_app(name))
        row = box(spacing=9)
        image = icons.image(name, 21)
        row.append(image)
        title = truncate(label(name, "strong"), 28)
        title.set_hexpand(True)
        set_translated(title, 'set_tooltip_text', name)
        row.append(title)
        swatch = Gtk.DrawingArea(content_width=8, content_height=8, valign=Gtk.Align.CENTER)
        swatch.set_draw_func(lambda _a, cr, _w, _h: AppRow.draw_swatch(cr, name))
        row.append(swatch)
        self.duration = label("", "reading")
        row.append(self.duration)
        self.set_child(row)

    @staticmethod
    def draw_swatch(cr, name):
        source(cr, app_color(name))
        cr.arc(4, 4, 3.3, 0, 6.29)
        cr.fill()

    def update(self, seconds, selected=False):
        set_translated(self.duration, 'set_text', duration_label(seconds))
        if selected:
            self.add_css_class("row-selected")
        else:
            self.remove_css_class("row-selected")


class Ranking(Gtk.Box):
    def __init__(self, icons, open_app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.icons, self.open_app = icons, open_app
        self.rows = {}

    def update(self, visible, selected=None):
        if list(self.rows) != [name for name, _ in visible] or self.get_first_child() is None:
            clear(self)
            self.rows = {}
            if not visible:
                self.append(label(tr('За этот период пока нет записей.'), "muted"))
            for name, _seconds in visible:
                item = AppRow(name, self.icons, self.open_app)
                self.append(item)
                self.rows[name] = item
        for name, seconds in visible:
            self.rows[name].update(seconds, name == selected)


class ActivityPanel(Gtk.Box):
    def __init__(self, store, icons, accent):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.store, self.icons, self.accent = store, icons, accent
        self.period = "day"
        self.metric = "focus"
        self.open_windows = None
        self.running = None
        self.selected = None
        self.timeline_focus = None
        self.days = []
        self.background = None

        controls = box(spacing=10)
        self.period_control = Segments([(key, tr(title)) for key, title in PERIODS], self.period, self.change_period)
        controls.append(self.period_control)
        controls.append(spacer())
        self.metric_control = Segments(
            [("focus", tr('В фокусе')), ("runtime", tr('Запущено'))], self.metric, self.change_metric,
        )
        controls.append(self.metric_control)
        self.range = label("", "muted")
        controls.append(self.range)
        self.append(controls)

        distribution = box(True, 10, "card")
        summary = box(spacing=8)
        self.distribution_title = label(tr('Распределение времени'), "section-title")
        summary.append(self.distribution_title)
        summary.append(spacer())
        self.total = label("", "reading")
        summary.append(self.total)
        distribution.append(summary)
        self.distribution = ColorStrip()
        distribution.append(self.distribution)
        self.append(distribution)

        columns = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        columns.set_resize_start_child(True)
        columns.set_resize_end_child(True)
        columns.set_shrink_start_child(False)
        columns.set_shrink_end_child(False)
        self.append(columns)

        left = box(True, 12)
        left.set_size_request(300, -1)
        columns.set_start_child(left)
        ranks = box(True, 9, "card")
        self.ranking_title = label(tr('Популярные приложения'), "section-title")
        ranks.append(self.ranking_title)
        self.ranked = Ranking(icons, self.select_app)
        ranks.append(self.ranked)
        left.append(ranks)

        right = box(True, 12)
        right.set_size_request(275, -1)
        columns.set_end_child(right)
        timeline = box(True, 8, "card")
        head = box(spacing=8)
        self.timeline_title = label(tr('Хронология дня'), "section-title", wrap=True)
        self.timeline_title.set_wrap_mode(Pango.WrapMode.WORD)
        self.timeline_title.set_hexpand(True)
        head.append(self.timeline_title)
        self.day_filter = translated_dropdown([])
        accessible_label(self.day_filter, tr('День на временной шкале'))
        self.day_handler = self.day_filter.connect("notify::selected", lambda *_: self.refresh_timeline())
        head.append(self.day_filter)
        timeline.append(head)
        self.timeline = ColorStrip(timeline=True)
        timeline.append(self.timeline)
        self.timeline_note = label("", "muted", wrap=True)
        timeline.append(self.timeline_note)
        right.append(timeline)

        self.detail = box(True, 9, "card")
        right.append(self.detail)
        line = box(spacing=8)
        self.detail_icon = Gtk.Image(pixel_size=25)
        line.append(self.detail_icon)
        self.detail_title = truncate(label("", "section-title"), 26)
        self.detail_title.set_hexpand(True)
        line.append(self.detail_title)
        self.detail.append(line)
        self.detail_total = label("", "metric-value")
        self.detail.append(self.detail_total)
        self.detail_note = label("", "muted", wrap=True)
        self.detail.append(self.detail_note)
        self.chart = ActivityChart(103)
        self.detail.append(self.chart)

        background_panel = box(True, 8, "card")
        self.background_title = label(tr('Открыты в фоне сейчас'), "section-title")
        background_panel.append(self.background_title)
        self.background_note = label("", "muted", wrap=True)
        background_panel.append(self.background_note)
        self.background_rows = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, row_spacing=5, column_spacing=5,
            min_children_per_line=1, max_children_per_line=7,
        )
        background_panel.append(self.background_rows)
        left.append(background_panel)

    def change_metric(self, metric):
        if self.metric == metric:
            return
        self.metric = metric
        self.metric_control.select(metric)
        self.timeline_focus = None
        runtime = metric == "runtime"
        set_translated(self.distribution_title, 'set_text', tr('Суммарное время работы') if runtime else tr('Распределение времени'))
        set_translated(self.total, 'set_tooltip_text', tr('Время складывается по программам, которые могут работать одновременно.')
                                    if runtime else None)
        set_translated(self.ranking_title, 'set_text', tr('По длительности работы') if runtime else tr('Популярные приложения'))
        set_translated(self.timeline_title, 'set_text', tr('Когда приложение работало') if runtime else tr('Хронология дня'))
        self.refresh_background()
        self.refresh(animate=True)

    def records(self, edges):
        if self.metric != "runtime":
            return self.icons.samples(self.store.activity(edges[0], edges[-1]))
        return [row for row in self.icons.samples(self.store.runtime(edges[0], edges[-1]))
                if is_runtime_app(row[0], self.icons.runtime_excluded)]

    def change_period(self, period):
        self.period_control.select(period)
        self.period = period
        self.refresh(animate=True)

    def select_app(self, name):
        self.selected = name
        self.timeline_focus = name if name != self.timeline_focus else None
        self.refresh(animate=True)

    def refresh(self, animate=False):
        edges, captions = period_bins(self.period)
        rows = self.records(edges)
        _all, per_app, ranked = summarize(rows, edges, MIN_APP_SECONDS)
        self.visible_apps = set(per_app)
        self.refresh_background()
        if self.selected not in per_app:
            self.selected = ranked[0][0] if ranked else None
            self.timeline_focus = None
        seconds = sum(seconds for _, seconds in ranked)
        first = datetime.fromtimestamp(edges[0]).date()
        last = datetime.fromtimestamp(edges[-1] - 1).date()
        set_translated(self.range, 'set_text', date_label(first) if first == last else f"{short_date(first)} – {short_date(last)}")
        set_translated(self.total, 'set_text', duration_label(seconds))
        self.distribution.distribution(ranked)
        self.ranked.update(ranked, self.selected)
        self.detail.set_visible(self.selected is not None)
        if self.selected:
            values = per_app[self.selected]
            set_translated(self.detail_title, 'set_text', self.selected)
            self.icons.bind_image(self.detail_icon, self.selected)
            set_translated(self.detail_total, 'set_text', duration_label(sum(values)))
            share = sum(values) / seconds * 100
            set_translated(self.detail_note, 'set_text', tr('{0:.1f}% времени', share))
            self.chart.set_data(values, captions, app_color(self.selected), animate)
        days = [last - timedelta(days=delta) for delta in range((last - first).days + 1)]
        if days != self.days:
            self.day_filter.handler_block(self.day_handler)
            index = self.day_filter.get_selected()
            previous = self.days[index] if index < len(self.days) else None
            self.days = days
            self.day_filter.set_model(Gtk.StringList.new([short_date(day) for day in days]))
            self.day_filter.set_selected(days.index(previous) if previous in days else 0)
            self.day_filter.handler_unblock(self.day_handler)
        self.day_filter.set_visible(len(days) > 1)
        self.refresh_timeline()

    def refresh_timeline(self):
        if not self.days:
            return
        index = min(self.day_filter.get_selected(), len(self.days) - 1)
        edges, _ = period_bins("day", self.days[index])
        rows = [row for row in self.records(edges) if row[0] in getattr(self, "visible_apps", set())]
        if self.metric == "runtime":
            rows = [row for row in rows if row[0] == self.selected]
        self.timeline.history(rows, edges[0], edges[-1], self.timeline_focus)
        set_translated(self.timeline_note, 'set_text', tr('За этот день нет записей.'))
        self.timeline_note.set_visible(not rows)

    def refresh_presence(self, open_windows, running):
        self.open_windows, self.running = open_windows, running
        self.refresh_background()

    def refresh_background(self):
        runtime = self.metric == "runtime"
        names = self.running if runtime else self.open_windows
        if names is not None:
            names = set(names) & getattr(self, "visible_apps", set())
        signature = (self.metric, None if names is None else tuple(sorted(names, key=str.casefold)))
        if self.background == signature:
            return
        self.background = signature
        clear(self.background_rows)
        set_translated(self.background_title, 'set_text', tr('Запущены сейчас') if runtime else tr('Открыты в фоне сейчас'))
        self.background_rows.set_visible(bool(names))
        if runtime:
            message = (tr('Включая свёрнутые приложения и программы без окна.') if names
                       else tr('Нет данных о запущенных приложениях.'))
        elif names is None:
            message = tr('Список открытых окон доступен при включённом мониторинге X11.')
        elif not names:
            message = tr('Сейчас нет открытых фоновых окон.')
        else:
            message = tr('Окна вне фокуса · время не учитывается.')
        set_translated(self.background_note, 'set_text', message)
        for name in signature[1] or ():
            chip = box(spacing=6, css="app-chip")
            chip.append(self.icons.image(name, 15))
            chip.append(truncate(label(name), 24))
            self.background_rows.append(chip)


class TodayContent(Gtk.Box):
    def __init__(self, icons, new_event, open_event, open_planner, open_stats, open_app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.new_event, self.open_event = new_event, open_event
        self.event = None
        self.next_signature = None

        next_card = box(spacing=13, css="next-event")
        next_card.append(icon("x-office-calendar-symbolic", "event-emblem", 21))
        event_text = box(True, 4)
        event_text.set_hexpand(True)
        event_text.append(label(tr('Ближайшее событие'), "next-caption"))
        self.event_title = truncate(label("", "event-title"), 38)
        event_text.append(self.event_title)
        self.event_time = label("", "muted", wrap=True)
        event_text.append(self.event_time)
        next_card.append(event_text)
        self.event_action = button(tr('Расписание'), self.go_event, "outline")
        self.event_action.set_valign(Gtk.Align.CENTER)
        next_card.append(self.event_action)
        self.append(next_card)

        columns = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        columns.set_resize_start_child(True)
        columns.set_resize_end_child(True)
        columns.set_shrink_start_child(False)
        columns.set_shrink_end_child(False)
        self.append(columns)

        task_card = box(True, 9, "card")
        task_card.set_size_request(265, -1)
        task_card.set_valign(Gtk.Align.START)
        task_card.append(section_heading(tr('Задачи'), button(tr('Планировщик'), open_planner, "ghost")))
        self.task_count = label("", "muted")
        task_card.append(self.task_count)
        self.tasks = box(True)
        task_card.append(self.tasks)
        columns.set_start_child(task_card)

        app_card = box(True, 9, "card")
        app_card.set_size_request(290, -1)
        app_card.set_valign(Gtk.Align.START)
        app_card.append(section_heading(tr('Время в приложениях'), button(tr('Статистика'), open_stats, "ghost")))
        self.activity_total = label("", "metric-value")
        app_card.append(self.activity_total)
        self.distribution = ColorStrip()
        app_card.append(self.distribution)
        self.ranking = Ranking(icons, open_app)
        app_card.append(self.ranking)
        columns.set_end_child(app_card)

    def go_event(self):
        if self.event:
            self.open_event(self.event)
        else:
            self.new_event()

    def update_event(self, event):
        if self.next_signature == (event, date.today()):
            return
        self.event = event
        self.next_signature = (event, date.today())
        if event:
            today = date.today()
            when = tr('Сегодня') if event.day == today else (
                short_date(event.day) if event.day.year == today.year else date_label(event.day)
            )
            set_translated(self.event_title, 'set_text', event.title)
            set_translated(self.event_title, 'set_tooltip_text', event.title)
            set_translated(self.event_time, 'set_text', f"{when} · {time_range(event.start, event.end)}")
            set_translated(self.event_action, 'set_label', tr('Расписание'))
        else:
            set_translated(self.event_title, 'set_text', tr('Нет предстоящих событий'))
            set_translated(self.event_title, 'set_tooltip_text', None)
            set_translated(self.event_action, 'set_label', tr('Добавить событие'))
        self.event_time.set_visible(bool(event))

    def update_activity(self, ranked):
        seconds = sum(seconds for _, seconds in ranked)
        set_translated(self.activity_total, 'set_text', duration_label(seconds))
        self.distribution.distribution(ranked)
        self.ranking.update(ranked[:3])
