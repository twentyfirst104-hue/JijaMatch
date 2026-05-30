"""
handlers/moderation.py — очередь модерации pending-карточек.

Платные производители вверху очереди с «⚡». Кнопки: одобрить / отклонить с
комментарием / редактировать и одобрить (правка делегируется admin_edit).
Все действия логируются в moderation_log.
"""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import database as db
from handlers.admin import is_admin
from keyboards.admin import moderation_kb

logger = logging.getLogger("handlers.moderation")
router = Router()


class Reject(StatesGroup):
    comment = State()


@router.callback_query(F.data == "adm:moderation")
async def show_moderation_queue(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    lines, flavors = await db.list_pending_lines_and_flavors()

    if not lines and not flavors:
        await call.message.answer("Очередь модерации пуста ✅")
        await call.answer()
        return

    # Сортируем так, чтобы платные производители были вверху (с ⚡)
    async def is_paid_owner(owner) -> bool:
        return await db.is_producer_paid(owner) if owner else False

    # Линейки
    for ln in sorted(lines, key=lambda r: r["created_at"]):
        paid = await is_paid_owner(ln["owner"])
        prefix = "⚡ Платный\n" if paid else ""
        await call.message.answer(
            f"{prefix}📦 <b>Линейка на модерации</b> (#{ln['id']})\n"
            f"Название: {ln['name']}\nКатегория: {ln['category']}",
            reply_markup=moderation_kb("line", ln["id"]),
        )
    # Вкусы
    for fl in sorted(flavors, key=lambda r: r["created_at"]):
        paid = await is_paid_owner(fl["owner"])
        prefix = "⚡ Платный\n" if paid else ""
        await call.message.answer(
            f"{prefix}🍬 <b>Вкус на модерации</b> (#{fl['id']})\n"
            f"Название: {fl['name']}\nОписание: {fl['description'] or '(нет)'}",
            reply_markup=moderation_kb("flavor", fl["id"]),
        )
    await call.answer()


async def _notify_owner(bot, entity_type: str, entity_id: int, approved: bool, comment: str = ""):
    """Уведомить владельца карточки об итоге модерации."""
    owner = None
    name = ""
    if entity_type == "flavor":
        row = await db.get_flavor_full(entity_id)
        if row:
            owner = row["producer_owner"]
            name = row["name"]
    else:
        line = await db.get_line(entity_id)
        if line:
            producer = await db.get_producer(line["producer_id"])
            owner = producer["owner_user_id"] if producer else None
            name = line["name"]
    if not owner:
        return
    if approved:
        text = f"✅ Твоя карточка «{name}» одобрена и теперь в каталоге!"
    else:
        text = f"❌ Твоя карточка «{name}» отклонена.\nПричина: {comment or 'не указана'}"
    try:
        await bot.send_message(owner, text)
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


@router.callback_query(F.data.startswith("mod:approve:"))
async def mod_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    _, _, entity_type, raw_id = call.data.split(":")
    entity_id = int(raw_id)
    if entity_type == "flavor":
        await db.update_flavor_field(entity_id, "status", "approved")
    else:
        await db.update_line_field(entity_id, "status", "approved")
    await db.log_moderation(entity_type, entity_id, "approve", None, call.from_user.id)
    await call.message.edit_text(f"✅ Одобрено ({entity_type} #{entity_id}).")
    await _notify_owner(call.bot, entity_type, entity_id, approved=True)
    await call.answer()


@router.callback_query(F.data.startswith("mod:reject:"))
async def mod_reject_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    _, _, entity_type, raw_id = call.data.split(":")
    await state.update_data(entity_type=entity_type, entity_id=int(raw_id))
    await call.message.answer("Напиши комментарий-причину отклонения:")
    await state.set_state(Reject.comment)
    await call.answer()


@router.message(Reject.comment)
async def mod_reject_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    entity_type = data["entity_type"]
    entity_id = data["entity_id"]
    comment = (message.text or "").strip()
    if entity_type == "flavor":
        await db.update_flavor_field(entity_id, "status", "rejected")
    else:
        await db.update_line_field(entity_id, "status", "rejected")
    await db.log_moderation(entity_type, entity_id, "reject", comment, message.from_user.id)
    await message.answer(f"❌ Отклонено ({entity_type} #{entity_id}).")
    await _notify_owner(message.bot, entity_type, entity_id, approved=False, comment=comment)
    await state.clear()
