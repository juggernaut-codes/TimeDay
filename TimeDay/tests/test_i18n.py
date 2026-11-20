import ast
import string
import unittest
from datetime import date
from pathlib import Path

from timeday.events import minute_value, time_range
from timeday.i18n import catalog, language, render, set_language, tr
from timeday.models import MONTH_NAMES, WEEKDAYS, date_label, duration_label
from timeday.store import Store


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_language, language())

    def test_catalogs_cover_messages_and_preserve_format_fields(self):
        messages = set()
        for path in Path(__file__).resolve().parents[1].joinpath("timeday").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr"
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    messages.add(node.args[0].value)
        formatter = string.Formatter()

        def fields(value):
            return sorted((field, spec, conversion) for _text, field, spec, conversion in formatter.parse(value)
                          if field is not None)

        for code in ("en", "uk"):
            translated = catalog(code)
            self.assertFalse(messages - translated.keys(), (code, messages - translated.keys()))
            for message, result in translated.items():
                with self.subTest(code=code, message=message):
                    self.assertTrue(result.strip())
                    self.assertEqual(fields(message), fields(result))
                    self.assertEqual(message.count("%s"), result.count("%s"))

    def test_dates_durations_and_errors_follow_the_selected_language(self):
        samples = (
            ("ru", "Сегодня", "Понедельник", "Январь", "18 ноября 2026", "1 ч 01 мин"),
            ("en", "Today", "Monday", "January", "18 November 2026", "1 h 01 min"),
            ("ua", "Сьогодні", "Понеділок", "Січень", "18 листопада 2026", "1 год 01 хв"),
        )
        for code, today, weekday, month, day, duration in samples:
            with self.subTest(code=code):
                set_language(code)
                self.assertEqual(tr("Сегодня"), today)
                self.assertEqual(WEEKDAYS[0], weekday)
                self.assertEqual(MONTH_NAMES[0], month)
                self.assertEqual(date_label(date(2026, 11, 18)), day)
                self.assertEqual(duration_label(3660), duration)
                with self.assertRaisesRegex(ValueError, tr("Введите время в формате ЧЧ:ММ.")):
                    minute_value("wrong")
                self.assertIn("day" if code == "en" else "день", time_range(1410, 1500))
        set_language("unsupported")
        self.assertEqual(language(), "ru")

    def test_language_does_not_translate_user_content(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.save_task("Сегодня", "Назва / description", date.today())
        store.save_event("Настройки", date.today(), 60, 120)
        for code in ("en", "uk", "ru"):
            set_language(code)
            self.assertEqual(store.tasks()[0].title, "Сегодня")
            self.assertEqual(store.calendar()[0][0].title, "Настройки")

    def test_existing_text_can_be_translated_again_without_changing_user_values(self):
        set_language("ru")
        title = tr("Настройки")
        total = tr("ПК сегодня · {0}", duration_label(3660))
        task = tr("Выполнить: {0}", "Сегодня")
        set_language("en")
        self.assertEqual(render(title.message, title.values), "Settings")
        self.assertEqual(render(total.message, total.values), "Computer today · 1 h 01 min")
        self.assertEqual(render(task.message, task.values), "Complete: Сегодня")
