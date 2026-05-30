"""
ai/profiler.py — анализ анкеты пользователя (Задача 2 из ТЗ).

Вход: ответы анкеты (особое внимание свободному полю «о себе»).
Выход: стартовые веса по тем же тегам, диапазон −5..+10.

Пример из ТЗ: «люблю кофе и тёмный шоколад» -> повышает веса «кофейный»,
«десертный», «шоколадный», «горький/насыщенный».

При недоступности ИИ кидается AIError — вызывающий код просто стартует
с нулевыми весами (холодный старт алгоритма подбора это поддерживает).
"""

from ai import tags as T
from ai.client import chat_json, AIError  # noqa: F401

_SYSTEM = (
    "Ты — аналитик вкусовых предпочтений. По анкете пользователя выставь стартовые "
    "веса по тегам вкусов. Положительный вес = пользователю это нравится, "
    "отрицательный = не нравится. Отвечай только JSON-объектом."
)


def _build_prompt(age, experience, likes_text, dislikes_text, moods, about_text) -> str:
    return (
        "Анкета пользователя:\n"
        f"- Возраст: {age}\n"
        f"- Стаж парения: {experience}\n"
        f"- Любимые вкусы: {likes_text or '(не указано)'}\n"
        f"- Что НЕ нравится: {dislikes_text or '(не указано)'}\n"
        f"- Настроение/повод: {moods or '(не указано)'}\n"
        f"- О себе (анализируй особенно внимательно): {about_text or '(не указано)'}\n\n"
        "Выставь веса от -5 до +10 по тегам, выбирая ТОЛЬКО из списков:\n"
        f"Вкусовой профиль: {', '.join(T.PROFILE_TAGS)}\n"
        f"Свойства: {', '.join(T.PROPERTY_TAGS)}\n"
        f"Настроение: {', '.join(T.MOOD_TAGS)}\n\n"
        "Верни плоский JSON {\"тег\": вес, ...}. Указывай только значимые теги "
        "(те, по которым есть явный сигнал из анкеты). Пример: "
        '{"кофейный": 8, "шоколадный": 6, "насыщенность": 7, "ментоловый": -3}.'
    )


def _clamp(value, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, v))


def _normalize(parsed) -> dict[str, float]:
    """Оставить только известные теги и обрезать веса в диапазон [-5, +10]."""
    result: dict[str, float] = {}
    if isinstance(parsed, dict):
        # поддержим как плоский, так и вложенный формат
        flat: dict = {}
        for key, val in parsed.items():
            if isinstance(val, dict):
                flat.update(val)
            else:
                flat[key] = val
        for tag, value in flat.items():
            if T.is_known_tag(tag):
                w = _clamp(value, -5, 10)
                if w != 0:
                    result[tag] = round(w, 2)
    return result


async def analyze_profile(age, experience, likes_text, dislikes_text,
                          moods, about_text) -> dict[str, float]:
    """
    Получить стартовые веса от ИИ. Возвращает {tag: weight}.
    Кидает AIError при недоступности ИИ.
    """
    parsed = await chat_json(
        _SYSTEM,
        _build_prompt(age, experience, likes_text, dislikes_text, moods, about_text),
        temperature=0.3,
    )
    return _normalize(parsed)
