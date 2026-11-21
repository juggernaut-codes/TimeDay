"""Application-owned translations, independent of the system locale."""

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path


LANGUAGES = (("ru", "RU · Русский"), ("en", "EN · English"), ("uk", "UA · Українська"))
_language = "ru"


def normalize_language(code):
    code = "uk" if code == "ua" else code
    return code if code in {key for key, _name in LANGUAGES} else "ru"


def language():
    return _language


def set_language(code):
    global _language
    _language = normalize_language(code)


@lru_cache(maxsize=2)
def catalog(code):
    return json.loads(Path(__file__).with_name("locales").joinpath(f"{code}.json").read_text(encoding="utf-8"))


def render(message, values):
    translated = message if _language == "ru" else catalog(_language).get(message, message)
    arguments = [render(value.message, value.values) if isinstance(value, TranslatedText) else value for value in values]
    return translated.format(*arguments) if values else translated


class TranslatedText(str):
    def __new__(cls, message, values):
        instance = super().__new__(cls, render(message, values))
        instance.message = message
        instance.values = values
        return instance


def tr(message, *values):
    if isinstance(message, TranslatedText) and not values:
        return TranslatedText(message.message, message.values)
    return TranslatedText(message, values)


class TranslatedNames(Sequence):
    def __init__(self, names):
        self.names = names

    def __len__(self):
        return len(self.names)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [tr(value) for value in self.names[index]]
        return tr(self.names[index])
