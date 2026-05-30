"""
handlers/common.py — общие хендлеры: дисклеймер, /menu, fallback на непонятный ввод.

Этот роутер подключается ПОСЛЕДНИМ, поэтому ловит то, что не перехватили
специфичные хендлеры. Чтобы не мешать FSM (анкета/добавление товаров), fallback
срабатывает только когда у пользователя НЕТ активного состояния.
"""

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import texts
import database as db
from keyboards.main_menu import main_menu_kb, BTN_DISCLAIMER

router = Router()


@router.message(F.text == BTN_DISCLAIMER)
async def show_disclaimer(message: Message):
    await message.answer(texts.DISCLAIMER, reply_markup=main_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_registered"]:
        await message.answer("Сначала пройди регистрацию: /start")
        return
    await message.answer(texts.MENU_GREETING, reply_markup=main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Команды:\n"
        "/start — начать / открыть меню\n"
        "/menu — главное меню\n"
        "/help — помощь\n\n"
        "Пользуйся кнопками меню для навигации 🙂",
        reply_markup=main_menu_kb(),
    )


# Fallback: только если нет активного FSM-состояния (StateFilter(None)).
@router.message(StateFilter(None))
async def fallback(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_registered"]:
        await message.answer("Нажми /start, чтобы начать 🙂")
        return
    await message.answer(texts.UNKNOWN, reply_markup=main_menu_kb())
