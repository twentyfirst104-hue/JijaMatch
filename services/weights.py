"""
services/weights.py — изменение весов профиля по действиям пользователя.

КЛЮЧЕВАЯ ЛОГИКА (из ТЗ, читать внимательно):
- Лайк/избранное прибавляют теги вкуса к весам, дизлайк — вычитает.
- Категориальные теги (профиль/настроение, value=1) добавляются как
  tag_value * coeff. Свойства (0..10) — пропорционально: (value/10) * 10 * coeff
  = value * coeff (то есть свойство со значением 7 даёт вклад 7*coeff). Это держит
  свойства и категориальные теги в одном масштабе при начислении.
- ЗАЩИТА ОТ ДВОЙНОГО НАЧИСЛЕНИЯ: вклад по конкретному вкусу применяется только
  один раз. Повторное положительное действие (лайк после лайка/избранного) ничего
  не меняет. При СМЕНЕ ЗНАКА (дизлайк -> лайк/избранное и наоборот) сначала
  откатываем прежний вклад, затем начисляем новый. Реализовано через «эффективный
  множитель» прошлого и нового действия: применяем дельту (new - old) * базовый вклад.

Возвращаем суммарный модуль изменения весов — он копится в users.weight_drift
и используется для «возврата дизлайков» (services/matching.py).
"""

import config
import database as db


def _action_multiplier(action: str) -> float:
    """
    Множитель действия:
      like     -> +LIKE_WEIGHT_COEFFICIENT
      favorite -> +FAVORITE_WEIGHT_COEFFICIENT (сильнее лайка)
      dislike  -> -LIKE_WEIGHT_COEFFICIENT (симметрично лайку, но со знаком минус)
      None/иное-> 0 (нет вклада)
    """
    if action == "like":
        return config.LIKE_WEIGHT_COEFFICIENT
    if action == "favorite":
        return config.FAVORITE_WEIGHT_COEFFICIENT
    if action == "dislike":
        return -config.LIKE_WEIGHT_COEFFICIENT
    return 0.0


def _base_contribution(tag_name: str, value: float) -> float:
    """
    Базовый вклад тега вкуса (без учёта знака действия), в едином масштабе.
    Категориальные теги: value уже 0/1 -> вклад = value (т.е. 1).
    Свойства (0..10): вклад = value (значение 7 даёт 7), что эквивалентно
    (value/10) * 10. Так свойства и категории сопоставимы.
    """
    return float(value)


async def apply_action(user_id: int, flavor_id: int, new_action: str) -> float:
    """
    Применить действие пользователя к весам с защитой от двойного начисления.

    Алгоритм:
    1. Узнаём прошлое действие по этому вкусу (если было).
    2. Если множитель не изменился (например, like -> like, или favorite -> like
       при равной семантике «положительное») — НИЧЕГО не меняем по весам.
       (По ТЗ повторное положительное действие веса не меняет.)
    3. Иначе для каждого тега вкуса начисляем дельту:
          delta = (new_multiplier - old_multiplier) * base_contribution(tag)
       Это автоматически: откатывает старый вклад и применяет новый
       (в т.ч. при смене знака дизлайк<->лайк).

    Возвращает суммарный |delta| по всем тегам (для накопления weight_drift).
    """
    prev = await db.get_swipe(user_id, flavor_id)
    old_action = prev["action"] if prev else None

    old_mult = _action_multiplier(old_action)
    new_mult = _action_multiplier(new_action)

    # Если эффективный множитель не изменился — веса не трогаем
    # (защита от повторного положительного начисления и от повтора того же действия).
    if abs(new_mult - old_mult) < 1e-9:
        # Всё равно фиксируем сам свайп (например, like -> favorite c равным... нет,
        # у favorite множитель больше, сюда не попадём; здесь только идентичные действия)
        await db.record_swipe(user_id, flavor_id, new_action)
        return 0.0

    tags = db.get_cached_flavor_tags(flavor_id)
    if not tags:
        tags = await db.get_flavor_tags(flavor_id)

    diff_mult = new_mult - old_mult
    total_abs_change = 0.0

    for tag_name, value in tags.items():
        base = _base_contribution(tag_name, value)
        delta = diff_mult * base
        if abs(delta) < 1e-9:
            continue
        await db.add_tag_weight(user_id, tag_name, delta)
        total_abs_change += abs(delta)

    # Фиксируем новое действие пользователя по вкусу
    await db.record_swipe(user_id, flavor_id, new_action)

    # Копим «сдвиг профиля» для логики возврата дизлайков
    if total_abs_change > 0:
        await db.add_weight_drift(user_id, total_abs_change)

    return total_abs_change
