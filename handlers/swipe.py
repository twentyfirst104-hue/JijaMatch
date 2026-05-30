"""
handlers/swipe.py — свайп-механика (ядро бота).

Поток:
- «🔥 Листать вкусы» -> показать следующую карточку (matching.get_next_flavor).
- Карточка показывается СРАЗУ с базовым ИИ-описанием (мгновенно).
  Персональное ИИ-описание генерируется неблокирующе в фоне и при готовности
  дополняет сообщение (редактируем подпись/текст). Это решает требование ТЗ
  «не генерировать на каждый свайп вживую блокирующе».
- Кнопки 👍/👎/⭐ -> services.weights.apply_action (с защитой от двойного начисления)
  -> показать следующую карточку.

Карточка с фото отправляется через answer_photo, без фото — обычным текстом.
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

import texts
import database as db
from services import matching, weights, cards
from keyboards.main_menu import main_menu_kb, BTN_SWIPE
from keyboards.swipe import swipe_kb

logger = logging.getLogger("handlers.swipe")
router = Router()


async def _send_next_card(message: Message, user_id: int):
    """Подобрать и отправить следующую карточку пользователю."""
    user = await db.get_user(user_id)
    if not user or not user["is_registered"]:
        await message.answer("Сначала пройди регистрацию: /start")
        return

    # Проверка выбранных категорий
    if not (user["show_ready"] or user["show_constructor"] or user["show_disposable"]):
        await message.answer(texts.NO_CATEGORIES, reply_markup=main_menu_kb())
        return

    flavor_id = await matching.get_next_flavor(user_id)
    if flavor_id is None:
        await message.answer(texts.NO_MORE_FLAVORS, reply_markup=main_menu_kb())
        return

    flavor = await db.get_flavor_full(flavor_id)
    if not flavor:
        await message.answer(texts.NO_MORE_FLAVORS, reply_markup=main_menu_kb())
        return

    is_paid = await db.is_producer_paid(flavor["producer_owner"]) if flavor["producer_owner"] else False
    text = await cards.build_card_text(user_id, flavor, is_paid)
    kb = swipe_kb(flavor_id)

    # Фиксируем показ (impression) для статистики
    await db.record_impression(user_id, flavor_id)

    # Фото: индивидуальное у вкуса либо общее у линейки
    photo = flavor["photo_file_id"] or flavor["line_photo"]

    if photo:
        sent = await message.answer_photo(photo=photo, caption=text, reply_markup=kb)
    else:
        sent = await message.answer(text, reply_markup=kb)

    # Фоновая генерация персонального ИИ-описания: при готовности дополняем сообщение.
    asyncio.create_task(_attach_personal_description(sent, user_id, flavor, kb, bool(photo)))


async def _attach_personal_description(sent: Message, user_id: int, flavor, kb, is_photo: bool):
    """
    Фоновая задача: сгенерировать персональное описание и обновить уже показанную
    карточку (если оно отличается от базового). Ошибки/недоступность ИИ — игнорируем.
    """
    personal = await cards.ensure_personal_description(user_id, flavor)
    if not personal:
        return
    # Если персональное == уже показанному базовому — нет смысла редактировать
    if flavor["ai_base_description"] and personal.strip() == flavor["ai_base_description"].strip():
        return

    # Перерисовываем карточку с персональным описанием (оно уже в кеше -> попадёт в текст)
    new_text = await cards.build_card_text(user_id, flavor, await _is_paid(flavor))
    try:
        if is_photo:
            await sent.edit_caption(caption=new_text, reply_markup=kb)
        else:
            await sent.edit_text(new_text, reply_markup=kb)
    except TelegramBadRequest:
        # Сообщение могло быть уже изменено/удалено (пользователь свайпнул) — это нормально
        pass


async def _is_paid(flavor) -> bool:
    if flavor["producer_owner"]:
        return await db.is_producer_paid(flavor["producer_owner"])
    return False


# ------------------------------------------------------------------ запуск ленты
@router.message(F.text == BTN_SWIPE)
async def start_swipe(message: Message):
    await _send_next_card(message, message.from_user.id)


# ------------------------------------------------------------------ нажатия кнопок
@router.callback_query(F.data.startswith("sw:"))
async def on_swipe_action(call: CallbackQuery):
    _, action, raw_id = call.data.split(":")
    flavor_id = int(raw_id)
    user_id = call.from_user.id

    # Применяем действие к весам (с защитой от двойного начисления)
    await weights.apply_action(user_id, flavor_id, action)

    if action == "favorite":
        await db.add_favorite(user_id, flavor_id)
        toast = texts.SWIPE_FAVORITED
    elif action == "like":
        toast = texts.SWIPE_LIKED
    else:
        toast = texts.SWIPE_DISLIKED

    await call.answer(toast)

    # Убираем кнопки у показанной карточки, чтобы нельзя было нажать повторно
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    # Показываем следующую карточку
    await _send_next_card(call.message, user_id)
