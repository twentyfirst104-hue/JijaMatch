"""
services/matching.py — алгоритм подбора следующей карточки.

Реализует ровно то, что описано в ТЗ:

score = score_категориальных_тегов + score_свойств   (с нормализацией)

Нормализация (обязательно):
- Свойства (0..10): (значение/10) * вес_пользователя_по_свойству.
- Категориальные (профиль/настроение, 0/1): вес_пользователя * наличие.

Приоритет платных: ДОБАВОЧНЫЙ бонус к ПОЛОЖИТЕЛЬНОЙ части score
(+30% при PAID_PRIORITY_COEFFICIENT=1.3), а не умножение всего score.

Разнообразие: выбираем случайно из топ-N кандидатов (config.MATCH_TOP_N).

Холодный старт: если у всех score ≈ 0 — случайный порядок/новизна.

Возврат дизлайков: если накопленный weight_drift пользователя превысил
DISLIKE_RESET_THRESHOLD — дизлайкнутые карточки возвращаются в очередь
(их свайпы удаляются), drift сбрасывается. Логика вызывается из get_next_flavor.

Категории показа учитываются через выбранные пользователем show_* флаги.
Берём только approved-вкусы в approved-линейках.
"""

import random

import config
import database as db
from ai import tags as T


# Соответствие флагов пользователя категориям линеек
_CATEGORY_BY_FLAG = {
    "show_ready": "ready",
    "show_constructor": "constructor",
    "show_disposable": "disposable",
}


def _allowed_categories(user_row) -> list[str]:
    cats = []
    if user_row["show_ready"]:
        cats.append("ready")
    if user_row["show_constructor"]:
        cats.append("constructor")
    if user_row["show_disposable"]:
        cats.append("disposable")
    return cats


def score_flavor(flavor_tags: dict[str, float], user_weights: dict[str, float]) -> float:
    """
    Посчитать «сырой» score вкуса под профиль пользователя (без бонуса платных).
    Категориальные и свойства нормализуются по-разному (см. модульный docstring).
    """
    score = 0.0
    for tag_name, value in flavor_tags.items():
        weight = user_weights.get(tag_name, 0.0)
        if weight == 0.0:
            continue
        if T.is_property_tag(tag_name):
            # свойство 0..10 -> нормируем в долю и умножаем на вес пользователя
            score += (value / 10.0) * weight
        else:
            # категориальный: наличие(0/1) * вес пользователя
            score += value * weight
    return score


def apply_paid_bonus(raw_score: float, is_paid: bool) -> float:
    """
    Приоритет платных: добавочный бонус к ПОЛОЖИТЕЛЬНОЙ части score.
    PAID_PRIORITY_COEFFICIENT=1.3 трактуем как +30% от положительной части.
    Отрицательный score не «улучшаем» — иначе выдача исказится.
    """
    if not is_paid:
        return raw_score
    if raw_score <= 0:
        return raw_score
    bonus = (config.PAID_PRIORITY_COEFFICIENT - 1.0) * raw_score
    return raw_score + bonus


async def _candidate_rows(user_row) -> list:
    """
    Кандидаты: approved-вкусы в approved-линейках выбранных категорий,
    которые пользователь ещё не свайпал.
    Подтягиваем owner_user_id производителя для определения платного статуса.
    """
    cats = _allowed_categories(user_row)
    if not cats:
        return []

    placeholders = ",".join("?" for _ in cats)
    query = f"""
        SELECT f.id AS flavor_id, p.owner_user_id AS owner_user_id
        FROM flavors f
        JOIN lines l ON f.line_id = l.id
        JOIN producers p ON l.producer_id = p.id
        WHERE f.status='approved' AND l.status='approved'
          AND l.category IN ({placeholders})
          AND f.id NOT IN (SELECT flavor_id FROM swipes WHERE user_id=?)
    """
    params = [*cats, user_row["tg_id"]]
    async with db.db().execute(query, params) as cur:
        return list(await cur.fetchall())


async def get_next_flavor(user_id: int) -> int | None:
    """
    Вернуть flavor_id следующей карточки для пользователя или None, если показывать нечего.

    Порядок действий:
    1. Возврат дизлайков, если профиль значительно сместился (weight_drift).
    2. Собрать кандидатов, посчитать score (+ бонус платных).
    3. Холодный старт: если все score ≈ 0 — случайный порядок.
    4. Разнообразие: случайный выбор из топ-N.
    """
    user_row = await db.get_user(user_id)
    if not user_row:
        return None

    # 1. Возврат дизлайков при значительном сдвиге профиля
    if user_row["weight_drift"] >= config.DISLIKE_RESET_THRESHOLD:
        returned = await db.reset_dislikes(user_id)
        await db.reset_weight_drift(user_id)
        # перечитываем пользователя (drift обнулён)
        user_row = await db.get_user(user_id)

    candidates = await _candidate_rows(user_row)
    if not candidates:
        return None

    user_weights = await db.get_tag_weights(user_id)

    # Кеш платного статуса по владельцу, чтобы не дёргать БД повторно
    paid_cache: dict[int, bool] = {}

    async def owner_is_paid(owner_id) -> bool:
        if owner_id is None:
            return False  # карточки админа без владельца — не платные
        if owner_id not in paid_cache:
            paid_cache[owner_id] = await db.is_producer_paid(owner_id)
        return paid_cache[owner_id]

    scored: list[tuple[int, float]] = []
    for row in candidates:
        fid = row["flavor_id"]
        ftags = db.get_cached_flavor_tags(fid)
        raw = score_flavor(ftags, user_weights)
        final = apply_paid_bonus(raw, await owner_is_paid(row["owner_user_id"]))
        scored.append((fid, final))

    # 3. Холодный старт: все score близки к нулю -> случайный порядок
    max_abs = max((abs(s) for _, s in scored), default=0.0)
    if max_abs < 1e-6:
        return random.choice([fid for fid, _ in scored])

    # 4. Сортируем по убыванию score, берём топ-N, выбираем случайно (разнообразие)
    scored.sort(key=lambda x: x[1], reverse=True)
    top_n = scored[: max(1, config.MATCH_TOP_N)]
    return random.choice([fid for fid, _ in top_n])
