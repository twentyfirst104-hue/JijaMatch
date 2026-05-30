"""
bot.py — точка входа.

Что делает при запуске:
1. Настраивает логирование.
2. Проверяет конфигурацию (config.validate()) и понятно сообщает о проблемах.
3. Инициализирует БД (миграции, кеш тегов).
4. Создаёт Bot + Dispatcher (память для FSM), подключает все роутеры.
5. Запускает планировщик фоновых задач (подписки, уведомления, автобэкап).
6. Стартует long polling. На остановке аккуратно закрывает БД и планировщик.

Запуск:  python bot.py   (предварительно активируйте виртуальное окружение).
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
import database as db
from handlers import setup_routers
from scheduler import create_scheduler


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Понижаем «болтливость» сторонних библиотек
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def main() -> None:
    setup_logging()
    log = logging.getLogger("bot")

    # 1. Проверка конфигурации
    problems = config.validate()
    # Отсутствие OPENROUTER_API_KEY — не критично (бот работает без ИИ), остальное критично.
    critical = [p for p in problems if "OPENROUTER_API_KEY" not in p]
    for p in problems:
        log.warning("Конфигурация: %s", p)
    if critical:
        log.error("Невозможно запустить бота — исправьте .env. Подробности выше.")
        return

    # 2. Инициализация БД
    await db.init_db()
    log.info("База данных инициализирована: %s", config.DB_PATH)

    # 3. Bot + Dispatcher
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    for router in setup_routers():
        dp.include_router(router)

    # 4. Планировщик
    scheduler = create_scheduler(bot)
    scheduler.start()
    log.info("Планировщик фоновых задач запущен.")

    # 5. Polling
    try:
        log.info("Бот запущен. Ожидаю сообщения...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close_db()
        await bot.session.close()
        log.info("Бот остановлен, ресурсы освобождены.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
