"""
ai/tagger.py — ИИ-тегирование вкуса (Задача 1 из ТЗ).

Вход: название, описание, категория вкуса.
Выход: словарь тегов СТРОГО из справочника ai/tags.py:
  - PROFILE_TAGS  -> 0/1 (есть/нет)
  - PROPERTY_TAGS -> 0..10
  - MOOD_TAGS     -> 0/1 (есть/нет)

Любой тег вне справочника отбрасывается (защита от «фантазий» модели).
При недоступности ИИ кидается AIError — вызывающий код сохраняет вкус без тегов
и ставит флаг needs_retag (см. handlers/обработка добавления).
"""

from ai import tags as T
from ai.client import chat_json, AIError  # noqa: F401  (AIError реэкспортируется для удобства)

_CATEGORY_RU = {
    "ready": "готовая жидкость",
    "constructor": "жидкость-конструктор",
    "disposable": "одноразка",
}

_SYSTEM = (
    "Ты — эксперт-сомелье по вкусам жидкостей для вейпа. "
    "Твоя задача — описать вкус набором тегов СТРОГО из заданных списков. "
    "Никаких новых тегов не придумывай. Отвечай только JSON-объектом."
)


def _build_prompt(name: str, description: str, category: str) -> str:
    cat = _CATEGORY_RU.get(category, category)
    return (
        f"Вкус: «{name}»\n"
        f"Категория: {cat}\n"
        f"Описание производителя: {description or '(нет описания)'}\n\n"
        "Проставь теги, выбирая ТОЛЬКО из этих списков:\n\n"
        f"1) Вкусовой профиль (для каждого укажи 1 если присутствует, 0 если нет):\n"
        f"   {', '.join(T.PROFILE_TAGS)}\n\n"
        f"2) Свойства (число от 0 до 10):\n"
        f"   {', '.join(T.PROPERTY_TAGS)}\n\n"
        f"3) Настроение/повод (1 если подходит, 0 если нет):\n"
        f"   {', '.join(T.MOOD_TAGS)}\n\n"
        "Верни JSON вида: "
        '{"profile": {"фруктовый": 1, ...}, "properties": {"сладость": 7, ...}, '
        '"mood": {"освежающий": 1, ...}}. '
        "Указывай только те теги, которые реально применимы; остальные можно опустить."
    )


def _clamp(value, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _normalize(parsed: dict) -> dict[str, float]:
    """
    Привести ответ модели к плоскому словарю {tag: value}, отбросив чужие теги
    и нормализовав значения.
    """
    result: dict[str, float] = {}
    if not isinstance(parsed, dict):
        return result

    profile = parsed.get("profile", {}) or {}
    properties = parsed.get("properties", {}) or {}
    mood = parsed.get("mood", {}) or {}

    # Иногда модель отдаёт всё плоско — поддержим и такой вариант
    flat = {**profile, **properties, **mood}
    if not (isinstance(profile, dict) or isinstance(properties, dict) or isinstance(mood, dict)):
        flat = parsed

    for tag, value in flat.items():
        if not T.is_known_tag(tag):
            continue  # отбрасываем теги вне справочника
        if T.is_property_tag(tag):
            v = _clamp(value, 0, 10)
            if v > 0:
                result[tag] = round(v, 2)
        else:
            # бинарный тег: считаем «есть», если значение истинно/≥0.5
            try:
                present = float(value) >= 0.5
            except (TypeError, ValueError):
                present = bool(value)
            if present:
                result[tag] = 1.0
    return result


async def tag_flavor(name: str, description: str, category: str) -> dict[str, float]:
    """
    Получить теги вкуса от ИИ. Возвращает {tag: value}.
    Кидает AIError, если ИИ недоступен/не дал валидный ответ.
    """
    parsed = await chat_json(_SYSTEM, _build_prompt(name, description, category), temperature=0.2)
    return _normalize(parsed)
