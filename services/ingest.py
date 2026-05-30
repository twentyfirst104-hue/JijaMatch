"""
services/ingest.py — обработка нового вкуса ИИ: тегирование + базовое описание.

Вызывается после создания вкуса (и админом, и производителем). Делает:
1. ИИ-тегирование -> сохраняет теги (обновляет кеш).
2. Генерацию базового ИИ-описания -> сохраняет в flavors.ai_base_description.

При недоступности ИИ — НЕ роняет процесс: ставит флаг needs_retag=1, чтобы вкус
позже дотегировала команда админа /retag. Вкус при этом уже в каталоге.

Запускать удобно фоном (asyncio.create_task), чтобы пользователь/админ не ждал.
"""

import logging

import database as db
from ai.tagger import tag_flavor, AIError as TagError
from ai.describer import generate_base_description, AIError as DescError

logger = logging.getLogger("services.ingest")


async def process_new_flavor(flavor_id: int) -> None:
    """ИИ-тегирование + базовое описание для одного вкуса. Безопасно к ошибкам ИИ."""
    flavor = await db.get_flavor_full(flavor_id)
    if not flavor:
        return

    name = flavor["name"]
    description = flavor["description"] or ""
    category = flavor["category"]

    ai_failed = False

    # 1. Тегирование
    try:
        tags = await tag_flavor(name, description, category)
        await db.set_flavor_tags(flavor_id, tags)
        logger.info("Вкус %s протегирован: %s", flavor_id, tags)
    except TagError:
        ai_failed = True
        logger.info("ИИ недоступен для тегирования вкуса %s — отложено в /retag", flavor_id)

    # 2. Базовое описание
    try:
        base = await generate_base_description(name, description, category)
        if base:
            await db.update_flavor_field(flavor_id, "ai_base_description", base)
    except DescError:
        ai_failed = True
        logger.info("ИИ недоступен для описания вкуса %s — отложено в /retag", flavor_id)

    if ai_failed:
        await db.update_flavor_field(flavor_id, "needs_retag", 1)


async def retag_all(line_id: int | None = None) -> tuple[int, int]:
    """
    Дотегировать + догенерировать описания для всех вкусов без них (или одной линейки).
    Возвращает (обработано_успешно, всего_в_очереди). Используется командой /retag.
    """
    pending = await db.flavors_needing_retag(line_id)
    total = len(pending)
    ok = 0
    for row in pending:
        before = await db.get_flavor(row["id"])
        await process_new_flavor(row["id"])
        after = await db.get_flavor(row["id"])
        # Считаем успехом, если флаг needs_retag снят
        if after and not after["needs_retag"]:
            ok += 1
    return ok, total
