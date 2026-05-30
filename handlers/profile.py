"""
handlers/profile.py — «Моя анкета» (переделать) и «Мой вкусовой профиль».

Переделка анкеты:
- Запускаем тот же FSM, что и при регистрации (Reg), но помечаем в state, что это
  редактирование. После сохранения сравниваем likes/dislikes/about со старыми —
  при значительном изменении возвращаем дизлайки в ленту (по ТЗ).

«Мой вкусовой профиль» — показываем топ положительных/отрицательных весов
человеческим языком.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import texts
import database as db
from keyboards.main_menu import BTN_PROFILE, BTN_TASTE, main_menu_kb
from keyboards.registration import experience_kb
from handlers.registration import Reg

router = Router()


# ------------------------------------------------------------------ переделать анкету
@router.message(F.text == BTN_PROFILE)
async def reopen_profile(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    if not user or not user["is_registered"]:
        await message.answer("Сначала пройди регистрацию: /start")
        return

    # Запоминаем старые ответы, чтобы потом понять, изменилась ли анкета значимо
    old = await db.get_profile(message.from_user.id)
    await state.clear()
    await state.update_data(
        editing=True,
        old_likes=(old["likes_text"] if old else "") or "",
        old_dislikes=(old["dislikes_text"] if old else "") or "",
        old_about=(old["about_text"] if old else "") or "",
        age=old["age"] if old else None,
    )
    await message.answer(texts.PROFILE_REOPENED)
    # Стартуем с возраста (как в регистрации)
    await message.answer(texts.ASK_AGE)
    await state.set_state(Reg.age)


# ------------------------------------------------------------------ вкусовой профиль
@router.message(F.text == BTN_TASTE)
async def show_taste_profile(message: Message):
    weights = await db.get_tag_weights(message.from_user.id)
    # Отбираем значимые веса
    nonzero = {t: w for t, w in weights.items() if abs(w) > 0.01}
    if not nonzero:
        await message.answer(texts.TASTE_PROFILE_EMPTY, reply_markup=main_menu_kb())
        return

    liked = sorted([(t, w) for t, w in nonzero.items() if w > 0], key=lambda x: -x[1])[:8]
    disliked = sorted([(t, w) for t, w in nonzero.items() if w < 0], key=lambda x: x[1])[:5]

    lines = ["📊 <b>Твой вкусовой профиль</b>\n"]
    if liked:
        lines.append("✅ <b>Нравится:</b>")
        for t, w in liked:
            lines.append(f"   • {t} ({round(w, 1)})")
    if disliked:
        lines.append("\n🚫 <b>Не очень:</b>")
        for t, w in disliked:
            lines.append(f"   • {t} ({round(w, 1)})")
    lines.append("\nПрофиль уточняется по мере того, как ты листаешь вкусы 🙂")
    await message.answer("\n".join(lines), reply_markup=main_menu_kb())
