"""
Пакет хендлеров. Функция setup_routers() собирает все роутеры в нужном порядке
и возвращает список для подключения к Dispatcher в bot.py.

Порядок важен: специфичные роутеры (регистрация/админ/производитель с FSM)
идут раньше общих, чтобы их состояния перехватывались первыми.
"""

from aiogram import Router

from handlers import (
    registration, swipe, favorites, profile,
    producer, admin, admin_edit, moderation, common,
)


def setup_routers() -> list[Router]:
    return [
        registration.router,
        admin.router,
        admin_edit.router,
        moderation.router,
        producer.router,
        swipe.router,
        favorites.router,
        profile.router,
        common.router,  # общий — последним (ловит прочее)
    ]
