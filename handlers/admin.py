"""
handlers/admin.py — админ-панель (/admin), команды /retag, /backup, /broadcast,
управление производителями, добавление производителей/линеек/вкусов, статистика.

Доступ ограничен ADMIN_ID. Все админские добавления карточек идут со статусом
approved (без модерации). Вкусы после добавления автоматически тегируются ИИ.
"""

import asyncio
import datetime
import logging
import os
import shutil

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import config
import texts
import database as db
from services import ingest
from keyboards.admin import (
    admin_menu_kb, category_choice_kb, pick_from_list_kb,
    approve_producer_kb, manage_producer_kb,
)

logger = logging.getLogger("handlers.admin")
router = Router()


def is_admin(user_id: int) -> bool:
    return config.ADMIN_ID > 0 and user_id == config.ADMIN_ID


async def notify_admin(bot: Bot, text: str) -> None:
    """Отправить уведомление админу (используется из разных модулей)."""
    if config.ADMIN_ID <= 0:
        return
    try:
        await bot.send_message(config.ADMIN_ID, text)
    except (TelegramForbiddenError, TelegramBadRequest):
        logger.warning("Не удалось отправить уведомление админу")


# FSM-состояния админских мастеров
class AdmProducer(StatesGroup):
    name = State()
    description = State()
    channel = State()


class AdmLine(StatesGroup):
    producer = State()
    category = State()
    name = State()
    photo = State()


class AdmFlavor(StatesGroup):
    line = State()
    name = State()
    description = State()
    photo = State()
    proportion = State()


class Broadcast(StatesGroup):
    text = State()
    confirm = State()


# ------------------------------------------------------------------ /admin
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await message.answer(texts.ADMIN_PANEL, reply_markup=admin_menu_kb())


# ------------------------------------------------------------------ добавить производителя
@router.callback_query(F.data == "adm:add_producer")
async def adm_add_producer(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await call.message.answer("Название производителя:")
    await state.set_state(AdmProducer.name)
    await call.answer()


@router.message(AdmProducer.name)
async def adm_prod_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await message.answer("Описание (или «-»):")
    await state.set_state(AdmProducer.description)


@router.message(AdmProducer.description)
async def adm_prod_desc(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    await state.update_data(description="" if desc == "-" else desc)
    await message.answer("Ссылка на канал (или «-»):")
    await state.set_state(AdmProducer.channel)


@router.message(AdmProducer.channel)
async def adm_prod_channel(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    # owner_user_id=None — это «административный» производитель без владельца, сразу одобрен
    pid = await db.create_producer(
        name=data["name"], description=data.get("description", ""),
        channel_url=None if raw == "-" else raw, owner_user_id=None, is_approved=True,
    )
    await message.answer(f"✅ Производитель добавлен (#{pid}).")
    await state.clear()


# ------------------------------------------------------------------ одобрить производителей
@router.callback_query(F.data == "adm:approve_producers")
async def adm_approve_producers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pending = await db.list_pending_producers()
    if not pending:
        await call.message.answer("Очередь одобрения пуста.")
        await call.answer()
        return
    for p in pending:
        await call.message.answer(
            f"🏭 <b>{p['name']}</b>\n{p['description'] or ''}\n"
            f"Владелец: {p['owner_user_id']}",
            reply_markup=approve_producer_kb(p["id"]),
        )
    await call.answer()


@router.callback_query(F.data.startswith("adm:approve_prod:"))
async def adm_approve_prod(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.split(":")[2])
    await db.approve_producer(pid)
    producer = await db.get_producer(pid)
    await db.log_moderation("producer", pid, "approve", None, call.from_user.id)
    await call.message.edit_text(f"✅ Производитель «{producer['name']}» одобрен.")
    # уведомим владельца
    if producer["owner_user_id"]:
        try:
            await call.bot.send_message(
                producer["owner_user_id"],
                "🎉 Тебя одобрили как производителя! Теперь можно добавлять товары "
                "через «🏭 Я производитель».",
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
    await call.answer()


# ------------------------------------------------------------------ добавить линейку (админ)
@router.callback_query(F.data == "adm:add_line")
async def adm_add_line(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    producers = await db.list_producers()
    if not producers:
        await call.message.answer("Сначала добавь производителя.")
        await call.answer()
        return
    await call.message.answer("Выбери производителя:",
                              reply_markup=pick_from_list_kb(producers, "admline_prod"))
    await state.set_state(AdmLine.producer)
    await call.answer()


@router.callback_query(AdmLine.producer, F.data.startswith("admline_prod:"))
async def adm_line_producer(call: CallbackQuery, state: FSMContext):
    await state.update_data(producer_id=int(call.data.split(":")[1]))
    await call.message.answer("Категория линейки:", reply_markup=category_choice_kb("admline_cat"))
    await state.set_state(AdmLine.category)
    await call.answer()


@router.callback_query(AdmLine.category, F.data.startswith("admline_cat:"))
async def adm_line_category(call: CallbackQuery, state: FSMContext):
    await state.update_data(category=call.data.split(":")[1])
    await call.message.answer("Название линейки:")
    await state.set_state(AdmLine.name)
    await call.answer()


@router.message(AdmLine.name)
async def adm_line_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await message.answer("Общее фото линейки (или «-»):")
    await state.set_state(AdmLine.photo)


@router.message(AdmLine.photo)
async def adm_line_photo(message: Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif (message.text or "").strip() != "-":
        await message.answer("Пришли фото или «-».")
        return
    data = await state.get_data()
    lid = await db.create_line(
        producer_id=data["producer_id"], category=data["category"], name=data["name"],
        common_photo_file_id=photo_id, created_by_user_id=message.from_user.id,
        status="approved",  # админ -> сразу одобрено
    )
    await message.answer(f"✅ Линейка добавлена (#{lid}).")
    await state.clear()


# ------------------------------------------------------------------ добавить вкус (админ)
@router.callback_query(F.data == "adm:add_flavor")
async def adm_add_flavor(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    lines = await db.list_lines()
    if not lines:
        await call.message.answer("Сначала добавь линейку.")
        await call.answer()
        return
    await call.message.answer("Выбери линейку:",
                              reply_markup=pick_from_list_kb(lines, "admflavor_line"))
    await state.set_state(AdmFlavor.line)
    await call.answer()


@router.callback_query(AdmFlavor.line, F.data.startswith("admflavor_line:"))
async def adm_flavor_line(call: CallbackQuery, state: FSMContext):
    line_id = int(call.data.split(":")[1])
    line = await db.get_line(line_id)
    await state.update_data(line_id=line_id, category=line["category"])
    await call.message.answer("Название вкуса:")
    await state.set_state(AdmFlavor.name)
    await call.answer()


@router.message(AdmFlavor.name)
async def adm_flavor_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await message.answer("Описание вкуса:")
    await state.set_state(AdmFlavor.description)


@router.message(AdmFlavor.description)
async def adm_flavor_desc(message: Message, state: FSMContext):
    await state.update_data(description=(message.text or "").strip())
    await message.answer("Фото вкуса (или «-»):")
    await state.set_state(AdmFlavor.photo)


@router.message(AdmFlavor.photo)
async def adm_flavor_photo(message: Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif (message.text or "").strip() != "-":
        await message.answer("Пришли фото или «-».")
        return
    await state.update_data(photo_id=photo_id)
    data = await state.get_data()
    if data["category"] == "constructor":
        await message.answer("Рекомендуемая пропорция / база:")
        await state.set_state(AdmFlavor.proportion)
    else:
        await _save_admin_flavor(message, state, None)


@router.message(AdmFlavor.proportion)
async def adm_flavor_proportion(message: Message, state: FSMContext):
    await _save_admin_flavor(message, state, (message.text or "").strip())


async def _save_admin_flavor(message: Message, state: FSMContext, proportion):
    data = await state.get_data()
    fid = await db.create_flavor(
        line_id=data["line_id"], name=data["name"], description=data.get("description", ""),
        photo_file_id=data.get("photo_id"), constructor_proportion=proportion,
        status="approved",  # админ -> сразу одобрено
    )
    await message.answer(f"✅ Вкус добавлен (#{fid}). Запускаю ИИ-тегирование...")
    await state.clear()
    asyncio.create_task(ingest.process_new_flavor(fid))


# ------------------------------------------------------------------ статистика
@router.callback_query(F.data == "adm:stats")
async def adm_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    s = await db.stats_overview()
    top_likes = await db.stats_top_flavors("like")
    top_dis = await db.stats_top_flavors("dislike")
    out = [
        "📊 <b>Статистика</b>",
        f"👥 Пользователей: {s['users_total']} (активных за неделю: {s['users_active_week']})",
        f"🍬 Вкусы: ✅{s['flavors_approved']} ⏳{s['flavors_pending']} ❌{s['flavors_rejected']}",
        f"👍 Лайков: {s['likes_total']}  👎 Дизлайков: {s['dislikes_total']}  ⭐ {s['favorites_total']}",
        f"💎 Платных производителей: {s['producers_paid']}",
    ]
    if top_likes:
        out.append("\n🔥 Топ лайков:")
        out += [f"   • {r['name']} ({r['cnt']})" for r in top_likes]
    if top_dis:
        out.append("\n💧 Топ дизлайков:")
        out += [f"   • {r['name']} ({r['cnt']})" for r in top_dis]
    await call.message.answer("\n".join(out))
    await call.answer()


# ------------------------------------------------------------------ управление производителями
@router.callback_query(F.data == "adm:manage_producers")
async def adm_manage_producers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    producers = await db.list_producers(approved=True)
    if not producers:
        await call.message.answer("Производителей пока нет.")
        await call.answer()
        return
    for p in producers:
        paid = await db.is_producer_paid(p["owner_user_id"]) if p["owner_user_id"] else False
        tag = "💎 платный" if paid else "бесплатный"
        await call.message.answer(
            f"🏭 <b>{p['name']}</b> — {tag}",
            reply_markup=manage_producer_kb(p["id"]),
        )
    await call.answer()


async def _set_paid(owner_id: int, days: int, extend: bool) -> int:
    """Активировать/продлить платный статус. Возвращает unix-время окончания."""
    user = await db.get_user(owner_id)
    base = db.now()
    if extend and user and user["producer_paid_until"] and user["producer_paid_until"] > base:
        base = user["producer_paid_until"]
    until = base + days * 86400
    await db.set_user_field(owner_id, "producer_paid_until", until)
    # сбрасываем флаги уведомлений — новый период
    await db.set_user_field(owner_id, "paid_notified_3days", 0)
    await db.set_user_field(owner_id, "paid_notified_expired", 0)
    await db.set_user_field(owner_id, "is_producer", 1)
    return until


@router.callback_query(F.data.startswith("mp:"))
async def adm_manage_producer_action(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    _, action, raw_id = call.data.split(":")
    pid = int(raw_id)
    producer = await db.get_producer(pid)
    owner = producer["owner_user_id"]

    if action in ("activate30", "extend30"):
        if not owner:
            await call.answer("У этого производителя нет владельца-пользователя", show_alert=True)
            return
        until = await _set_paid(owner, 30, extend=(action == "extend30"))
        until_str = datetime.datetime.fromtimestamp(until).strftime("%d.%m.%Y")
        await call.message.answer(f"💎 Платный статус «{producer['name']}» до {until_str}.")
        try:
            await call.bot.send_message(owner, f"💎 Тебе активирован платный статус до {until_str}!")
        except (TelegramForbiddenError, TelegramBadRequest):
            pass
    elif action == "deactivate":
        if owner:
            await db.set_user_field(owner, "producer_paid_until", None)
        await call.message.answer(f"🚫 Платный статус «{producer['name']}» отключён.")
    elif action == "stats":
        basic = await db.producer_basic_stats(pid)
        await call.message.answer(
            f"📊 {producer['name']}: 👁{basic['impressions']} 👍{basic['likes']} "
            f"👎{basic['dislikes']} ⭐{basic['favorites']}"
        )
    await call.answer()


# ------------------------------------------------------------------ /retag
@router.message(Command("retag"))
async def cmd_retag(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await message.answer("🤖 Запускаю дотегирование вкусов без тегов/описаний...")
    ok, total = await ingest.retag_all()
    await message.answer(f"Готово: обработано {ok} из {total}.")


# ------------------------------------------------------------------ /backup
@router.message(Command("backup"))
async def cmd_backup(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    path = await make_backup()
    if path and os.path.exists(path):
        await message.answer_document(FSInputFile(path), caption="📦 Бэкап БД")
    else:
        await message.answer("Не удалось создать бэкап.")


async def make_backup() -> str | None:
    """
    Создать копию файла БД в BACKUP_DIR с датой в имени. Возвращает путь.
    Используется и командой /backup, и планировщиком (автобэкап).
    """
    if not os.path.exists(config.DB_PATH):
        return None
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    dest = os.path.join(config.BACKUP_DIR, f"bot_backup_{stamp}.db")
    # WAL: гарантируем, что данные сброшены в основной файл перед копией
    try:
        await db.db().execute("PRAGMA wal_checkpoint(FULL)")
    except Exception:  # noqa: BLE001 — бэкап не должен падать из-за чекпойнта
        pass
    shutil.copy2(config.DB_PATH, dest)
    return dest


# ------------------------------------------------------------------ /broadcast
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(texts.ADMIN_ONLY)
        return
    await message.answer("Пришли текст рассылки. Отправлю всем пользователям после подтверждения.")
    await state.set_state(Broadcast.text)


@router.message(Broadcast.text)
async def broadcast_text(message: Message, state: FSMContext):
    await state.update_data(text=message.html_text or message.text or "")
    ids = await db.all_user_ids(only_active=True)
    await message.answer(
        f"Получателей: {len(ids)}.\nОтправить? Напиши «да» для подтверждения или /cancel."
    )
    await state.set_state(Broadcast.confirm)


@router.message(Broadcast.confirm, F.text.lower() == "да")
async def broadcast_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text", "")
    await state.clear()
    ids = await db.all_user_ids(only_active=True)
    sent, blocked, failed = 0, 0, 0
    for uid in ids:
        try:
            await message.bot.send_message(uid, text)
            sent += 1
        except TelegramForbiddenError:
            # пользователь заблокировал бота — помечаем
            await db.mark_blocked(uid, True)
            blocked += 1
        except TelegramBadRequest:
            failed += 1
        # Пауза для соблюдения лимита ~30 сообщений/сек
        await asyncio.sleep(0.05)
    await message.answer(f"📣 Рассылка завершена. Отправлено: {sent}, "
                         f"заблокировали: {blocked}, ошибок: {failed}.")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(texts.CANCELLED)
