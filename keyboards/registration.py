"""
keyboards/registration.py — клавиатуры анкеты (стаж, мультивыбор настроений и категорий).

Мультивыбор реализован через inline-кнопки с «галочками»: текущее состояние
выбора хранится в FSM (см. handlers/registration.py), а клавиатура только
рисует отметки и шлёт callback'и toggle/done.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import texts


def experience_kb() -> InlineKeyboardMarkup:
    """Выбор стажа (одиночный выбор)."""
    rows = [
        [InlineKeyboardButton(text=opt, callback_data=f"exp:{i}")]
        for i, opt in enumerate(texts.EXPERIENCE_OPTIONS)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def moods_kb(selected: set[int]) -> InlineKeyboardMarkup:
    """Мультивыбор настроений. selected — индексы выбранных вариантов."""
    rows = []
    for i, opt in enumerate(texts.MOOD_OPTIONS):
        mark = "✅ " if i in selected else "▫️ "
        rows.append([InlineKeyboardButton(text=mark + opt, callback_data=f"mood:{i}")])
    rows.append([InlineKeyboardButton(text="Готово ➡️", callback_data="mood:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Категории показа: ключи фиксированы (ready/constructor/disposable)
_CATEGORY_ORDER = ["ready", "constructor", "disposable"]


def categories_kb(selected: set[str]) -> InlineKeyboardMarkup:
    """Мультивыбор категорий для показа."""
    rows = []
    for key in _CATEGORY_ORDER:
        mark = "✅ " if key in selected else "▫️ "
        rows.append([InlineKeyboardButton(
            text=mark + texts.CATEGORY_LABELS[key], callback_data=f"cat:{key}"
        )])
    rows.append([InlineKeyboardButton(text="Готово ➡️", callback_data="cat:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


CATEGORY_ORDER = _CATEGORY_ORDER
