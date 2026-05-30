"""
config.py — единая точка конфигурации проекта.

Здесь читаются переменные окружения из .env (через python-dotenv) и
складываются в простые модульные константы. Любой другой модуль импортирует
нужные значения отсюда, а не лезет в os.environ напрямую — так проще менять.

Если какого-то значения нет — берётся разумное значение по умолчанию,
а критичные (BOT_TOKEN, ADMIN_ID) проверяются функцией validate().
"""

import os
from dotenv import load_dotenv

# Загружаем .env, если он есть. В проде (Amvera) переменные могут быть заданы
# через панель — load_dotenv просто ничего не сделает, и это нормально.
load_dotenv()


def _get_int(name: str, default: int) -> int:
    """Безопасно прочитать целое число из окружения."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    """Безопасно прочитать число с плавающей точкой из окружения."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --- Telegram ---
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = _get_int("ADMIN_ID", 0)
ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "").lstrip("@")

# --- OpenRouter / ИИ ---
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat:free")

# Фолбэк-модели: строка "a,b,c" -> список без пустых элементов.
_fallback_raw = os.getenv(
    "OPENROUTER_FALLBACK_MODELS",
    "meta-llama/llama-3.3-70b-instruct:free,"
    "google/gemini-2.0-flash-exp:free,"
    "qwen/qwen-2.5-7b-instruct:free",
)
OPENROUTER_FALLBACK_MODELS: list[str] = [
    m.strip() for m in _fallback_raw.split(",") if m.strip()
]

# Заголовки, которые OpenRouter рекомендует слать (необязательны).
OPENROUTER_HTTP_REFERER: str = os.getenv("OPENROUTER_HTTP_REFERER", "")
OPENROUTER_APP_TITLE: str = os.getenv("OPENROUTER_APP_TITLE", "VapeFlavorCatalog")

# Таймаут одного запроса к ИИ и параметры retry.
AI_REQUEST_TIMEOUT: float = _get_float("AI_REQUEST_TIMEOUT", 60.0)
AI_MAX_RETRIES: int = _get_int("AI_MAX_RETRIES", 3)

# --- Коэффициенты алгоритма ---
LIKE_WEIGHT_COEFFICIENT: float = _get_float("LIKE_WEIGHT_COEFFICIENT", 1.0)
FAVORITE_WEIGHT_COEFFICIENT: float = _get_float("FAVORITE_WEIGHT_COEFFICIENT", 1.5)
# Трактуем как «+30% бонуса к положительной части score», см. services/matching.py
PAID_PRIORITY_COEFFICIENT: float = _get_float("PAID_PRIORITY_COEFFICIENT", 1.3)
# Накопленная сумма абсолютных изменений весов, после которой дизлайки возвращаются
DISLIKE_RESET_THRESHOLD: float = _get_float("DISLIKE_RESET_THRESHOLD", 15.0)
# Мягкий лимит товаров для бесплатного производителя
FREE_PRODUCER_FLAVOR_LIMIT: int = _get_int("FREE_PRODUCER_FLAVOR_LIMIT", 20)
# Раз в N свайпов ИИ пересматривает профиль (для MVP не критично)
PROFILE_REWEIGHT_EVERY_N_SWIPES: int = _get_int("PROFILE_REWEIGHT_EVERY_N_SWIPES", 30)
# Из скольки лучших кандидатов случайно выбираем карточку (разнообразие ленты)
MATCH_TOP_N: int = _get_int("MATCH_TOP_N", 10)

# --- Хранилище ---
# Путь к файлу БД. Папку создаём автоматически в database.py.
DB_PATH: str = os.getenv("DB_PATH", "data/bot.db")
# Куда складывать бэкапы перед отправкой админу
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "backups")

# Время ежедневного автобэкапа (час по серверному времени)
DAILY_BACKUP_HOUR: int = _get_int("DAILY_BACKUP_HOUR", 4)


def validate() -> list[str]:
    """
    Проверяет критичные настройки. Возвращает список проблем (пустой = всё ок).
    Вызывается при старте bot.py, чтобы дать заказчику понятную ошибку.
    """
    problems: list[str] = []
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456:ABC"):
        problems.append("BOT_TOKEN не задан в .env (получите у @BotFather).")
    if ADMIN_ID <= 0:
        problems.append("ADMIN_ID не задан в .env (узнайте у @userinfobot).")
    if not OPENROUTER_API_KEY:
        problems.append(
            "OPENROUTER_API_KEY не задан — ИИ-функции работать не будут "
            "(бот запустится, теги/описания будут пустыми до /retag)."
        )
    return problems
