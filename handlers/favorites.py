"""
handlers/favorites.py — раздел «Избранное».

Список карточек с краткой инфой -> по клику полная карточка с кнопкой
«Убрать из избранного».
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

import texts
import database as db
from services import cards
from keyboards.main_menu import BTN_FAVORITES, main_menu_kb
from keyboards.swipe import favorites_list_kb, favorite_card_kb

router = Router()


@router.message(F.text == BTN_FAVORITES)
async def show_favorites(message: Message):
    items = await db.list_favorites(message.from_user.id)
    if not items:
        await message.answer(texts.FAVORITES_EMPTY, reply_markup=main_menu_kb())
        return
    await message.answer(texts.FAVORITES_HEADER, reply_markup=favorites_list_kb(items))


@router.callback_query(F.data.startswith("fav:open:"))
async def open_favorite(call: CallbackQuery):
    flavor_id = int(call.data.split(":")[2])
    flavor = await db.get_flavor_full(flavor_id)
    if not flavor:
        await call.answer("Вкус не найден", show_alert=True)
        return

    is_paid = await db.is_producer_paid(flavor["producer_owner"]) if flavor["producer_owner"] else False
    text = await cards.build_card_text(call.from_user.id, flavor, is_paid)
    photo = flavor["photo_file_id"] or flavor["line_photo"]

    if photo:
        await call.message.answer_photo(photo=photo, caption=text,
                                        reply_markup=favorite_card_kb(flavor_id))
    else:
        await call.message.answer(text, reply_markup=favorite_card_kb(flavor_id))
    await call.answer()


@router.callback_query(F.data.startswith("fav:remove:"))
async def remove_favorite(call: CallbackQuery):
    flavor_id = int(call.data.split(":")[2])
    await db.remove_favorite(call.from_user.id, flavor_id)
    await call.answer(texts.FAVORITE_REMOVED)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
