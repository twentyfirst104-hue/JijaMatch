"""
handlers/producer.py — кабинет производителя.

Логика (по ТЗ):
- «🏭 Я производитель»: при первом входе объяснение + кнопка отправить заявку.
- Заявка создаёт producers с is_approved=0 (один owner = один producer).
- Пока не одобрен админом — добавлять товары нельзя.
- После одобрения: добавление линеек/вкусов (статус pending, идёт на модерацию),
  у бесплатных — проверка лимита FREE_PRODUCER_FLAVOR_LIMIT.
- «Мой канал» — ссылка сохраняется у всех, показывается только у платных.
- «Платный статус» — описание плюшек + кнопка «Связаться с админом» / срок+продлить.
"""

import asyncio

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import config
import texts
import database as db
from keyboards.main_menu import BTN_PRODUCER, contact_admin_kb
from keyboards.admin import (
    producer_menu_kb, become_producer_kb, category_choice_kb, pick_from_list_kb,
)
from services import ingest

router = Router()


class ProdReg(StatesGroup):
    name = State()
    description = State()
    channel = State()


class AddLine(StatesGroup):
    category = State()
    name = State()
    photo = State()


class AddFlavor(StatesGroup):
    line = State()
    name = State()
    description = State()
    photo = State()
    proportion = State()


class ChannelEdit(StatesGroup):
    waiting = State()


# ------------------------------------------------------------------ вход в кабинет
@router.message(F.text == BTN_PRODUCER)
async def producer_entry(message: Message):
    user_id = message.from_user.id
    producer = await db.get_producer_by_owner(user_id)

    if producer is None:
        # Ещё не подавал заявку
        await message.answer(
            texts.PRODUCER_INTRO.format(limit=config.FREE_PRODUCER_FLAVOR_LIMIT),
            reply_markup=become_producer_kb(),
        )
        return

    is_paid = await db.is_producer_paid(user_id)
    if not producer["is_approved"]:
        await message.answer(texts.PRODUCER_NOT_APPROVED_YET)
        return

    status = "💎 Платный активен" if is_paid else "Бесплатный"
    await message.answer(
        f"🏭 <b>{producer['name']}</b>\nСтатус: {status}",
        reply_markup=producer_menu_kb(is_approved=True, is_paid=is_paid),
    )


# ------------------------------------------------------------------ подача заявки
@router.callback_query(F.data == "prod:request")
async def prod_request_start(call: CallbackQuery, state: FSMContext):
    existing = await db.get_producer_by_owner(call.from_user.id)
    if existing:
        await call.answer("Заявка уже подана", show_alert=True)
        return
    await call.message.answer(texts.PRODUCER_NAME_ASK)
    await state.set_state(ProdReg.name)
    await call.answer()


@router.message(ProdReg.name)
async def prod_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await message.answer(texts.PRODUCER_DESC_ASK)
    await state.set_state(ProdReg.description)


@router.message(ProdReg.description)
async def prod_desc(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    await state.update_data(description="" if desc == "-" else desc)
    await message.answer(texts.PRODUCER_CHANNEL_ASK)
    await state.set_state(ProdReg.channel)


@router.message(ProdReg.channel)
async def prod_channel(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    channel = None if raw == "-" else raw
    data = await state.get_data()
    # is_approved=0 — производитель ждёт одобрения админом (антиспам)
    await db.create_producer(
        name=data["name"], description=data.get("description", ""),
        channel_url=channel, owner_user_id=message.from_user.id, is_approved=False,
    )
    await message.answer(texts.PRODUCER_REQUEST_SENT)
    await state.clear()
    # Уведомление админу — отправляется в bot.py через notify (импорт по требованию)
    from handlers.admin import notify_admin
    await notify_admin(message.bot,
                       f"🆕 Новая заявка в производители: <b>{data['name']}</b> "
                       f"(@{message.from_user.username or message.from_user.id}). "
                       f"Открой /admin → «Одобрить производителей».")


# ------------------------------------------------------------------ добавление линейки
@router.callback_query(F.data == "prod:add_line")
async def prod_add_line(call: CallbackQuery, state: FSMContext):
    producer = await db.get_producer_by_owner(call.from_user.id)
    if not producer or not producer["is_approved"]:
        await call.answer(texts.PRODUCER_NOT_APPROVED_YET, show_alert=True)
        return
    await state.update_data(producer_id=producer["id"])
    await call.message.answer("Выбери категорию линейки:",
                              reply_markup=category_choice_kb("prodline_cat"))
    await state.set_state(AddLine.category)
    await call.answer()


@router.callback_query(AddLine.category, F.data.startswith("prodline_cat:"))
async def prod_line_category(call: CallbackQuery, state: FSMContext):
    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await call.message.answer("Название линейки:")
    await state.set_state(AddLine.name)
    await call.answer()


@router.message(AddLine.name)
async def prod_line_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await message.answer("Пришли общее фото линейки (или отправь «-», чтобы пропустить):")
    await state.set_state(AddLine.photo)


@router.message(AddLine.photo)
async def prod_line_photo(message: Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif (message.text or "").strip() != "-":
        await message.answer("Пришли фото или «-» для пропуска.")
        return
    data = await state.get_data()
    line_id = await db.create_line(
        producer_id=data["producer_id"], category=data["category"], name=data["name"],
        common_photo_file_id=photo_id, created_by_user_id=message.from_user.id,
        status="pending",  # производитель -> на модерацию
    )
    await message.answer("✅ Линейка отправлена на модерацию.")
    await state.clear()
    from handlers.admin import notify_admin
    await notify_admin(message.bot, f"🆕 Линейка на модерации (#{line_id}): {data['name']}")


# ------------------------------------------------------------------ добавление вкуса
@router.callback_query(F.data == "prod:add_flavor")
async def prod_add_flavor(call: CallbackQuery, state: FSMContext):
    producer = await db.get_producer_by_owner(call.from_user.id)
    if not producer or not producer["is_approved"]:
        await call.answer(texts.PRODUCER_NOT_APPROVED_YET, show_alert=True)
        return

    # Проверка лимита для бесплатных
    if not await db.is_producer_paid(call.from_user.id):
        count = await db.count_producer_flavors(producer["id"])
        if count >= config.FREE_PRODUCER_FLAVOR_LIMIT:
            await call.message.answer(
                texts.PRODUCER_LIMIT_REACHED.format(limit=config.FREE_PRODUCER_FLAVOR_LIMIT),
                reply_markup=contact_admin_kb(config.ADMIN_USERNAME),
            )
            await call.answer()
            return

    lines = await db.list_lines(producer_id=producer["id"])
    if not lines:
        await call.answer("Сначала добавь линейку", show_alert=True)
        return
    await call.message.answer("Выбери линейку для нового вкуса:",
                              reply_markup=pick_from_list_kb(lines, "prodflavor_line"))
    await state.set_state(AddFlavor.line)
    await call.answer()


@router.callback_query(AddFlavor.line, F.data.startswith("prodflavor_line:"))
async def prod_flavor_line(call: CallbackQuery, state: FSMContext):
    line_id = int(call.data.split(":")[1])
    line = await db.get_line(line_id)
    await state.update_data(line_id=line_id, category=line["category"])
    await call.message.answer("Название вкуса:")
    await state.set_state(AddFlavor.name)
    await call.answer()


@router.message(AddFlavor.name)
async def prod_flavor_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await message.answer("Описание вкуса (от производителя):")
    await state.set_state(AddFlavor.description)


@router.message(AddFlavor.description)
async def prod_flavor_desc(message: Message, state: FSMContext):
    await state.update_data(description=(message.text or "").strip())
    await message.answer("Пришли фото вкуса (или «-», чтобы пропустить):")
    await state.set_state(AddFlavor.photo)


@router.message(AddFlavor.photo)
async def prod_flavor_photo(message: Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif (message.text or "").strip() != "-":
        await message.answer("Пришли фото или «-».")
        return
    await state.update_data(photo_id=photo_id)
    data = await state.get_data()
    if data["category"] == "constructor":
        await message.answer("Укажи рекомендуемую пропорцию / совместимую базу:")
        await state.set_state(AddFlavor.proportion)
    else:
        await _save_producer_flavor(message, state, proportion=None)


@router.message(AddFlavor.proportion)
async def prod_flavor_proportion(message: Message, state: FSMContext):
    await _save_producer_flavor(message, state, proportion=(message.text or "").strip())


async def _save_producer_flavor(message: Message, state: FSMContext, proportion):
    data = await state.get_data()
    flavor_id = await db.create_flavor(
        line_id=data["line_id"], name=data["name"], description=data.get("description", ""),
        photo_file_id=data.get("photo_id"), constructor_proportion=proportion,
        status="pending",  # на модерацию
    )
    await message.answer("✅ Вкус отправлен на модерацию. ИИ проанализирует его описание.")
    await state.clear()
    # ИИ-обработка фоном (теги + базовое описание)
    asyncio.create_task(ingest.process_new_flavor(flavor_id))
    from handlers.admin import notify_admin
    await notify_admin(message.bot, f"🆕 Вкус на модерации (#{flavor_id}): {data['name']}")


# ------------------------------------------------------------------ мои линейки/вкусы
@router.callback_query(F.data == "prod:my_items")
async def prod_my_items(call: CallbackQuery):
    producer = await db.get_producer_by_owner(call.from_user.id)
    lines = await db.list_lines(producer_id=producer["id"])
    if not lines:
        await call.message.answer("У тебя пока нет линеек.")
        await call.answer()
        return
    out = ["📦 <b>Твои линейки и вкусы:</b>\n"]
    for ln in lines:
        out.append(f"• <b>{ln['name']}</b> [{ln['status']}]")
        flavors = await db.list_flavors(line_id=ln["id"])
        for fl in flavors:
            out.append(f"    — {fl['name']} [{fl['status']}]")
    await call.message.answer("\n".join(out))
    await call.answer()


# ------------------------------------------------------------------ статистика
@router.callback_query(F.data == "prod:stats")
async def prod_stats(call: CallbackQuery):
    producer = await db.get_producer_by_owner(call.from_user.id)
    basic = await db.producer_basic_stats(producer["id"])
    out = [
        "📊 <b>Твоя статистика</b>",
        f"Показы: {basic['impressions']}",
        f"👍 Лайки: {basic['likes']}",
        f"👎 Дизлайки: {basic['dislikes']}",
        f"⭐ В избранном: {basic['favorites']}",
    ]
    # Расширенная — только для платных
    if await db.is_producer_paid(call.from_user.id):
        per = await db.producer_per_flavor_stats(producer["id"])
        out.append("\n<b>По вкусам (конверсия в избранное):</b>")
        for f in per[:15]:
            imp = f["impressions"] or 0
            conv = (f["favorites"] / imp * 100) if imp else 0
            out.append(f"• {f['name']}: 👁{imp} 👍{f['likes']} ⭐{f['favorites']} ({conv:.0f}%)")
    else:
        out.append("\n💎 Расширенная статистика — при платном статусе.")
    await call.message.answer("\n".join(out))
    await call.answer()


# ------------------------------------------------------------------ канал
@router.callback_query(F.data == "prod:channel")
async def prod_channel_menu(call: CallbackQuery, state: FSMContext):
    producer = await db.get_producer_by_owner(call.from_user.id)
    cur = producer["channel_url"] or "(не задана)"
    note = "" if await db.is_producer_paid(call.from_user.id) else \
        "\n⚠️ Показывается только при платном статусе."
    await call.message.answer(f"📢 Текущая ссылка на канал: {cur}{note}\n\nПришли новую ссылку (или «-» чтобы очистить):")
    await state.set_state(ChannelEdit.waiting)
    await call.answer()


@router.message(ChannelEdit.waiting)
async def prod_channel_save(message: Message, state: FSMContext):
    producer = await db.get_producer_by_owner(message.from_user.id)
    raw = (message.text or "").strip()
    value = None if raw == "-" else raw
    await db.update_producer_field(producer["id"], "channel_url", value)
    await message.answer("✅ Ссылка на канал обновлена.")
    await state.clear()


# ------------------------------------------------------------------ платный статус
@router.callback_query(F.data == "prod:paid")
async def prod_paid(call: CallbackQuery):
    is_paid = await db.is_producer_paid(call.from_user.id)
    if is_paid:
        user = await db.get_user(call.from_user.id)
        import datetime
        until = datetime.datetime.fromtimestamp(user["producer_paid_until"]).strftime("%d.%m.%Y")
        await call.message.answer(
            f"💎 Платный статус активен до <b>{until}</b>.\n\n"
            "Для продления свяжись с админом.",
            reply_markup=contact_admin_kb(config.ADMIN_USERNAME),
        )
    else:
        await call.message.answer(
            texts.PAID_FEATURES,
            reply_markup=contact_admin_kb(config.ADMIN_USERNAME),
        )
    await call.answer()
