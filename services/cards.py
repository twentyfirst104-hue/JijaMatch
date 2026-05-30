"""
services/cards.py — формирование текста карточки вкуса и подбор ИИ-описания.

Карточка показывает: производителя, линейку, название, категорию, описание
производителя + ИИ-описание, для конструкторов — пропорцию/базу, у платных —
ссылку на канал и отметку «✓ Официальный производитель».

ИИ-описание выбирается по приоритету (см. resolve_ai_description):
1) персональное из кеша ai_descriptions (если есть);
2) базовое ai_base_description;
3) описание производителя (как последний фолбэк уже в самой карточке).

Генерация персонального описания — отдельной функцией ensure_personal_description,
вызывается неблокирующе из хендлера свайпа (показываем карточку сразу с базовым
описанием, персональное подгружаем фоном и при готовности дополняем сообщение).
"""

import html

import database as db
from ai.describer import generate_personal_description, AIError

# Подписи категорий (дублируем минимально, чтобы не тянуть texts в сервис)
_CATEGORY_LABELS = {
    "ready": "Готовая жидкость",
    "constructor": "Жидкость-конструктор",
    "disposable": "Одноразка",
}


def _esc(value) -> str:
    """HTML-экранирование пользовательского текста (parse_mode=HTML)."""
    return html.escape(str(value)) if value is not None else ""


async def resolve_ai_description(user_id: int, flavor_row) -> str | None:
    """Вернуть лучшее доступное ИИ-описание (персональное из кеша или базовое)."""
    personal = await db.get_cached_ai_description(user_id, flavor_row["id"])
    if personal:
        return personal
    if flavor_row["ai_base_description"]:
        return flavor_row["ai_base_description"]
    return None


async def build_card_text(user_id: int, flavor_row, is_paid: bool) -> str:
    """
    Собрать HTML-текст карточки. flavor_row — результат db.get_flavor_full().
    is_paid — активен ли платный статус у владельца производителя.
    """
    lines: list[str] = []

    # Заголовок: бренд + отметка официального
    brand = _esc(flavor_row["producer_name"])
    if is_paid:
        brand += " ✓ <i>Официальный производитель</i>"
    lines.append(f"🏷 <b>{brand}</b>")
    lines.append(f"📦 Линейка: {_esc(flavor_row['line_name'])}")
    lines.append(f"🍬 <b>{_esc(flavor_row['name'])}</b>")

    category = flavor_row["category"]
    lines.append(f"🗂 {_esc(_CATEGORY_LABELS.get(category, category))}")

    # Для конструкторов — пропорция/база
    if category == "constructor" and flavor_row["constructor_proportion"]:
        lines.append(f"⚗️ Пропорция/база: {_esc(flavor_row['constructor_proportion'])}")

    # Описание производителя
    if flavor_row["description"]:
        lines.append("")
        lines.append(f"📝 {_esc(flavor_row['description'])}")

    # ИИ-описание (персональное -> базовое)
    ai_desc = await resolve_ai_description(user_id, flavor_row)
    if ai_desc:
        lines.append("")
        lines.append(f"🤖 <i>{_esc(ai_desc)}</i>")

    # Ссылка на канал — только у платных
    if is_paid and flavor_row["channel_url"]:
        lines.append("")
        lines.append(f"📢 Канал: {_esc(flavor_row['channel_url'])}")

    return "\n".join(lines)


async def ensure_personal_description(user_id: int, flavor_row) -> str | None:
    """
    Сгенерировать (если ещё нет) и закешировать персональное ИИ-описание.
    Возвращает текст описания или None при недоступности ИИ.
    Безопасно вызывать из фоновой задачи: ошибки ИИ глотаются (фолбэк — базовое).
    """
    existing = await db.get_cached_ai_description(user_id, flavor_row["id"])
    if existing:
        return existing

    weights = await db.get_tag_weights(user_id)
    profile = await db.get_profile(user_id)
    about = profile["about_text"] if profile else None

    try:
        text = await generate_personal_description(
            name=flavor_row["name"],
            description=flavor_row["description"],
            base_description=flavor_row["ai_base_description"],
            user_weights=weights,
            about_text=about,
        )
    except AIError:
        return None  # фолбэк на базовое описание уже в карточке

    if text:
        await db.save_ai_description(user_id, flavor_row["id"], text)
    return text or None
