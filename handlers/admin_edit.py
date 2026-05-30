"""
handlers/admin_edit.py — раздел «Все карточки» и редактирование любых карточек.

Админ видит ВСЕ линейки и вкусы (включая чужие и на модерации), может:
- редактировать название/описание любого вкуса;
- перетегировать ИИ-ом;
- удалять.
Все правки логируются в edit_log.

Также сюда приходит «✏️ Редактировать и одобрить» из модерации (mod:edit:...):
открываем карточку вкуса/линейки с кнопками редактирования.
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import database as db
from handlers.admin import is_admin
from services import ingest
from keyboards.admin import edit_flavor_kb

logger = logging.getLogger("handlers.admin_edit")
router = Router()


class EditField(StatesGroup):
    waiting = State()


@router.callback_query(F.data == "adm:all_cards")
async def all_cards(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    lines = await db.list_lines()
    if not lines:
        await call.message.answer("Карточек пока нет.")
        await call.answer()
        return
    # Выводим компактным списком: линейка -> её вкусы с кнопками управления
    for ln in lines[:30]:  # ограничим вывод, чтобы не спамить
        producer = await db.get_producer(ln["producer_id"])
        header = (f"📦 <b>{ln['name']}</b> "
                  f"({producer['name'] if producer else '?'}) "
                  f"[{ln['status']}, {ln['category']}]")
        await call.message.answer(header)
        flavors = await db.list_flavors(line_id=ln["id"])
        for fl in flavors:
            await call.message.answer(
                f"🍬 #{fl['id']} {fl['name']} [{fl['status']}]\n{fl['description'] or ''}",
                reply_markup=edit_flavor_kb(fl["id"]),
            )
    await call.answer()


# «Редактировать и одобрить» из модерации
@router.callback_query(F.data.startswith("mod:edit:"))
async def mod_edit(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    _, _, entity_type, raw_id = call.data.split(":")
    entity_id = int(raw_id)
    if entity_type == "flavor":
        fl = await db.get_flavor(entity_id)
        await call.message.answer(
            f"✏️ Редактирование вкуса #{entity_id}: {fl['name']}\n"
            "Измени поля кнопками ниже, затем одобри командой одобрения в модерации.",
            reply_markup=edit_flavor_kb(entity_id),
        )
    else:
        await call.message.answer("Для линейки доступно одобрение/отклонение из модерации.")
    await call.answer()


# Редактирование конкретного поля вкуса
@router.callback_query(F.data.startswith("edit:flavor:"))
async def edit_flavor_field(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    _, _, field, raw_id = call.data.split(":")
    flavor_id = int(raw_id)

    if field == "delete":
        flavor = await db.get_flavor(flavor_id)
        await db.delete_flavor(flavor_id)
        await db.log_edit("flavor", flavor_id, call.from_user.id, "delete",
                          flavor["name"] if flavor else "", "DELETED")
        await call.message.edit_text(f"🗑 Вкус #{flavor_id} удалён.")
        await call.answer()
        return

    # Поля name/description -> запрашиваем новое значение
    await state.update_data(flavor_id=flavor_id, field=field)
    await call.message.answer(f"Введи новое значение для поля «{field}»:")
    await state.set_state(EditField.waiting)
    await call.answer()


@router.message(EditField.waiting)
async def edit_field_save(message: Message, state: FSMContext):
    data = await state.get_data()
    flavor_id = data["flavor_id"]
    field = data["field"]
    new_value = (message.text or "").strip()

    flavor = await db.get_flavor(flavor_id)
    old_value = flavor[field] if flavor and field in flavor.keys() else ""
    await db.update_flavor_field(flavor_id, field, new_value)
    await db.log_edit("flavor", flavor_id, message.from_user.id, field, old_value, new_value)
    await message.answer(f"✅ Поле «{field}» обновлено.")
    await state.clear()


# Перетегировать вкус ИИ-ом
@router.callback_query(F.data.startswith("edit:retag:"))
async def edit_retag(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    flavor_id = int(call.data.split(":")[2])
    await call.answer("🤖 Перетегирую...")
    # Сбрасываем флаг и запускаем обработку (теги + базовое описание)
    await db.update_flavor_field(flavor_id, "needs_retag", 1)
    await ingest.process_new_flavor(flavor_id)
    flavor = await db.get_flavor(flavor_id)
    status = "готово ✅" if flavor and not flavor["needs_retag"] else "ИИ недоступен, отложено в /retag"
    await call.message.answer(f"Перетегирование вкуса #{flavor_id}: {status}")
