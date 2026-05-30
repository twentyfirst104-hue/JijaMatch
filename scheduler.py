"""
scheduler.py — фоновые задачи (APScheduler):

1. Уведомления о платном статусе:
   - за 3 дня до окончания (один раз, флаг paid_notified_3days);
   - в день/после окончания (один раз, флаг paid_notified_expired).
   Уведомления идут АДМИНУ (он управляет статусом). Каждое — ровно один раз.

2. Ежедневный автобэкап БД -> файл админу в личку (час задаётся DAILY_BACKUP_HOUR).

Планировщик создаётся и стартует из bot.py. Все задачи устойчивы к ошибкам:
исключение в одной задаче не роняет бота.
"""

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import database as db
from handlers.admin import notify_admin, make_backup
from aiogram.types import FSInputFile

logger = logging.getLogger("scheduler")


async def _check_paid_expirations(bot):
    """Проверка истечения платных статусов и отправка одноразовых уведомлений."""
    try:
        # За 3 дня: окно [now+0, now+3 дня], кому ещё не слали уведомление за 3 дня
        soon = await db.producers_expiring_in(0, 3 * 86400)
        for u in soon:
            if not u["paid_notified_3days"]:
                producer = await db.get_producer_by_owner(u["tg_id"])
                name = producer["name"] if producer else str(u["tg_id"])
                await notify_admin(
                    bot,
                    f"⏳ У производителя «{name}» платный статус истекает в течение 3 дней.",
                )
                await db.set_user_field(u["tg_id"], "paid_notified_3days", 1)

        # Истёкшие: один раз уведомляем и больше не трогаем
        expired = await db.producers_just_expired()
        for u in expired:
            producer = await db.get_producer_by_owner(u["tg_id"])
            name = producer["name"] if producer else str(u["tg_id"])
            await notify_admin(
                bot,
                f"🔚 У производителя «{name}» платный статус закончился. "
                f"Ссылка/приоритет/отметка/расширенная статистика отключены "
                f"(товары остаются в каталоге).",
            )
            await db.set_user_field(u["tg_id"], "paid_notified_expired", 1)
    except Exception as e:  # noqa: BLE001 — фоновая задача не должна ронять процесс
        logger.exception("Ошибка проверки платных статусов: %s", e)


async def _daily_backup(bot):
    """Ежедневный автобэкап БД с отправкой админу."""
    try:
        path = await make_backup()
        if path and os.path.exists(path) and config.ADMIN_ID > 0:
            await bot.send_document(config.ADMIN_ID, FSInputFile(path),
                                    caption="📦 Автоматический ежедневный бэкап БД")
    except Exception as e:  # noqa: BLE001
        logger.exception("Ошибка автобэкапа: %s", e)


def create_scheduler(bot) -> AsyncIOScheduler:
    """Создать и сконфигурировать планировщик (старт — в bot.py)."""
    scheduler = AsyncIOScheduler()

    # Проверка платных статусов — раз в час
    scheduler.add_job(_check_paid_expirations, "interval", hours=1, args=[bot],
                      id="paid_check", replace_existing=True)

    # Автобэкап — ежедневно в заданный час
    scheduler.add_job(_daily_backup, "cron", hour=config.DAILY_BACKUP_HOUR, args=[bot],
                      id="daily_backup", replace_existing=True)

    return scheduler
