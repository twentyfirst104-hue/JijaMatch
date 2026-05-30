"""
ai/describer.py — генерация ИИ-описаний вкуса (Задача 3 из ТЗ).

Два вида описаний:
1. БАЗОВОЕ (не персональное) — generate_base_description():
   создаётся один раз при добавлении вкуса (вместе с тегированием),
   сохраняется в flavors.ai_base_description, показывается мгновенно как фолбэк.

2. ПЕРСОНАЛЬНОЕ — generate_personal_description():
   создаётся один раз при первом показе вкуса конкретному пользователю,
   кешируется в ai_descriptions. Опирается на вкусовой профиль и анкету.

ЮРИДИЧЕСКИ ВАЖНО: описание НЕ должно упоминать покупку/цену/где купить.
Это явно прописано в системном промте.

При недоступности ИИ кидается AIError — вызывающий код показывает базовое
описание, а если и его нет — описание производителя.
"""

from ai.client import chat_text, AIError  # noqa: F401

_NO_BUY_RULE = (
    "СТРОГО ЗАПРЕЩЕНО упоминать покупку, цену, магазины, доставку, «где купить», "
    "скидки и любые призывы к приобретению. Это только описание вкусовых ощущений."
)

_SYSTEM_BASE = (
    "Ты — мастер вкусовых описаний. Опиши вкус живо и аппетитно, "
    "2–4 коротких предложения, тёплым языком. " + _NO_BUY_RULE
)

_SYSTEM_PERSONAL = (
    "Ты — личный вкусовой консультант. Опиши вкус для конкретного человека, "
    "опираясь на его предпочтения. 2–4 коротких предложения, тёплым дружеским "
    "языком, расскажи кому именно зайдёт этот вкус. " + _NO_BUY_RULE
)


async def generate_base_description(name: str, description: str, category: str) -> str:
    """Базовое (не персональное) ИИ-описание вкуса."""
    prompt = (
        f"Вкус: «{name}»\n"
        f"Описание производителя: {description or '(нет)'}\n\n"
        "Напиши собственное живое описание вкусовых ощущений."
    )
    return await chat_text(_SYSTEM_BASE, prompt, temperature=0.8)


def _profile_summary(weights: dict[str, float]) -> str:
    """Короткое текстовое резюме вкусового профиля по весам (топ-предпочтения)."""
    if not weights:
        return "пока без выраженных предпочтений"
    liked = sorted([(t, w) for t, w in weights.items() if w > 0], key=lambda x: -x[1])[:5]
    disliked = sorted([(t, w) for t, w in weights.items() if w < 0], key=lambda x: x[1])[:3]
    parts = []
    if liked:
        parts.append("любит: " + ", ".join(t for t, _ in liked))
    if disliked:
        parts.append("не любит: " + ", ".join(t for t, _ in disliked))
    return "; ".join(parts) if parts else "пока без выраженных предпочтений"


async def generate_personal_description(name: str, description: str,
                                        base_description: str | None,
                                        user_weights: dict[str, float],
                                        about_text: str | None) -> str:
    """
    Персональное ИИ-описание под профиль пользователя.
    base_description передаём как контекст, чтобы держать единый «характер» вкуса.
    """
    prompt = (
        f"Вкус: «{name}»\n"
        f"Описание производителя: {description or '(нет)'}\n"
        f"Базовое описание вкуса: {base_description or '(нет)'}\n"
        f"Профиль пользователя: {_profile_summary(user_weights)}\n"
        f"Пользователь о себе: {about_text or '(не указано)'}\n\n"
        "Напиши тёплое персональное описание: какие ощущения подарит этот вкус "
        "именно такому человеку и почему ему может понравиться."
    )
    return await chat_text(_SYSTEM_PERSONAL, prompt, temperature=0.85)
