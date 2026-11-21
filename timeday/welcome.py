"""First-run setup and the loading window."""

from gi.repository import Gtk

from .i18n import LANGUAGES, language, tr
from . import APP_ICON
from .widgets import accessible_label, localized_widget, set_translated, translated_dropdown
from .widgets import box, button, label, weak_callback
from .windowing import center_on_first_map


PURPOSES = (
    ("work", "Работа и задачи", "Планировать дела и следить за дедлайнами", "tasks", "view-list-symbolic"),
    ("study", "Учёба и расписание", "Организовать занятия и встречи", "schedule", "x-office-calendar-symbolic"),
    ("focus", "Концентрация", "Работать с таймером и секундомером", "timers", "preferences-system-time-symbolic"),
    ("activity", "Статистика времени", "Следить за временем в приложениях", "activity", "utilities-system-monitor-symbolic"),
)


def purpose_page(purpose):
    return next((page for key, _title, _detail, page, _icon in PURPOSES if key == purpose), "overview")


class WelcomeWindow(Gtk.ApplicationWindow):
    def __init__(self, app, first_run=True):
        super().__init__(application=app, title=tr('Добро пожаловать в TimeDay'), default_width=650,
                         default_height=690 if first_run else 380)
        set_translated(self, "set_title", tr('Добро пожаловать в TimeDay'))
        self.app = app
        center_on_first_map(self)
        self.connect("close-request", weak_callback(self.cancel))
        header = Gtk.HeaderBar()
        header.set_title_widget(label("TimeDay", "strong"))
        self.set_titlebar(header)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=280)
        self.set_child(self.pages)
        loading = box(True, 18, "welcome-loading")
        loading.set_halign(Gtk.Align.CENTER)
        loading.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_file(str(APP_ICON))
        image.set_pixel_size(64)
        loading.append(image)
        loading.append(label("TimeDay", "title"))
        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        self.spinner.set_size_request(24, 24)
        loading.append(self.spinner)
        loading.append(label(tr('Загружаем ваш день…'), "muted"))
        self.pages.add_named(loading, "loading")

        if first_run:
            self.build_setup()
            self.pages.set_visible_child_name("setup")
        else:
            self.pages.set_visible_child_name("loading")

    def build_setup(self):
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        form = box(True, 16, "welcome-content")
        scroller.set_child(form)
        language_row = box(spacing=12)
        caption = label(tr("Язык интерфейса"), "muted")
        caption.set_hexpand(True)
        language_row.append(caption)
        self.language_control = translated_dropdown([name for _code, name in LANGUAGES])
        self.language_control.set_selected([code for code, _name in LANGUAGES].index(language()))
        accessible_label(self.language_control, tr("Язык интерфейса"))
        self.language_control.connect("notify::selected", self.app.change_language)
        language_row.append(self.language_control)
        form.append(language_row)
        form.append(label(tr('Добро пожаловать в TimeDay'), "section-title", wrap=True))
        form.append(label(tr('Для чего вы будете использовать приложение?'), "muted", wrap=True))
        choices = box(True, 6)
        self.purposes = {}
        group = None
        selected = self.app.store.setting("usage_purpose", "work")
        for key, title, detail, _page, picture in PURPOSES:
            row = box(spacing=12, css="welcome-choice")
            choice = localized_widget(Gtk.CheckButton, label=tr(title), hexpand=True)
            choice.add_css_class("purpose-choice")
            if group is not None:
                choice.set_group(group)
            group = choice
            choice.set_active(key == selected)
            text = box(True, 4)
            text.append(choice)
            caption = label(tr(detail), "muted", wrap=True)
            caption.set_margin_start(24)
            text.append(caption)
            text.set_hexpand(True)
            row.append(text)
            image = Gtk.Image.new_from_icon_name(picture)
            image.set_pixel_size(22)
            row.append(image)
            choices.append(row)
            self.purposes[key] = choice
        form.append(choices)
        form.append(label(tr('Выбранный раздел будет открываться при запуске.'), "muted", wrap=True))
        options = box(True, 12, "card")
        self.autostart = localized_widget(Gtk.CheckButton,
            label=tr('Запускать при входе в систему только в трее'), active=self.app.settings("autostart", "1") == "1",
        )
        self.shortcut = localized_widget(Gtk.CheckButton,
            label=tr('Создать ярлык на рабочем столе'), active=self.app.settings("desktop_shortcut", "1") == "1",
        )
        options.append(self.autostart)
        options.append(self.shortcut)
        form.append(options)
        self.error = label("", "error-text", wrap=True)
        self.error.set_visible(False)
        form.append(self.error)
        self.start_button = button(tr('Начать работу'), self.finish, "primary")
        self.start_button.set_halign(Gtk.Align.END)
        form.append(self.start_button)
        self.pages.add_named(scroller, "setup")

    def finish(self):
        purpose = next((key for key, choice in self.purposes.items() if choice.get_active()), "work")
        self.error.set_visible(False)
        self.start_button.set_sensitive(False)
        self.app.complete_setup(purpose, self.autostart.get_active(), self.shortcut.get_active())

    def show_error(self, text):
        self.pages.set_visible_child_name("setup")
        set_translated(self.error, 'set_text', text)
        self.error.set_visible(True)
        self.start_button.set_sensitive(True)

    def show_loading(self):
        set_translated(self, 'set_title', "TimeDay")
        self.pages.set_visible_child_name("loading")

    def cancel(self, *_):
        if self.app.window is None:
            self.app.quit()
        else:
            self.app.launch_screen = None
            self.destroy()
        return True
