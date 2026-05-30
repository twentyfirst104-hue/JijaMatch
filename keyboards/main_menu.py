"""
keyboards/main_menu.py — главное меню (reply-клавиатура) и inline-наборы.
"""

from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

import texts


# --- Тексты кнопок главного меню (используются и в фильтрах хендлеров) ---
BTN_SWIPE = "🔥 Листать вкусы"
BTN_FAVORITES = "⭐ Избранное"
BTN_PROFILE = "📝 Моя анкета"
BTN_TASTE = "📊 Мой вкусовой профиль"
BTN_PRODUCER = "🏭 Я производитель"
BTN_DISCLAIMER = "📋 О боте / 18+"


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню пользователя."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SWIPE)],
            [KeyboardButton(text=BTN_FAVORITES), KeyboardButton(text=BTN_TASTE)],
            [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_PRODUCER)],
            [KeyboardButton(text=BTN_DISCLAIMER)],
        ],
        resize_keyboard=True,
    )


def age_gate_kb() -> InlineKeyboardMarkup:
    """Кнопки подтверждения 18+."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Мне 18+", callback_data="age:yes")],
        [InlineKeyboardButton(text="🚫 Мне нет 18", callback_data="age:no")],
    ])


def contact_admin_kb(admin_username: str) -> InlineKeyboardMarkup:
    """Кнопка «Связаться с админом» — открывает чат с админом."""
    url = f"https://t.me/{admin_username}" if admin_username else "https://t.me/"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Связаться с админом", url=url)],
    ])
