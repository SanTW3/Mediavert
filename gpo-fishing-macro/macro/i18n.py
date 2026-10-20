"""Языки интерфейса. В коде строки написаны по-русски и обёрнуты в tr(); переводы — в lang_*.py,
ключ — русская строка. Чего нет в переводе, показывается по-русски (лучше, чем пусто).

    tr("Поймано: {name} (×{count})", name=name, count=c.count)
"""
from . import lang_en, lang_es, lang_pt

# Код → (название на этом языке, таблица переводов). Русский — исходный, таблица не нужна
LANGUAGES = {
    "en": ("English", lang_en.T),
    "ru": ("Русский", None),
    "es": ("Español", lang_es.T),
    "pt": ("Português (BR)", lang_pt.T),
}
DEFAULT = "en"
_current = "ru"   # до загрузки настроек (и в тестах) — исходный язык


def set_language(code):
    global _current
    _current = code if code in LANGUAGES else DEFAULT


def current():
    return _current


def tr(text, /, **kw):
    table = LANGUAGES.get(_current, (None, None))[1]
    out = table.get(text, text) if table else text
    if kw:
        try:
            return out.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text.format(**kw)   # перевод с ошибкой в подстановках — показываем оригинал
    return out


def plural(n, forms):
    """Слово для числа n. forms — русские формы (1, 2–4, 5+); в других языках: 1 — единственное, иначе — множественное."""
    n = abs(int(n))
    if _current != "ru":
        return tr(forms[0]) if n == 1 else tr(forms[2])
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]
