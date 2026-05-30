"""
keyboards/swipe.py — inline-кнопки под карточкой свайпа и в избранном.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def swipe_kb(flavor_id: int) -> InlineKeyboardMarkup:
    """Кнопки под карточкой: Лайк / Дизлайк / В избранное."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👍 Лайк", callback_data=f"sw:like:{flavor_id}"),
            InlineKeyboardButton(text="👎 Дизлайк", callback_data=f"sw:dislike:{flavor_id}"),
        ],
        [InlineKeyboardButton(text="⭐ В избранное", callback_data=f"sw:favorite:{flavor_id}")],
    ])


def favorites_list_kb(items: list) -> InlineKeyboardMarkup:
    """Список избранного: по кнопке на вкус (open)."""
    rows = []
    for it in items:
        title = f"{it['name']} · {it['producer_name']}"
        rows.append([InlineKeyboardButton(
            text=title[:60], callback_data=f"fav:open:{it['id']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def favorite_card_kb(flavor_id: int) -> InlineKeyboardMarkup:
    """Полная карточка из избранного: убрать из избранного."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Убрать из избранного",
                              callback_data=f"fav:remove:{flavor_id}")],
    ])
