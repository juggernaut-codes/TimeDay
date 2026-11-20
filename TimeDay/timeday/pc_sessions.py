"""Monthly view of the time the computer was awake."""

from datetime import date

from gi.repository import Gtk

from .i18n import tr
from .models import MONTH_NAMES, MONTHS, date_label, duration_label
from .uptime import month_dates, shift_month
from .widgets import set_translated
from .widgets import ActivityChart, box, button, clear, icon_button, label, spacer


class PCSessions(Gtk.Box):
    def __init__(self, uptime, accent, request_history, history_pending):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.uptime, self.accent = uptime, accent
        self.request_history, self.history_pending = request_history, history_pending
        self.last_today = date.today()
        self.month = self.last_today.replace(day=1)
        self.rendered = None

        toolbar = box(spacing=7)
        self.previous = icon_button(
            "go-previous-symbolic", tr('Предыдущий месяц'), lambda: self.move(-1), "outline",
        )
        self.next = icon_button("go-next-symbolic", tr('Следующий месяц'), lambda: self.move(1), "outline")
        toolbar.append(self.previous)
        toolbar.append(self.next)
        self.period = label("", "section-title")
        self.period.set_margin_start(5)
        toolbar.append(self.period)
        toolbar.append(spacer())
        self.current = button(tr('Текущий месяц'), self.go_current, "outline")
        toolbar.append(self.current)
        self.append(toolbar)

        summary = box(spacing=12, css="card")
        summary.set_homogeneous(True)
        self.total = self.metric(summary, tr('Всего за месяц'), prominent=True)
        self.active_days = self.metric(summary, tr('Дней с данными'))
        self.average = self.metric(summary, tr('Среднее за день'))
        set_translated(self.average, 'set_tooltip_text', tr('По дням, за которые есть данные'))
        self.maximum = self.metric(summary, tr('Самый долгий день'))
        self.peak_date = label("", "faint")
        self.maximum.get_parent().append(self.peak_date)
        self.append(summary)

        results = box(spacing=12)
        self.append(results)
        plot = box(True, 8, "card")
        plot.set_hexpand(True)
        plot.append(label(tr('Работа ПК по дням'), "section-title"))
        self.chart = ActivityChart(228)
        plot.append(self.chart)
        legend = box(spacing=14)
        legend.append(label(tr('Зелёный — работа ПК'), "pc-active-legend"))
        legend.append(label(tr('Оранжевый — неактивность'), "pc-idle-legend"))
        plot.append(legend)
        plot.append(label(tr('Неактивность: 10 минут без ввода. Воспроизведение видео и музыки учитывается по системному медиастатусу. История неактивности записывается с этого обновления.'), "faint", wrap=True))
        self.empty = label("", "muted", wrap=True)
        self.empty.add_css_class("agenda-empty")
        plot.append(self.empty)
        results.append(plot)

        self.days_card = box(True, 8, "card")
        self.days_card.set_hexpand(False)
        self.days_card.set_valign(Gtk.Align.START)
        self.days_card.set_size_request(230, -1)
        self.days_card.append(label(tr('Итоги по датам'), "section-title"))
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, max_content_height=220, propagate_natural_height=True,
        )
        self.rows = box(True)
        scroller.set_child(self.rows)
        self.days_card.append(scroller)
        results.append(self.days_card)
        self.refresh()

    @staticmethod
    def metric(parent, caption, prominent=False):
        field = box(True, 5)
        field.append(label(caption, "muted", wrap=True))
        value = label("—", "pc-month-value", wrap=True)
        if prominent:
            value.add_css_class("pc-month-total")
        field.append(value)
        parent.append(field)
        return value

    def move(self, offset):
        self.select_month(shift_month(self.month, offset))

    def go_current(self):
        self.select_month(date.today())

    def select_month(self, day):
        self.month = min(day.replace(day=1), date.today().replace(day=1))
        self.request_history(self.month)
        self.refresh(animate=True)

    def refresh(self, animate=False):
        today = date.today()
        current_month = today.replace(day=1)
        previous_month = self.last_today.replace(day=1)
        if self.month > current_month or (previous_month != current_month and self.month == previous_month):
            self.month = current_month
            self.request_history(self.month)
        self.last_today = today
        set_translated(self.period, 'set_text', f"{MONTH_NAMES[self.month.month - 1]} {self.month.year}")
        self.previous.set_sensitive(shift_month(self.month, -1) != self.month)
        self.next.set_sensitive(self.month < current_month)
        self.current.set_sensitive(self.month != current_month)

        days = month_dates(self.month, today)
        totals = self.uptime.totals(days)
        idle_totals = self.uptime.idle_totals(days)
        idle_totals = {day: min(totals[day], idle_totals[day]) for day in days}
        populated = [day for day in days if totals[day] > 0]
        total = sum(totals.values())
        peak = max(populated, key=totals.get, default=None)
        set_translated(self.total, 'set_text', duration_label(total) if populated else "—")
        set_translated(self.active_days, 'set_text', str(len(populated)))
        set_translated(self.average, 'set_text', duration_label(total / len(populated)) if populated else "—")
        set_translated(self.maximum, 'set_text', duration_label(totals[peak]) if peak else "—")
        set_translated(self.peak_date, 'set_text', f"{peak.day} {MONTHS[peak.month - 1]}" if peak else "")
        self.peak_date.set_visible(peak is not None)
        self.chart.set_visible(bool(populated))
        self.days_card.set_visible(bool(populated))
        self.empty.set_visible(not populated)
        if not populated:
            set_translated(self.empty, 'set_text', tr('Загружаем историю…') if self.history_pending(self.month)
                                else tr('За этот месяц нет записей о работе ПК.'))
        else:
            self.chart.set_data(
                [totals[day] for day in days], [str(day.day) for day in days], "#76d7a0", animate,
                tooltip_labels=[date_label(day) for day in days],
                tooltip_values=[tr('Всего: {0} · Неактивность: {1}', duration_label(totals[day]), duration_label(idle_totals[day]))
                                if totals[day] else tr('Нет данных') for day in days],
                idle_values=[idle_totals[day] for day in days],
            )

        signature = tuple((day, duration_label(totals[day]), duration_label(idle_totals[day])) for day in populated)
        if self.rendered != signature:
            self.rendered = signature
            clear(self.rows)
            for day, duration, idle in reversed(signature):
                row = box(spacing=8, css="pc-date-row")
                name = f"{day.day} {MONTHS[day.month - 1]}"
                row.append(label(name, "strong" if day == today else "muted"))
                row.append(spacer())
                durations = box(True, 2)
                durations.append(label(duration, "reading"))
                durations.append(label(tr('Неактивность: {0}', idle), "pc-idle-legend"))
                row.append(durations)
                self.rows.append(row)
