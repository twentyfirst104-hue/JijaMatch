"""
keyboards/admin.py — клавиатуры админ-панели, производителя и модерации.
"""

from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

import texts


# ---------------------------------------------------------------- админ-панель
def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить производителя", callback_data="adm:add_producer")],
        [InlineKeyboardButton(text="✅ Одобрить производителей", callback_data="adm:approve_producers")],
        [InlineKeyboardButton(text="➕ Добавить линейку", callback_data="adm:add_line")],
        [InlineKeyboardButton(text="➕ Добавить вкус", callback_data="adm:add_flavor")],
        [InlineKeyboardButton(text="🗂 Все карточки", callback_data="adm:all_cards")],
        [InlineKeyboardButton(text="🛡 Заявки на модерацию", callback_data="adm:moderation")],
        [InlineKeyboardButton(text="🏭 Управление производителями", callback_data="adm:manage_producers")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
    ])


def category_choice_kb(prefix: str) -> InlineKeyboardMarkup:
    """Выбор категории при добавлении линейки. prefix — префикс callback'а."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.CATEGORY_LABELS["ready"], callback_data=f"{prefix}:ready")],
        [InlineKeyboardButton(text=texts.CATEGORY_LABELS["constructor"], callback_data=f"{prefix}:constructor")],
        [InlineKeyboardButton(text=texts.CATEGORY_LABELS["disposable"], callback_data=f"{prefix}:disposable")],
    ])


def pick_from_list_kb(items: list, prefix: str, id_field: str = "id",
                      title_field: str = "name") -> InlineKeyboardMarkup:
    """Универсальный выбор сущности из списка (производитель/линейка)."""
    rows = [
        [InlineKeyboardButton(text=str(it[title_field])[:60],
                              callback_data=f"{prefix}:{it[id_field]}")]
        for it in items
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------- модерация
def moderation_kb(entity_type: str, entity_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod:approve:{entity_type}:{entity_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:reject:{entity_type}:{entity_id}"),
        ],
        [InlineKeyboardButton(text="✏️ Редактировать и одобрить",
                              callback_data=f"mod:edit:{entity_type}:{entity_id}")],
    ])


def approve_producer_kb(producer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить производителя",
                              callback_data=f"adm:approve_prod:{producer_id}")],
    ])


# ---------------------------------------------------------------- редактирование карточки
def edit_flavor_kb(flavor_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Название", callback_data=f"edit:flavor:name:{flavor_id}"),
         InlineKeyboardButton(text="✏️ Описание", callback_data=f"edit:flavor:description:{flavor_id}")],
        [InlineKeyboardButton(text="🤖 Перетегировать ИИ", callback_data=f"edit:retag:{flavor_id}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"edit:flavor:delete:{flavor_id}")],
    ])


def manage_producer_kb(producer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Активировать платный (30 дней)",
                              callback_data=f"mp:activate30:{producer_id}")],
        [InlineKeyboardButton(text="➕ Продлить (30 дней)",
                              callback_data=f"mp:extend30:{producer_id}")],
        [InlineKeyboardButton(text="🚫 Деактивировать платный",
                              callback_data=f"mp:deactivate:{producer_id}")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data=f"mp:stats:{producer_id}")],
    ])


# ---------------------------------------------------------------- меню производителя
def producer_menu_kb(is_approved: bool, is_paid: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_approved:
        rows.append([InlineKeyboardButton(text="📦 Мои линейки и вкусы", callback_data="prod:my_items")])
        rows.append([InlineKeyboardButton(text="➕ Добавить линейку", callback_data="prod:add_line")])
        rows.append([InlineKeyboardButton(text="➕ Добавить вкус", callback_data="prod:add_flavor")])
        rows.append([InlineKeyboardButton(text="📊 Моя статистика", callback_data="prod:stats")])
        rows.append([InlineKeyboardButton(text="📢 Мой канал", callback_data="prod:channel")])
    rows.append([InlineKeyboardButton(text="💎 Платный статус", callback_data="prod:paid")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def become_producer_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Отправить заявку", callback_data="prod:request")],
    ])
