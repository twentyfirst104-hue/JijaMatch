"""
handlers/registration.py — регистрация и анкета (FSM).

Шаги (по ТЗ):
1. /start -> приветствие + подтверждение 18+.
2. Возраст (валидация).
3. Стаж парения (выбор).
4. Любимые вкусы (текст).
5. Что не нравится (текст).
6. Настроение/повод (мультивыбор).
7. «Расскажи о себе» (текст, ИИ анализирует внимательно).
8. Категории показа (мультивыбор).
После сохранения — анализ анкеты ИИ -> стартовые веса (фоном/неблокирующе).

Переделать анкету можно из меню «Моя анкета». Если анкета значительно изменилась
(меняются likes/dislikes/about) — возвращаем дизлайки в ленту.
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

import texts
import database as db
from keyboards.main_menu import age_gate_kb, main_menu_kb
from keyboards.registration import (
    experience_kb, moods_kb, categories_kb, CATEGORY_ORDER,
)
from ai.profiler import analyze_profile, AIError

logger = logging.getLogger("handlers.registration")
router = Router()


class Reg(StatesGroup):
    age = State()
    experience = State()
    likes = State()
    dislikes = State()
    moods = State()
    about = State()
    categories = State()


# ------------------------------------------------------------------ /start
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.ensure_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    user = await db.get_user(message.from_user.id)

    # Уже зарегистрирован -> сразу меню
    if user and user["is_registered"]:
        # сбрасываем флаг блокировки, раз человек снова пишет
        if user["is_blocked"]:
            await db.mark_blocked(message.from_user.id, False)
        await message.answer(texts.MENU_GREETING, reply_markup=main_menu_kb())
        return

    await message.answer(texts.WELCOME)
    await message.answer(texts.AGE_GATE, reply_markup=age_gate_kb())


@router.callback_query(F.data == "age:no")
async def age_no(call: CallbackQuery):
    await call.message.edit_text(texts.AGE_DENIED)
    await call.answer()


@router.callback_query(F.data == "age:yes")
async def age_yes(call: CallbackQuery, state: FSMContext):
    await db.set_user_field(call.from_user.id, "is_adult_confirmed", 1)
    await call.message.edit_text("✅ Возраст подтверждён.")
    await call.message.answer(texts.DISCLAIMER)
    await call.message.answer(texts.ASK_AGE)
    await state.set_state(Reg.age)
    await call.answer()


# ------------------------------------------------------------------ возраст
@router.message(Reg.age)
async def reg_age(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(texts.AGE_INVALID)
        return
    age = int(raw)
    if age < 18:
        await message.answer(texts.AGE_TOO_YOUNG)
        return
    if age > 120:
        await message.answer(texts.AGE_INVALID)
        return
    await state.update_data(age=age)
    await message.answer(texts.ASK_EXPERIENCE, reply_markup=experience_kb())
    await state.set_state(Reg.experience)


# ------------------------------------------------------------------ стаж
@router.callback_query(Reg.experience, F.data.startswith("exp:"))
async def reg_experience(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split(":")[1])
    experience = texts.EXPERIENCE_OPTIONS[idx]
    await state.update_data(experience=experience)
    await call.message.edit_text(f"Стаж: {experience}")
    await call.message.answer(texts.ASK_LIKES)
    await state.set_state(Reg.likes)
    await call.answer()


# ------------------------------------------------------------------ любимые вкусы
@router.message(Reg.likes)
async def reg_likes(message: Message, state: FSMContext):
    await state.update_data(likes_text=(message.text or "").strip())
    await message.answer(texts.ASK_DISLIKES)
    await state.set_state(Reg.dislikes)


# ------------------------------------------------------------------ не нравится
@router.message(Reg.dislikes)
async def reg_dislikes(message: Message, state: FSMContext):
    await state.update_data(dislikes_text=(message.text or "").strip())
    await state.update_data(moods_sel=set())
    await message.answer(texts.ASK_MOODS, reply_markup=moods_kb(set()))
    await state.set_state(Reg.moods)


# ------------------------------------------------------------------ настроения (мультивыбор)
@router.callback_query(Reg.moods, F.data.startswith("mood:"))
async def reg_moods(call: CallbackQuery, state: FSMContext):
    arg = call.data.split(":")[1]
    data = await state.get_data()
    selected: set[int] = set(data.get("moods_sel", set()))

    if arg == "done":
        moods = [texts.MOOD_OPTIONS[i] for i in sorted(selected)]
        await state.update_data(moods="; ".join(moods))
        await call.message.edit_text(
            "Настроение: " + (", ".join(moods) if moods else "(не выбрано)")
        )
        await call.message.answer(texts.ASK_ABOUT)
        await state.set_state(Reg.about)
        await call.answer()
        return

    idx = int(arg)
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    await state.update_data(moods_sel=selected)
    await call.message.edit_reply_markup(reply_markup=moods_kb(selected))
    await call.answer()


# ------------------------------------------------------------------ о себе
@router.message(Reg.about)
async def reg_about(message: Message, state: FSMContext):
    await state.update_data(about_text=(message.text or "").strip())
    await state.update_data(cats_sel=set(CATEGORY_ORDER))  # по умолчанию все включены
    await message.answer(texts.ASK_CATEGORIES, reply_markup=categories_kb(set(CATEGORY_ORDER)))
    await state.set_state(Reg.categories)


# ------------------------------------------------------------------ категории (мультивыбор)
@router.callback_query(Reg.categories, F.data.startswith("cat:"))
async def reg_categories(call: CallbackQuery, state: FSMContext):
    arg = call.data.split(":")[1]
    data = await state.get_data()
    selected: set[str] = set(data.get("cats_sel", set()))

    if arg == "done":
        if not selected:
            await call.answer("Выбери хотя бы одну категорию", show_alert=True)
            return
        await _finish_registration(call, state, selected, data)
        return

    if arg in selected:
        selected.discard(arg)
    else:
        selected.add(arg)
    await state.update_data(cats_sel=selected)
    await call.message.edit_reply_markup(reply_markup=categories_kb(selected))
    await call.answer()


async def _finish_registration(call: CallbackQuery, state: FSMContext,
                               categories: set[str], data: dict):
    """Сохранить анкету, выставить категории, запустить анализ ИИ фоном."""
    user_id = call.from_user.id

    await db.save_profile(
        user_id=user_id,
        age=data.get("age"),
        experience=data.get("experience", ""),
        likes_text=data.get("likes_text", ""),
        dislikes_text=data.get("dislikes_text", ""),
        moods=data.get("moods", ""),
        about_text=data.get("about_text", ""),
    )
    await db.set_show_categories(
        user_id,
        ready="ready" in categories,
        constructor="constructor" in categories,
        disposable="disposable" in categories,
    )
    await db.set_user_field(user_id, "is_registered", 1)

    await call.message.edit_text("Категории сохранены ✅")
    await call.message.answer(texts.PROFILE_SAVED, reply_markup=main_menu_kb())

    # Если это РЕДАКТИРОВАНИЕ анкеты и она изменилась значимо (likes/dislikes/about)
    # — возвращаем ранее дизлайкнутые вкусы в ленту (по ТЗ).
    if data.get("editing"):
        if _profile_changed_significantly(data):
            returned = await db.reset_dislikes(user_id)
            await db.reset_weight_drift(user_id)
            if returned:
                await call.message.answer(texts.PROFILE_CHANGED_RESET)

    await state.clear()
    await call.answer()

    # Анализ анкеты ИИ -> стартовые веса. Делаем фоном, чтобы не блокировать ответ.
    asyncio.create_task(_analyze_and_store_weights(user_id, data))


def _profile_changed_significantly(data: dict) -> bool:
    """
    Считаем анкету «значительно изменённой», если изменился хотя бы один из
    смысловых текстовых ответов: любимые вкусы, нелюбимые, или поле «о себе».
    Сравнение без учёта регистра и крайних пробелов.
    """
    def norm(s) -> str:
        return (s or "").strip().lower()

    return (
        norm(data.get("likes_text")) != norm(data.get("old_likes"))
        or norm(data.get("dislikes_text")) != norm(data.get("old_dislikes"))
        or norm(data.get("about_text")) != norm(data.get("old_about"))
    )


async def _analyze_and_store_weights(user_id: int, data: dict):
    """Фоновая задача: получить веса от ИИ и сохранить. Ошибки ИИ не критичны."""
    try:
        weights = await analyze_profile(
            age=data.get("age"),
            experience=data.get("experience", ""),
            likes_text=data.get("likes_text", ""),
            dislikes_text=data.get("dislikes_text", ""),
            moods=data.get("moods", ""),
            about_text=data.get("about_text", ""),
        )
    except AIError:
        logger.info("ИИ недоступен при анализе анкеты user=%s — старт с нулевыми весами", user_id)
        return
    if weights:
        await db.bulk_set_tag_weights(user_id, weights)
        logger.info("Стартовые веса для user=%s: %s", user_id, weights)
