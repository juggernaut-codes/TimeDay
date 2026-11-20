"""Application lifecycle and desktop shell."""

import logging
import os
import signal
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from .i18n import LANGUAGES, language, normalize_language, set_language, tr
from . import APP_ICON, APP_ID, APP_VERSION, DEFAULT_ACCENT
from .activity import ActivityRecorder, X11Monitor
from .app_icons import AppIcons
from .backup import backup_database
from .dashboard import ActivityPanel, TodayContent, page_heading
from .desktop import apply_integrations, autostart_enabled, migrate_autostart, set_autostart
from .events import next_occurrence
from .models import MIN_APP_SECONDS, WEEKDAYS, date_label, duration_label, period_bins, summarize
from .pc_sessions import PCSessions
from .presence import PresenceRecorder
from .media import MediaPlayback
from .store import Store, database_path
from .planner import Planner, task_row
from .runtime import ProcessApps, RuntimeRecorder
from .schedule import EventEditor, ScheduleView
from .tasks import MoveTask, TaskEditor
from .timers import Timers
from .tray import Tray
from .uptime import AwakePoint, ComputerUptime, journal_history, shift_month
from .widgets import accessible_label, localized_widget, refresh_translations, set_translated, translated_dropdown
from .widgets import box, button, clear, icon, label, spacer, truncate
from .welcome import WelcomeWindow, purpose_page
from .windowing import center_on_first_map


DEFAULT_COLOR = "Белый"
NUMBER_SETTINGS = {"idle_minutes": (5, 1, 60), "reminder_minutes": (15, 0, 1440)}
PALETTE = {
    DEFAULT_COLOR: DEFAULT_ACCENT,
    "Лавандовый": "#a78bfa", "Лазурный": "#80b7f5", "Мятный": "#76d7b0",
    "Розовый": "#f49dc8",
}
PAGES = {
    "overview": ("Сегодня", "view-grid-symbolic"),
    "tasks": ("Планировщик", "view-list-symbolic"),
    "schedule": ("Расписание", "x-office-calendar-symbolic"),
    "activity": ("Статистика", "utilities-system-monitor-symbolic"),
    "sessions": ("Сессии ПК", "computer-symbolic"),
    "timers": ("Таймер", "preferences-system-time-symbolic"),
    "settings": ("Настройки", "emblem-system-symbolic"),
}


class TimeDay(Gtk.Application):
    def __init__(self):
        GLib.set_prgname(APP_ID)
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start in the system tray", None)
        self.window = None
        self.launch_screen = None
        self.loading_source = 0
        self.language_source = 0
        self.pending_action = None
        self.store = None
        self.tray = None
        self.startup_failed = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        try:
            self.store = Store()
            set_language(self.store.setting("language", "ru"))
        except (OSError, sqlite3.Error) as exc:
            self.startup_failed = True
            logging.error(tr('Не удалось открыть базу TimeDay: %s'), exc)
            return
        self.theme = Gtk.CssProvider()
        self.stylesheet = Path(__file__).with_name("style.css").read_text(encoding="utf-8")
        display = Gdk.Display.get_default()
        icons = Gtk.IconTheme.get_for_display(display)
        installed_icon = icons.has_icon(APP_ID)
        icons.add_search_path(str(APP_ICON.parent))
        self.icon_file = Gio.File.new_for_path(str(APP_ICON))
        self.notification_icon = (
            Gio.ThemedIcon.new(APP_ID) if installed_icon
            else Gio.FileIcon.new(self.icon_file)
        )
        Gtk.Window.set_default_icon_name(APP_ID)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        Gtk.StyleContext.add_provider_for_display(display, self.theme, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_accent()
        self.apply_animation()
        self.add_command("tasks", lambda: self.open_action(lambda: self.window.navigate("tasks")))
        self.add_command("new-task", lambda: self.open_action(lambda: self.window.edit_task()))
        self.add_command("quit", self.quit)
        self.add_command("timers", lambda: self.open_action(lambda: self.window.navigate("timers")))
        task_action = Gio.SimpleAction.new("show-task-day", GLib.VariantType.new("x"))
        task_action.connect("activate", self.show_task_day)
        self.add_action(task_action)
        self.tray = Tray(
            self.get_dbus_connection(), APP_ID, self.activate, self.quit,
            icon_file=APP_ICON,
        )
        try:
            migrate_autostart()
        except (OSError, ValueError) as exc:
            logging.warning("%s", exc)
        self.hold()
        self.set_accels_for_action("app.new-task", ["<Control>n"])
        self.set_accels_for_action("app.quit", ["<Control>q"])
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.shutdown_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.shutdown_signal)

    def add_command(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda *_: callback())
        self.add_action(action)

    def shutdown_signal(self):
        self.quit()
        return False

    def show_task_day(self, _action, target):
        task_id = target.get_int64()

        def show():
            task = next((item for item in self.store.tasks() if item.id == task_id), None)
            self.window.navigate("tasks")
            if task is not None:
                self.window.planner.select_day(task.day)

        self.open_action(show)

    def open_action(self, callback):
        self.pending_action = callback
        self.activate()

    def do_command_line(self, command_line):
        if self.startup_failed:
            return 1
        if command_line.get_options_dict().contains("background"):
            # Construct without mapping: recorders, timers and reminders must run
            # even before the user opens a window for the first time this session.
            if (self.window is None and self.launch_screen is None
                    and self.settings("onboarding_completed", "0") == "1"):
                self.window = TimeDayWindow(self)
                self.window.navigate(purpose_page(self.settings("usage_purpose", "")))
        else:
            self.activate()
        return 0

    def do_activate(self):
        if self.startup_failed:
            return
        if self.launch_screen is not None:
            self.launch_screen.present()
            return
        if self.window is None:
            first_run = self.settings("onboarding_completed", "0") != "1"
            self.launch_screen = WelcomeWindow(self, first_run)
            self.launch_screen.present()
            if not first_run:
                self.launch_screen.add_tick_callback(self.queue_loading)
            return
        self.window.present()
        if self.pending_action is not None:
            callback, self.pending_action = self.pending_action, None
            callback()
        if self.window.editor is not None:
            self.window.editor.present()
        self.window.refresh_activity()

    def complete_setup(self, purpose, autostart, shortcut):
        try:
            desktop = apply_integrations(autostart, shortcut)
            if desktop is not None:
                try:
                    Gio.File.new_for_path(str(desktop)).set_attribute_string(
                        "metadata::trusted", "true", Gio.FileQueryInfoFlags.NONE, None,
                    )
                except GLib.Error:
                    logging.info(tr('Рабочий стол не поддерживает отметку доверенного ярлыка.'))
            with self.store.db:
                self.store.db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", (
                    ("usage_purpose", purpose), ("autostart", str(int(autostart))),
                    ("desktop_shortcut", str(int(shortcut))), ("onboarding_completed", "1"),
                ))
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.launch_screen.show_error(str(exc))
            return
        self.launch_screen.show_loading()
        self.launch_screen.add_tick_callback(self.queue_loading)

    def queue_loading(self, _widget, _clock):
        # GTK paints at high idle priority before constructing the main window.
        self.loading_source = GLib.idle_add(self.finish_loading)
        return False

    def change_language(self, widget, *_):
        code = LANGUAGES[widget.get_selected()][0]
        if code == language():
            return
        if self.language_source:
            GLib.source_remove(self.language_source)
        self.language_source = GLib.idle_add(self.apply_language, code)

    def apply_language(self, code):
        self.language_source = 0
        self.store.set_setting("language", normalize_language(code))
        set_language(code)
        self.tray.translate()
        for window in (self.window, self.launch_screen):
            if window is not None and hasattr(window, "language_control"):
                control = window.language_control
                control.handler_block_by_func(self.change_language)
                control.set_selected([key for key, _name in LANGUAGES].index(language()))
                control.handler_unblock_by_func(self.change_language)
        refresh_translations()
        if self.window is not None:
            self.window.refresh_language()
        return False

    def finish_loading(self):
        self.loading_source = 0
        if self.window is None:
            self.window = TimeDayWindow(self)
        self.window.navigate(purpose_page(self.settings("usage_purpose", "")))
        self.launch_screen.destroy()
        self.launch_screen = None
        self.activate()
        return False

    def do_shutdown(self):
        if self.language_source:
            GLib.source_remove(self.language_source)
            self.language_source = 0
        if self.loading_source:
            GLib.source_remove(self.loading_source)
            self.loading_source = 0
        if self.launch_screen is not None:
            self.launch_screen.destroy()
            self.launch_screen = None
        if self.window is not None:
            self.window.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.store is not None:
            self.store.close()
        Gtk.Application.do_shutdown(self)

    def settings(self, key, default):
        return self.store.setting(key, default)

    def number_setting(self, key):
        default, minimum, maximum = NUMBER_SETTINGS[key]
        try:
            value = int(self.settings(key, default))
        except ValueError:
            return default
        return max(minimum, min(maximum, value))

    @property
    def accent_name(self):
        name = self.settings("accent", DEFAULT_COLOR)
        return name if name in PALETTE else DEFAULT_COLOR

    @property
    def accent(self):
        return PALETTE[self.accent_name]

    def apply_accent(self):
        css = f"@define-color accent {self.accent};\n{self.stylesheet}"
        if self.settings("animations", "1") != "1":
            css += "\n* { transition-duration: 0ms; }"
        self.theme.load_from_data(css.encode())

    def apply_animation(self):
        Gtk.Settings.get_default().set_property("gtk-enable-animations", self.settings("animations", "1") == "1")
        self.apply_accent()

    def notify(self, key, title, body, action=None, target=None):
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(self.notification_icon)
        if action and target is not None:
            notification.set_default_action_and_target(action, target)
        elif action:
            notification.set_default_action(action)
        self.send_notification(key, notification)


class TimeDayWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TimeDay", default_width=1120, default_height=780)
        center_on_first_map(self)
        self.app = app
        self.store = app.store
        self.icons = AppIcons(Gdk.Display.get_default())
        self.monitor = X11Monitor(
            self.icons.names, self.icons.canonical_name, self.icons.accept_window_icon, self.icons.window_name,
        )
        self.foreground = None
        self.recorder = ActivityRecorder(self.store)
        self.presence = PresenceRecorder(self.store)
        self.media = MediaPlayback()
        self.runtime = RuntimeRecorder(self.store)
        self.process_apps = ProcessApps(
            self.icons.executables, self.icons.names, excluded=self.icons.runtime_excluded, steam=self.icons.steam,
        )
        self.runtime_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timeday-processes")
        self.runtime_probe = None
        self.runtime_revision = 0
        self.editor = None
        self.backup_chooser = None
        self.sources = []
        self.active_page = None
        self.compact = False
        self.tasks = None
        self.locked = False
        self.activity_state = None
        self.lock_subscription = 0
        self.preview_signature = None
        self.uptime = ComputerUptime(self.store)
        self.uptime.checkpoint()
        self.pc_history_loading = None
        self.pc_history_pending = None
        self.pc_history_loaded = set()
        self.stopped = False
        self.connect("close-request", self.hide_to_tray)

        top = Gtk.HeaderBar()
        section = box(spacing=8, css="header-section")
        self.section_icon = icon(PAGES["overview"][1], size=15)
        self.section_title = label(tr('Сегодня'), "strong")
        section.append(self.section_icon)
        section.append(self.section_title)
        top.pack_start(section)
        self.brand = box(spacing=8, css="brand")
        self.brand.set_valign(Gtk.Align.CENTER)
        brand_icon = Gtk.IconPaintable.new_for_file(app.icon_file, 26, self.get_scale_factor())
        brand_image = Gtk.Image.new_from_paintable(brand_icon)
        brand_image.set_pixel_size(26)
        self.brand.append(brand_image)
        self.brand_name = label(css="brandname")
        self.brand_name.set_markup('Time<span weight="medium" foreground="#c6c6c6">Day</span>')
        self.brand.append(self.brand_name)
        top.set_title_widget(self.brand)
        session = box(spacing=6, css="session")
        session.set_valign(Gtk.Align.CENTER)
        self.pc_button = localized_widget(Gtk.MenuButton, has_frame=False)
        self.pc_button.add_css_class("pc-session")
        set_translated(self.pc_button, 'set_tooltip_text', tr('Время работы компьютера по дням'))
        self.session = label(tr('ПК сегодня'), "status")
        reading = box(spacing=5)
        reading.append(icon("computer-symbolic", size=13))
        reading.append(self.session)
        self.pc_button.set_child(reading)
        self.pc_popover = Gtk.Popover()
        self.pc_popover.set_size_request(220, -1)
        history = box(True, 8, "pc-history")
        history.append(label(tr('Время работы ПК'), "section-title"))
        self.pc_rows = box(True, 6)
        history.append(self.pc_rows)
        history.append(button(tr('Сессии ПК'), self.open_pc_sessions, "ghost"))
        self.pc_popover.set_child(history)
        self.pc_button.set_popover(self.pc_popover)
        self.pc_popover.connect("show", lambda *_: self.refresh_pc_display())
        session.append(self.pc_button)
        top.pack_end(session)
        self.set_titlebar(top)

        root = box()
        self.set_child(root)
        self.rail = box(True, 5, "sidebar")
        self.rail.set_size_request(192, -1)
        root.append(self.rail)
        self.nav = {}
        for key, (title, picture) in PAGES.items():
            if key == "settings":
                self.rail.append(spacer(False))
            item = button(tr(title), lambda current=key: self.navigate(current), "nav", picture)
            accessible_label(item, tr(title))
            self.nav[key] = item
            self.rail.append(item)
        self.tracking_footer = box(spacing=8, css="sidebar-footer")
        self.dot = box(css="dot")
        self.dot.set_valign(Gtk.Align.CENTER)
        self.tracking_footer.append(self.dot)
        self.status = label("", "status", wrap=True)
        self.tracking_footer.append(self.status)
        self.rail.append(self.tracking_footer)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=280, interpolate_size=True,
            hexpand=True, vexpand=True, hhomogeneous=True, vhomogeneous=False,
        )
        root.append(self.stack)
        self.build_overview()
        self.build_tasks()
        self.build_schedule()
        self.build_timers()
        self.build_activity()
        self.build_pc_sessions()
        self.build_settings()
        self.navigate("overview")
        self.update_tracking_status()
        self.watch_lock()
        self.refresh_tasks()
        self.refresh_activity()
        self.tick()
        self.refresh_pc_display()
        self.load_pc_history()
        self.capture_activity()
        self.poll_runtime()
        if not self.monitor.problem:
            self.focus_source = GLib.unix_fd_add_full(
                GLib.PRIORITY_DEFAULT, self.monitor.fileno(),
                GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                self.on_focus,
            )
        else:
            self.focus_source = 0
        self.sources.extend([
            GLib.timeout_add(100, self.tick),
            GLib.timeout_add_seconds(10, self.checkpoint),
            GLib.timeout_add(600, self.capture_activity),
            GLib.timeout_add_seconds(2, self.poll_runtime),
            GLib.timeout_add_seconds(3, self.refresh_visible_activity),
            GLib.timeout_add_seconds(5, self.refresh_tasks),
        ])

    def hide_to_tray(self, *_):
        if self.editor is not None:
            self.editor.set_visible(False)
        self.set_visible(False)
        return True

    def add_page(self, key, content):
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(content)
        self.stack.add_named(scroller, key)

    def resize_navigation(self):
        width = self.get_width()
        if width <= 0 or self.compact == (width < 1000):
            return
        self.compact = width < 1000
        if self.compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        self.rail.set_size_request(62 if self.compact else 192, -1)
        self.status.set_visible(not self.compact)
        self.dot.set_hexpand(self.compact)
        self.dot.set_halign(Gtk.Align.CENTER)
        for key, item in self.nav.items():
            body = item.get_child()
            body.get_last_child().set_visible(not self.compact)
            body.set_halign(Gtk.Align.CENTER if self.compact else Gtk.Align.FILL)
            set_translated(item, 'set_tooltip_text', tr(PAGES[key][0]) if self.compact else None)

    def navigate(self, name):
        if self.active_page == name:
            return
        if self.active_page is not None:
            self.nav[self.active_page].remove_css_class("active")
        self.active_page = name
        self.section_icon.set_from_icon_name(PAGES[name][1])
        set_translated(self.section_title, "set_text", tr(PAGES[name][0]))
        self.nav[name].add_css_class("active")
        self.stack.set_visible_child_name(name)
        if name in {"activity", "overview"} and hasattr(self, "tracking"):
            self.refresh_activity()
        elif name == "sessions":
            self.load_pc_history(self.pc_sessions.month)
            self.pc_sessions.refresh()

    def add_task_button(self):
        return button(tr('Новая задача'), self.edit_task, "primary", "list-add-symbolic")

    def build_overview(self):
        page = box(True, 18, "page")
        self.date_caption = label(self.today_text(), "eyebrow")
        page.append(page_heading(tr('Сегодня'), "", self.add_task_button(), eyebrow=self.date_caption))
        self.today = TodayContent(
            self.icons, self.edit_event, self.open_occurrence, self.open_today_tasks,
            lambda: self.navigate("activity"), self.open_app_details,
        )
        page.append(self.today)
        self.add_page("overview", page)

    def open_today_tasks(self):
        self.navigate("tasks")
        self.planner.select_day(date.today())

    def open_app_details(self, name):
        self.navigate("activity")
        self.activity.change_metric("focus")
        self.activity.change_period("day")
        self.activity.select_app(name)

    @staticmethod
    def today_text():
        today = date.today()
        return f"{WEEKDAYS[today.weekday()]}, {date_label(today)}"

    def build_tasks(self):
        page = box(True, 18, "page")
        page.append(page_heading(tr('Планировщик'), "", self.add_task_button()))
        self.planner = Planner(self.quick_add, self.edit_task, self.toggle_task, self.move_task, self.confirm_delete)
        page.append(self.planner)
        self.add_page("tasks", page)

    def build_schedule(self):
        page = box(True, 15, "page")
        action = button(tr('Добавить событие'), self.edit_event, "primary", "list-add-symbolic")
        page.append(page_heading(tr('Расписание'), "", action))
        self.schedule = ScheduleView(
            self.store, lambda day, minute: self.edit_event(day=day, start=minute),
            lambda item: self.edit_event(item=item), lambda: self.app.accent,
        )
        page.append(self.schedule)
        self.stack.add_named(page, "schedule")

    def edit_event(self, item=None, day=None, start=None):
        if self.editor is None:
            now = datetime.now()
            if day is None:
                day = item.day if item else (
                    self.schedule.day if self.active_page == "schedule" else now.date()
                )
            if start is None:
                start = min(1425, (now.hour * 60 + now.minute + 14) // 15 * 15) if day == now.date() else 540
            self.editor = EventEditor(self, day, start, item, self.store, self.event_changed)
            self.editor.connect("close-request", self.close_editor)
        self.editor.present()

    def event_changed(self, day):
        if self.active_page == "schedule":
            self.schedule.focus_day(day)
        else:
            self.schedule.refresh()
        self.today.update_event(next_occurrence(*self.store.calendar(), datetime.now()))

    def open_occurrence(self, item):
        self.navigate("schedule")
        self.schedule.open_day(item.day)

    def build_timers(self):
        page = box(True, 16, "page")
        page.append(page_heading(tr('Таймер и секундомер'), ""))
        self.timers = Timers(self.store, lambda: self.app.accent, lambda: self.app.notify(
            "countdown", tr('Таймер завершён'), tr('Заданное время истекло.'), "app.timers",
        ))
        page.append(self.timers)
        self.add_page("timers", page)

    def build_activity(self):
        page = box(True, 15, "page")
        page.append(page_heading(tr('Статистика'), ""))
        self.activity = ActivityPanel(self.store, self.icons, lambda: self.app.accent)
        page.append(self.activity)
        self.add_page("activity", page)

    def build_pc_sessions(self):
        page = box(True, 15, "page")
        page.append(page_heading(tr('Сессии ПК'), tr('Время, когда компьютер был включён и бодрствовал.')))
        self.pc_sessions = PCSessions(
            self.uptime, lambda: self.app.accent, self.load_pc_history,
            lambda month: month in {self.pc_history_loading, self.pc_history_pending},
        )
        page.append(self.pc_sessions)
        self.add_page("sessions", page)

    def open_pc_sessions(self):
        self.pc_popover.popdown()
        self.navigate("sessions")

    def build_settings(self):
        page = box(True, 15, "page")
        page.append(page_heading(tr('Настройки'), ""))
        general = box(True, 14, "card")
        general.append(label(tr('Оформление'), "section-title"))
        self.language_control = translated_dropdown([name for _code, name in LANGUAGES])
        self.language_control.set_selected([code for code, _name in LANGUAGES].index(language()))
        self.language_control.connect("notify::selected", self.app.change_language)
        general.append(self.setting_row(tr("Язык интерфейса"), "", self.language_control))
        colors = list(PALETTE)
        palette = translated_dropdown([tr(color) for color in colors])
        palette.set_selected(colors.index(self.app.accent_name))

        def change_color(widget, *_):
            self.store.set_setting("accent", colors[widget.get_selected()])
            self.app.apply_accent()
            self.refresh_activity()
            self.pc_sessions.refresh()

        palette.connect("notify::selected", change_color)
        general.append(self.setting_row(tr('Акцентный цвет'), "", palette))
        general.append(self.setting_row(
            tr('Анимации'), tr('Переходы между разделами и обновление графиков.'),
            self.switch("animations", self.app.apply_animation),
        ))
        page.append(general)

        startup = box(True, 10, "card")
        startup.append(label(tr('Запуск приложения'), "section-title"))
        self.autostart_control = Gtk.Switch(active=autostart_enabled())
        self.autostart_control.connect("notify::active", self.change_autostart)
        startup.append(self.setting_row(tr('Автозагрузка'), tr('Запускать при входе в систему только в трее'), self.autostart_control))
        self.autostart_error = label("", "error-text", wrap=True)
        self.autostart_error.set_visible(False)
        startup.append(self.autostart_error)
        page.append(startup)

        monitoring = box(True, 14, "card")
        monitoring.append(label(tr('Мониторинг'), "section-title"))
        monitoring.append(self.setting_row(
            tr('Отслеживать приложения'),
            tr('Записывает время работы программ и время в фокусе. Для записи фокуса нужен X11.'),
            self.switch("tracking", self.change_tracking),
        ))
        monitoring.append(self.setting_row(
            tr('Порог простоя, мин'), tr('При простое приостанавливается учёт времени в фокусе.'),
            self.number_setting("idle_minutes"),
        ))
        page.append(monitoring)

        reminders = box(True, 14, "card")
        reminders.append(label(tr('Напоминания'), "section-title"))
        reminders.append(self.setting_row(
            tr('Уведомлять о дедлайнах'), tr('TimeDay отправляет уведомления, пока приложение запущено.'),
            self.switch("reminders", self.refresh_tasks),
        ))
        reminders.append(self.setting_row(
            tr('Напомнить заранее, мин'), tr('В 0 минут срабатывает только уведомление о наступлении срока.'),
            self.number_setting("reminder_minutes"),
        ))
        page.append(reminders)

        data = box(True, 10, "card")
        data.append(label(tr('Данные'), "section-title"))
        self.backup_button = button(tr('Сохранить копию'), self.choose_backup, "outline")
        data.append(self.setting_row(
            tr('Резервная копия'), tr('Сохраняет задачи, расписание, таймеры, статистику и настройки одним файлом.'),
            self.backup_button,
        ))
        self.backup_status = label("", "muted", wrap=True)
        self.backup_status.set_visible(False)
        data.append(self.backup_status)
        page.append(data)
        page.append(label(f"TimeDay {APP_VERSION}", "muted"))
        self.add_page("settings", page)

    def change_autostart(self, widget, *_):
        enabled = widget.get_active()
        try:
            set_autostart(enabled)
            self.store.set_setting("autostart", str(int(enabled)))
            self.autostart_error.set_visible(False)
        except (OSError, ValueError, sqlite3.Error) as exc:
            widget.handler_block_by_func(self.change_autostart)
            widget.set_active(not enabled)
            widget.handler_unblock_by_func(self.change_autostart)
            set_translated(self.autostart_error, "set_text", str(exc))
            self.autostart_error.set_visible(True)

    def refresh_language(self):
        self.planner.rendered = None
        self.planner.calendar.rendered = None
        self.planner.calendar.refresh()
        self.schedule.agenda_signature = None
        self.schedule.refresh()
        selected_day = None
        index = self.activity.day_filter.get_selected()
        if index < len(self.activity.days):
            selected_day = self.activity.days[index]
        self.activity.days = []
        self.activity.refresh()
        if selected_day in self.activity.days:
            self.activity.day_filter.set_selected(self.activity.days.index(selected_day))
        self.pc_sessions.rendered = None
        self.pc_sessions.refresh()
        self.timers.refresh()
        self.refresh_tasks()
        self.refresh_activity()
        self.update_tracking_status()
        self.refresh_pc_display()
        if self.editor is not None and hasattr(self.editor, "picker"):
            self.editor.picker.rendered = None
            self.editor.picker.refresh()
            if hasattr(self.editor, "date_button"):
                set_translated(self.editor.date_button, "set_label", date_label(self.editor.day))

    def choose_backup(self):
        chooser = Gtk.FileChooserNative.new(
            tr('Сохранить резервную копию'), self, Gtk.FileChooserAction.SAVE, tr('Сохранить'), tr('Отмена'),
        )
        chooser.set_modal(True)
        chooser.set_current_name(f"TimeDay-{datetime.now():%Y-%m-%d_%H-%M-%S}.sqlite3")
        chooser.connect("response", self.backup_selected)
        self.backup_chooser = chooser
        chooser.show()

    def backup_selected(self, chooser, response):
        file = chooser.get_file() if response == Gtk.ResponseType.ACCEPT else None
        chooser.destroy()
        self.backup_chooser = None
        if response != Gtk.ResponseType.ACCEPT:
            return
        destination = file.get_path() if file else None
        if destination is None:
            self.backup_finished(tr('Выберите папку на этом компьютере.'))
            return
        self.backup_button.set_sensitive(False)
        self.backup_status.remove_css_class("error-text")
        set_translated(self.backup_status, 'set_text', tr('Создаём резервную копию…'))
        self.backup_status.set_visible(True)
        try:
            self.timers.checkpoint()
        except sqlite3.Error as exc:
            self.backup_finished(str(exc))
            return

        def export():
            error = None
            try:
                backup_database(database_path(), destination)
            except (OSError, ValueError, sqlite3.Error) as exc:
                error = str(exc)
            GLib.idle_add(self.backup_finished, error)

        threading.Thread(target=export, name="timeday-backup").start()

    def backup_finished(self, error):
        if self.stopped:
            return False
        self.backup_button.set_sensitive(True)
        if error is None:
            set_translated(self.backup_status, 'set_text', tr('Резервная копия сохранена.'))
        else:
            self.backup_status.add_css_class("error-text")
            set_translated(self.backup_status, 'set_text', tr('Не удалось сохранить копию. {0}', error))
        self.backup_status.set_visible(True)
        return False

    @staticmethod
    def setting_row(title, detail, control):
        row = box(spacing=14, css="task-row")
        body = box(True, 4)
        body.set_hexpand(True)
        body.append(label(title, "strong"))
        if detail:
            body.append(label(detail, "muted", wrap=True))
        row.append(body)
        control.set_valign(Gtk.Align.CENTER)
        accessible_label(control, tr(title))
        row.append(control)
        return row

    def switch(self, key, callback):
        control = Gtk.Switch(active=self.app.settings(key, "1") == "1")

        def change(widget, *_):
            self.store.set_setting(key, "1" if widget.get_active() else "0")
            callback()

        control.connect("notify::active", change)
        return control

    def number_setting(self, key):
        _, minimum, maximum = NUMBER_SETTINGS[key]
        control = Gtk.SpinButton.new_with_range(minimum, maximum, 1)
        control.set_numeric(True)
        control.set_value(self.app.number_setting(key))

        def change(widget):
            self.store.set_setting(key, str(widget.get_value_as_int()))
            if key == "reminder_minutes":
                self.refresh_tasks()
            else:
                self.recorder.previous = None
                self.capture_activity()

        control.connect("value-changed", change)
        return control

    def change_tracking(self):
        self.recorder.previous = None
        self.presence.observe(None)
        self.runtime_revision += 1
        self.runtime.observe(None)
        self.update_tracking_status()
        self.capture_activity()
        self.poll_runtime()

    def update_tracking_status(self):
        self.runtime_enabled = self.app.settings("tracking", "1") == "1"
        self.tracking = not self.monitor.problem and self.runtime_enabled
        if not self.runtime_enabled:
            status = tr('Мониторинг выключен')
        elif self.monitor.problem:
            status = tr('Записывается время работы')
        elif self.locked or self.activity_state == "idle":
            status = tr('Запись фокуса на паузе')
        elif self.activity_state == "media":
            status = tr('Медиа воспроизводится')
        elif self.activity_state == "no-window":
            status = tr('Ожидание активного окна')
        else:
            status = tr('Активность записывается')
        set_translated(self.status, 'set_text', status)
        set_translated(self.tracking_footer, 'set_tooltip_text', self.monitor.problem or status)
        if self.runtime_enabled:
            self.dot.add_css_class("live")
        else:
            self.dot.remove_css_class("live")
        if not self.tracking or self.locked:
            self.set_foreground(None)

    def quick_add(self, day, title):
        self.save_task(title, "", day, None, None)

    def edit_task(self, task=None):
        day = self.planner.edit_day if self.active_page == "tasks" else date.today()
        if self.editor is None:
            self.editor = TaskEditor(self, task, day, self.save_task)
            self.editor.connect("close-request", self.close_editor)
        self.editor.present()

    def move_task(self, task):
        if self.editor is None:
            self.editor = MoveTask(self, task, self.save_task)
            self.editor.connect("close-request", self.close_editor)
        self.editor.present()

    def close_editor(self, *_):
        self.editor = None
        return False

    def save_task(self, title, description, day, due, old):
        task_id = self.store.save_task(title, description, day, due, old.id if old else None)
        if old and due != old.due:
            self.app.withdraw_notification(f"task-{task_id}")
        self.refresh_tasks(reload=True)

    def confirm_delete(self, task):
        dialog = localized_widget(Gtk.MessageDialog,
            transient_for=self, modal=True, text=tr('Удалить задачу?'), secondary_text=task.title,
        )
        dialog.add_button(tr('Отмена'), Gtk.ResponseType.CANCEL)
        dialog.add_button(tr('Удалить'), Gtk.ResponseType.ACCEPT).add_css_class("danger")
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        def respond(message, response):
            message.close()
            if response == Gtk.ResponseType.ACCEPT:
                self.store.delete_task(task.id)
                self.app.withdraw_notification(f"task-{task.id}")
                self.refresh_tasks(reload=True)

        dialog.connect("response", respond)
        dialog.present()

    def toggle_task(self, task, done):
        self.store.set_done(task.id, done)
        if done:
            self.app.withdraw_notification(f"task-{task.id}")
        self.refresh_tasks(reload=True)

    def refresh_tasks(self, reload=False):
        if reload or self.tasks is None:
            self.tasks = self.store.tasks()
        tasks = self.tasks
        now = time.time()
        today = [task for task in tasks if task.day == date.today()]
        self.planner.update(tasks)
        done = sum(task.done for task in today)
        set_translated(self.today.task_count, 'set_text', tr('Запланировано: {0} · Выполнено: {1}', len(today), done) if today else tr('На сегодня пока нет задач.'))
        self.today.update_event(next_occurrence(*self.store.calendar(), datetime.now()))
        self.schedule.tick()
        preview = today[:5]
        signature = [(task, task.due is not None and task.due <= now) for task in preview]
        if signature != self.preview_signature:
            self.preview_signature = signature
            clear(self.today.tasks)
            for task in preview:
                self.today.tasks.append(task_row(
                    task, now, self.toggle_task, self.edit_task, self.move_task, self.confirm_delete, compact=True,
                ))
        if self.app.settings("reminders", "1") == "1":
            lead = self.app.number_setting("reminder_minutes") * 60
            for item in self.store.reminders(now, lead):
                title = tr('Дедлайн наступил') if item["due"] <= now else tr('Приближается дедлайн')
                self.app.notify(
                    f"task-{item['id']}", title, item["title"], "app.show-task-day",
                    GLib.Variant("x", item["id"]),
                )
        return True

    def refresh_visible_activity(self):
        if self.get_mapped():
            self.refresh_activity()
        return True

    def refresh_activity(self):
        set_translated(self.date_caption, 'set_text', self.today_text())
        edges, _ = period_bins("day")
        samples = self.icons.samples(self.store.activity(edges[0], edges[-1]))
        _totals, _per_app, ranked = summarize(samples, edges, MIN_APP_SECONDS)
        if self.active_page == "overview":
            self.today.update_activity(ranked)
        elif self.active_page == "activity":
            background = None
            if self.tracking and not self.locked:
                try:
                    background = self.monitor.background_apps()
                except (OSError, self.monitor.errors.ConnectionClosedError):
                    pass
            self.activity.refresh_presence(background, self.runtime.current)
            self.activity.refresh()
        return True

    def set_foreground(self, name):
        self.foreground = name

    def watch_lock(self):
        connection = self.app.get_dbus_connection()
        if connection is None or self.monitor.problem:
            return
        service = "org.cinnamon.ScreenSaver"
        self.lock_subscription = connection.signal_subscribe(
            service, service, "ActiveChanged", "/org/cinnamon/ScreenSaver", None,
            Gio.DBusSignalFlags.NONE,
            lambda _bus, _sender, _path, _iface, _signal, params, *_data: self.set_locked(params.unpack()[0]),
        )

        def get_active(bus, result):
            try:
                result = bus.call_finish(result)
            except GLib.Error:
                return
            if self.lock_subscription:
                self.set_locked(result.unpack()[0])

        connection.call(
            service, "/org/cinnamon/ScreenSaver", service, "GetActive", None,
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NO_AUTO_START, 1500, None, get_active,
        )

    def set_locked(self, locked):
        if self.locked == locked:
            return
        self.locked = locked
        self.capture_activity()
        self.update_tracking_status()

    def on_focus(self, _fd, condition):
        if condition & (GLib.IOCondition.ERR | GLib.IOCondition.HUP):
            self.focus_source = 0
            self.monitor.problem = tr('Соединение с X11 потеряно.')
            self.update_tracking_status()
            return False
        self.capture_activity(heartbeat=False)
        return True

    def capture_activity(self, heartbeat=True):
        if self.monitor.problem:
            return True
        try:
            changes = self.monitor.drain_focus_events()
            if self.tracking and (heartbeat or changes):
                if changes > 1:
                    # X11 exposes the current focus, so intermediate windows in a
                    # queued batch cannot be identified reliably.
                    self.recorder.previous = None
                limit = self.app.number_setting("idle_minutes") * 60
                sample = self.monitor.sample(self.locked)
                if sample is not None:
                    sample = replace(sample, playing=self.media.playing() and not sample.locked)
                self.presence.observe(sample)
                self.set_foreground(sample.app if sample else None)
                self.recorder.observe(sample, limit)
                if sample is None or (sample.app is None and sample.idle < limit):
                    state = "no-window"
                elif sample.playing:
                    state = "media"
                elif sample.idle >= limit:
                    state = "idle"
                else:
                    state = "active"
                if state != self.activity_state:
                    self.activity_state = state
                    self.update_tracking_status()
        except (OSError, self.monitor.errors.ConnectionClosedError) as exc:
            logging.warning(tr('Ошибка мониторинга TimeDay: %s'), exc)
            self.monitor.problem = tr('Соединение с X11 потеряно.')
            self.recorder.previous = None
            self.update_tracking_status()
        return True

    def runtime_pids(self):
        pids = {}
        if not self.monitor.problem:
            try:
                pids.update(self.monitor.window_processes())
            except (OSError, self.monitor.errors.ConnectionClosedError):
                pass
        pids[os.getpid()] = "TimeDay"
        return pids

    def poll_runtime(self):
        if self.runtime_enabled and self.runtime_probe is None:
            revision = self.runtime_revision
            future = self.runtime_worker.submit(self.process_apps.snapshot, self.runtime_pids())
            self.runtime_probe = (future, revision)
            future.add_done_callback(lambda result: GLib.idle_add(self.accept_runtime, result, revision))
        return True

    def accept_runtime(self, future, revision):
        self.runtime_probe = None
        if revision == self.runtime_revision and self.runtime_enabled:
            try:
                self.runtime.observe(future.result())
            except Exception:
                logging.exception(tr('Не удалось получить список запущенных приложений'))
                self.runtime.observe(None)
        return False

    def load_pc_history(self, month=None):
        today = date.today()
        current_month = today.replace(day=1)
        month = month or current_month
        already_requested = month in self.pc_history_loaded or month == self.pc_history_loading
        if self.pc_history_loading is not None:
            self.pc_history_pending = None if already_requested else month
            return
        if already_requested:
            return
        self.pc_history_loading = month
        first_day = min(month, today - timedelta(days=6)) if month == current_month else month
        before_day = shift_month(month, 1)
        boot_id, point = self.uptime.boot_id, AwakePoint.now()

        def recover():
            try:
                history = journal_history(boot_id, point, first_day, before_day)
            except (TypeError, ValueError, KeyError):
                logging.warning(tr('Часть системного журнала недоступна для подсчёта времени ПК.'))
                history = {}
            GLib.idle_add(self.apply_pc_history, month, history)

        threading.Thread(target=recover, name="timeday-pc-history", daemon=True).start()

    def apply_pc_history(self, month, history):
        if self.stopped:
            return False
        self.pc_history_loaded.add(month)
        self.pc_history_loading = None
        pending, self.pc_history_pending = self.pc_history_pending, None
        if pending is not None:
            self.load_pc_history(pending)
        if history:
            self.store.save_awake_history(history)
        self.refresh_pc_display()
        return False

    def checkpoint(self):
        self.uptime.checkpoint()
        self.timers.checkpoint()
        if self.get_mapped():
            self.refresh_pc_display()
        return True

    def refresh_pc_display(self):
        today = date.today()
        history_open = self.pc_popover.get_visible()
        days = [today - timedelta(days=offset) for offset in range(7)] if history_open else [today]
        totals = self.uptime.totals(days)
        set_translated(self.session, 'set_text', tr('ПК сегодня · {0}', duration_label(totals[today])))
        if self.active_page == "sessions":
            self.pc_sessions.refresh()
        if history_open:
            clear(self.pc_rows)
            for day in days:
                row = box(spacing=14)
                row.append(label(date_label(day), "muted"))
                row.append(spacer())
                row.append(label(duration_label(totals[day]) if totals[day] else "—", "strong"))
                self.pc_rows.append(row)

    def tick(self):
        self.resize_navigation()
        self.timers.refresh()
        return True

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        if self.backup_chooser is not None:
            self.backup_chooser.destroy()
            self.backup_chooser = None
        self.uptime.checkpoint()
        for source in self.sources:
            GLib.source_remove(source)
        if self.focus_source:
            GLib.source_remove(self.focus_source)
        if self.lock_subscription:
            self.app.get_dbus_connection().signal_unsubscribe(self.lock_subscription)
            self.lock_subscription = 0
        self.capture_activity()
        self.runtime_worker.shutdown(wait=True)
        if self.runtime_probe is not None:
            self.accept_runtime(*self.runtime_probe)
        self.runtime_revision += 1
        if self.runtime_enabled:
            self.runtime.observe(self.process_apps.snapshot(self.runtime_pids()))
        self.timers.check_countdown()
        self.timers.save()
        self.monitor.close()


def main(background=False):
    if Gdk.Display.get_default() is None:
        logging.error(tr('Не удалось подключиться к графическому сеансу. Запустите TimeDay из рабочего стола.'))
        return 1
    GLib.set_application_name("TimeDay")
    app = TimeDay()
    return app.run([APP_ID, "--background"] if background else [APP_ID]) or int(app.startup_failed)
