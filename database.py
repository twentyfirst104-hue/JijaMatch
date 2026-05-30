"""
database.py — слой доступа к данным (SQLite через aiosqlite).

Содержит:
- init_db(): создаёт папку, таблицы, индексы, выполняет лёгкие миграции
  (добавление недостающих колонок без потери данных), включает FK.
- Набор async-функций для всех операций бота: пользователи, профили, веса,
  производители, линейки, вкусы, теги, свайпы, избранное, логи модерации/правок.
- Кеш тегов вкусов в памяти (теги меняются редко) — ускоряет алгоритм подбора.

Архитектурное решение (отмечаю явно, в ТЗ не уточнено): используем ОДНО
постоянное соединение aiosqlite на всё приложение. aiogram-хендлеры обращаются
к нему последовательно (внутри одного процесса/loop), что для SQLite безопасно
и проще, чем пул. Если нагрузка вырастет — можно перейти на пул соединений.
"""

import os
import time
import aiosqlite

import config
from ai.tags import is_property_tag

# Единственное соединение на всё приложение. Инициализируется в init_db().
_db: aiosqlite.Connection | None = None

# --- Кеш тегов вкусов в памяти ---
# Структура: { flavor_id: { tag_name: value, ... }, ... }
# Заполняется в init_db() и обновляется точечно при изменении тегов вкуса.
# Используется алгоритмом подбора, чтобы не дёргать БД на каждый кандидат.
_flavor_tags_cache: dict[int, dict[str, float]] = {}


def db() -> aiosqlite.Connection:
    """Вернуть активное соединение. Бросает, если init_db ещё не вызван."""
    if _db is None:
        raise RuntimeError("База данных не инициализирована. Вызовите init_db().")
    return _db


# ========================================================================
# Инициализация и миграции
# ========================================================================

# DDL всех таблиц. IF NOT EXISTS делает повторный запуск безопасным.
_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at INTEGER,
        is_registered INTEGER DEFAULT 0,
        is_adult_confirmed INTEGER DEFAULT 0,
        is_producer INTEGER DEFAULT 0,
        producer_paid_until INTEGER,            -- unix-время окончания платного статуса, NULL = бесплатный
        paid_notified_3days INTEGER DEFAULT 0,  -- флаг: уведомление за 3 дня уже отправлено
        paid_notified_expired INTEGER DEFAULT 0,-- флаг: уведомление об окончании уже отправлено
        is_blocked INTEGER DEFAULT 0,           -- пользователь заблокировал бота
        show_ready INTEGER DEFAULT 1,           -- показывать категорию «готовые жидкости»
        show_constructor INTEGER DEFAULT 1,     -- показывать «конструкторы»
        show_disposable INTEGER DEFAULT 1,      -- показывать «одноразки»
        swipes_since_reweight INTEGER DEFAULT 0,-- счётчик свайпов для периодической корректировки
        weight_drift REAL DEFAULT 0             -- накопленный «сдвиг профиля» для возврата дизлайков
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        age INTEGER,
        experience TEXT,
        likes_text TEXT,
        dislikes_text TEXT,
        moods TEXT,                 -- мультивыбор, хранится как CSV
        about_text TEXT,
        updated_at INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(tg_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tag_weights (
        user_id INTEGER,
        tag_name TEXT,
        weight REAL DEFAULT 0,
        UNIQUE(user_id, tag_name),
        FOREIGN KEY (user_id) REFERENCES users(tg_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS producers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        channel_url TEXT,
        owner_user_id INTEGER UNIQUE,   -- один пользователь = один производитель
        is_approved INTEGER DEFAULT 0,  -- одобрен ли сам производитель админом (антиспам)
        created_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producer_id INTEGER NOT NULL,
        category TEXT NOT NULL,           -- ready | constructor | disposable
        name TEXT NOT NULL,
        common_photo_file_id TEXT,
        created_by_user_id INTEGER,
        status TEXT DEFAULT 'pending',    -- approved | pending | rejected
        created_at INTEGER,
        updated_at INTEGER,
        FOREIGN KEY (producer_id) REFERENCES producers(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS flavors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        line_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        description TEXT,
        ai_base_description TEXT,         -- базовое ИИ-описание (не персональное)
        photo_file_id TEXT,
        constructor_proportion TEXT,      -- только для конструкторов
        status TEXT DEFAULT 'pending',    -- approved | pending | rejected
        needs_retag INTEGER DEFAULT 0,    -- ИИ был недоступен, нужно дотегировать
        created_at INTEGER,
        updated_at INTEGER,
        FOREIGN KEY (line_id) REFERENCES lines(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS flavor_tags (
        flavor_id INTEGER,
        tag_name TEXT,
        value REAL DEFAULT 0,
        UNIQUE(flavor_id, tag_name),
        FOREIGN KEY (flavor_id) REFERENCES flavors(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_descriptions (
        user_id INTEGER,
        flavor_id INTEGER,
        text TEXT,
        created_at INTEGER,
        UNIQUE(user_id, flavor_id),
        FOREIGN KEY (user_id) REFERENCES users(tg_id) ON DELETE CASCADE,
        FOREIGN KEY (flavor_id) REFERENCES flavors(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS swipes (
        user_id INTEGER,
        flavor_id INTEGER,
        action TEXT,                -- like | dislike | favorite
        created_at INTEGER,
        UNIQUE(user_id, flavor_id),
        FOREIGN KEY (user_id) REFERENCES users(tg_id) ON DELETE CASCADE,
        FOREIGN KEY (flavor_id) REFERENCES flavors(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER,
        flavor_id INTEGER,
        created_at INTEGER,
        UNIQUE(user_id, flavor_id),
        FOREIGN KEY (user_id) REFERENCES users(tg_id) ON DELETE CASCADE,
        FOREIGN KEY (flavor_id) REFERENCES flavors(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS moderation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT,
        entity_id INTEGER,
        action TEXT,
        comment TEXT,
        admin_id INTEGER,
        created_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS edit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT,
        entity_id INTEGER,
        editor_user_id INTEGER,
        field TEXT,
        old_value TEXT,
        new_value TEXT,
        created_at INTEGER
    )
    """,
    # Лог показов карточек — нужен для статистики (показы/конверсия)
    """
    CREATE TABLE IF NOT EXISTS impressions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        flavor_id INTEGER,
        created_at INTEGER
    )
    """,
]

# Индексы для производительности
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tag_weights_user ON tag_weights(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_flavor_tags_flavor ON flavor_tags(flavor_id)",
    "CREATE INDEX IF NOT EXISTS idx_swipes_user ON swipes(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_swipes_flavor ON swipes(flavor_id)",
    "CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_flavors_line ON flavors(line_id)",
    "CREATE INDEX IF NOT EXISTS idx_flavors_status ON flavors(status)",
    "CREATE INDEX IF NOT EXISTS idx_lines_producer ON lines(producer_id)",
    "CREATE INDEX IF NOT EXISTS idx_lines_status ON lines(status)",
    "CREATE INDEX IF NOT EXISTS idx_impressions_user ON impressions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_impressions_flavor ON impressions(flavor_id)",
]

# Лёгкие миграции: (таблица, колонка, DDL для ADD COLUMN).
# Применяются только если колонки ещё нет — это позволяет дополнять схему
# без потери данных у заказчика, у которого БД уже наполнена.
_MIGRATIONS = [
    ("users", "swipes_since_reweight", "ALTER TABLE users ADD COLUMN swipes_since_reweight INTEGER DEFAULT 0"),
    ("users", "weight_drift", "ALTER TABLE users ADD COLUMN weight_drift REAL DEFAULT 0"),
    ("flavors", "needs_retag", "ALTER TABLE flavors ADD COLUMN needs_retag INTEGER DEFAULT 0"),
    ("producers", "created_at", "ALTER TABLE producers ADD COLUMN created_at INTEGER"),
]


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    """Вернуть множество имён колонок таблицы (для миграций)."""
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return {row[1] for row in rows}


async def init_db() -> None:
    """Открыть соединение, создать схему/индексы, применить миграции, наполнить кеш тегов."""
    global _db

    # Создаём папку под файл БД, если нужно
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA foreign_keys = ON")   # включаем FK
    await _db.execute("PRAGMA journal_mode = WAL")  # надёжнее и быстрее при конкурентных чтениях

    for ddl in _SCHEMA:
        await _db.execute(ddl)
    for idx in _INDEXES:
        await _db.execute(idx)

    # Применяем миграции (добавление недостающих колонок)
    for table, column, ddl in _MIGRATIONS:
        cols = await _table_columns(_db, table)
        if column not in cols:
            await _db.execute(ddl)

    await _db.commit()

    await _load_flavor_tags_cache()


async def close_db() -> None:
    """Аккуратно закрыть соединение (вызывается при остановке бота)."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def now() -> int:
    """Текущее unix-время в секундах (единый формат времени по всему проекту)."""
    return int(time.time())


# ========================================================================
# Кеш тегов вкусов
# ========================================================================

async def _load_flavor_tags_cache() -> None:
    """Загрузить все теги вкусов в память (вызывается при старте)."""
    _flavor_tags_cache.clear()
    async with _db.execute("SELECT flavor_id, tag_name, value FROM flavor_tags") as cur:
        async for row in cur:
            _flavor_tags_cache.setdefault(row["flavor_id"], {})[row["tag_name"]] = row["value"]


def get_cached_flavor_tags(flavor_id: int) -> dict[str, float]:
    """Вернуть теги вкуса из кеша (пустой dict, если тегов нет)."""
    return _flavor_tags_cache.get(flavor_id, {})


def invalidate_flavor_tags_cache(flavor_id: int, tags: dict[str, float]) -> None:
    """Обновить кеш тегов конкретного вкуса (после перетегирования)."""
    if tags:
        _flavor_tags_cache[flavor_id] = dict(tags)
    else:
        _flavor_tags_cache.pop(flavor_id, None)


# ========================================================================
# Пользователи
# ========================================================================

async def ensure_user(tg_id: int, username: str | None, first_name: str | None) -> None:
    """Создать запись пользователя, если её нет; обновить username/first_name."""
    await _db.execute(
        """
        INSERT INTO users (tg_id, username, first_name, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username,
                                         first_name=excluded.first_name
        """,
        (tg_id, username, first_name, now()),
    )
    await _db.commit()


async def get_user(tg_id: int) -> aiosqlite.Row | None:
    async with _db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)) as cur:
        return await cur.fetchone()


async def set_user_field(tg_id: int, field: str, value) -> None:
    """Установить одно поле пользователя. field — из доверенного списка вызывающего кода."""
    await _db.execute(f"UPDATE users SET {field}=? WHERE tg_id=?", (value, tg_id))
    await _db.commit()


async def set_show_categories(tg_id: int, ready: bool, constructor: bool, disposable: bool) -> None:
    await _db.execute(
        "UPDATE users SET show_ready=?, show_constructor=?, show_disposable=? WHERE tg_id=?",
        (int(ready), int(constructor), int(disposable), tg_id),
    )
    await _db.commit()


async def all_user_ids(only_active: bool = False) -> list[int]:
    """Все ID пользователей (для рассылки). only_active — пропустить заблокировавших бота."""
    q = "SELECT tg_id FROM users WHERE is_registered=1"
    if only_active:
        q += " AND is_blocked=0"
    async with _db.execute(q) as cur:
        return [r["tg_id"] for r in await cur.fetchall()]


async def mark_blocked(tg_id: int, blocked: bool = True) -> None:
    await _db.execute("UPDATE users SET is_blocked=? WHERE tg_id=?", (int(blocked), tg_id))
    await _db.commit()


# ========================================================================
# Профили и веса
# ========================================================================

async def save_profile(user_id: int, age: int, experience: str, likes_text: str,
                       dislikes_text: str, moods: str, about_text: str) -> None:
    await _db.execute(
        """
        INSERT INTO profiles (user_id, age, experience, likes_text, dislikes_text,
                              moods, about_text, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            age=excluded.age, experience=excluded.experience,
            likes_text=excluded.likes_text, dislikes_text=excluded.dislikes_text,
            moods=excluded.moods, about_text=excluded.about_text,
            updated_at=excluded.updated_at
        """,
        (user_id, age, experience, likes_text, dislikes_text, moods, about_text, now()),
    )
    await _db.commit()


async def get_profile(user_id: int) -> aiosqlite.Row | None:
    async with _db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)) as cur:
        return await cur.fetchone()


async def set_tag_weight(user_id: int, tag_name: str, weight: float) -> None:
    """Установить абсолютное значение веса тега (UPSERT)."""
    await _db.execute(
        """
        INSERT INTO tag_weights (user_id, tag_name, weight) VALUES (?, ?, ?)
        ON CONFLICT(user_id, tag_name) DO UPDATE SET weight=excluded.weight
        """,
        (user_id, tag_name, weight),
    )
    await _db.commit()


async def add_tag_weight(user_id: int, tag_name: str, delta: float) -> None:
    """Прибавить delta к весу тега (создаёт строку при отсутствии)."""
    await _db.execute(
        """
        INSERT INTO tag_weights (user_id, tag_name, weight) VALUES (?, ?, ?)
        ON CONFLICT(user_id, tag_name) DO UPDATE SET weight=weight+excluded.weight
        """,
        (user_id, tag_name, delta),
    )
    await _db.commit()


async def get_tag_weights(user_id: int) -> dict[str, float]:
    async with _db.execute(
        "SELECT tag_name, weight FROM tag_weights WHERE user_id=?", (user_id,)
    ) as cur:
        return {r["tag_name"]: r["weight"] for r in await cur.fetchall()}


async def bulk_set_tag_weights(user_id: int, weights: dict[str, float]) -> None:
    """Установить сразу несколько весов (после анализа анкеты)."""
    for tag, w in weights.items():
        await _db.execute(
            """
            INSERT INTO tag_weights (user_id, tag_name, weight) VALUES (?, ?, ?)
            ON CONFLICT(user_id, tag_name) DO UPDATE SET weight=excluded.weight
            """,
            (user_id, tag, w),
        )
    await _db.commit()


async def add_weight_drift(user_id: int, delta_abs: float) -> float:
    """Накопить «сдвиг профиля» и вернуть новое значение."""
    await _db.execute(
        "UPDATE users SET weight_drift = weight_drift + ? WHERE tg_id=?",
        (delta_abs, user_id),
    )
    await _db.commit()
    u = await get_user(user_id)
    return u["weight_drift"] if u else 0.0


async def reset_weight_drift(user_id: int) -> None:
    await _db.execute("UPDATE users SET weight_drift=0 WHERE tg_id=?", (user_id,))
    await _db.commit()


# ========================================================================
# Производители
# ========================================================================

async def create_producer(name: str, description: str, channel_url: str | None,
                           owner_user_id: int | None, is_approved: bool) -> int:
    cur = await _db.execute(
        """
        INSERT INTO producers (name, description, channel_url, owner_user_id, is_approved, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, description, channel_url, owner_user_id, int(is_approved), now()),
    )
    await _db.commit()
    return cur.lastrowid


async def get_producer(producer_id: int) -> aiosqlite.Row | None:
    async with _db.execute("SELECT * FROM producers WHERE id=?", (producer_id,)) as cur:
        return await cur.fetchone()


async def get_producer_by_owner(owner_user_id: int) -> aiosqlite.Row | None:
    async with _db.execute(
        "SELECT * FROM producers WHERE owner_user_id=?", (owner_user_id,)
    ) as cur:
        return await cur.fetchone()


async def list_producers(approved: bool | None = None) -> list[aiosqlite.Row]:
    q = "SELECT * FROM producers"
    params: tuple = ()
    if approved is not None:
        q += " WHERE is_approved=?"
        params = (int(approved),)
    q += " ORDER BY name COLLATE NOCASE"
    async with _db.execute(q, params) as cur:
        return list(await cur.fetchall())


async def list_pending_producers() -> list[aiosqlite.Row]:
    """Производители, ожидающие одобрения админом (антиспам-очередь)."""
    async with _db.execute(
        "SELECT * FROM producers WHERE is_approved=0 AND owner_user_id IS NOT NULL "
        "ORDER BY created_at ASC"
    ) as cur:
        return list(await cur.fetchall())


async def approve_producer(producer_id: int) -> None:
    await _db.execute("UPDATE producers SET is_approved=1 WHERE id=?", (producer_id,))
    await _db.commit()


async def update_producer_field(producer_id: int, field: str, value) -> None:
    await _db.execute(f"UPDATE producers SET {field}=? WHERE id=?", (value, producer_id))
    await _db.commit()


async def is_producer_paid(user_id: int) -> bool:
    """True, если у пользователя активен платный статус производителя."""
    u = await get_user(user_id)
    if not u or not u["producer_paid_until"]:
        return False
    return u["producer_paid_until"] > now()


# ========================================================================
# Линейки
# ========================================================================

async def create_line(producer_id: int, category: str, name: str,
                      common_photo_file_id: str | None, created_by_user_id: int,
                      status: str) -> int:
    ts = now()
    cur = await _db.execute(
        """
        INSERT INTO lines (producer_id, category, name, common_photo_file_id,
                          created_by_user_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (producer_id, category, name, common_photo_file_id, created_by_user_id, status, ts, ts),
    )
    await _db.commit()
    return cur.lastrowid


async def get_line(line_id: int) -> aiosqlite.Row | None:
    async with _db.execute("SELECT * FROM lines WHERE id=?", (line_id,)) as cur:
        return await cur.fetchone()


async def list_lines(producer_id: int | None = None, status: str | None = None) -> list[aiosqlite.Row]:
    q = "SELECT * FROM lines WHERE 1=1"
    params: list = []
    if producer_id is not None:
        q += " AND producer_id=?"
        params.append(producer_id)
    if status is not None:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at DESC"
    async with _db.execute(q, tuple(params)) as cur:
        return list(await cur.fetchall())


async def update_line_field(line_id: int, field: str, value) -> None:
    await _db.execute(
        f"UPDATE lines SET {field}=?, updated_at=? WHERE id=?", (value, now(), line_id)
    )
    await _db.commit()


async def delete_line(line_id: int) -> None:
    await _db.execute("DELETE FROM lines WHERE id=?", (line_id,))
    await _db.commit()


# ========================================================================
# Вкусы
# ========================================================================

async def create_flavor(line_id: int, name: str, description: str,
                        photo_file_id: str | None, constructor_proportion: str | None,
                        status: str) -> int:
    ts = now()
    cur = await _db.execute(
        """
        INSERT INTO flavors (line_id, name, description, photo_file_id,
                            constructor_proportion, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (line_id, name, description, photo_file_id, constructor_proportion, status, ts, ts),
    )
    await _db.commit()
    return cur.lastrowid


async def get_flavor(flavor_id: int) -> aiosqlite.Row | None:
    async with _db.execute("SELECT * FROM flavors WHERE id=?", (flavor_id,)) as cur:
        return await cur.fetchone()


async def get_flavor_full(flavor_id: int) -> aiosqlite.Row | None:
    """Вкус + линейка + производитель одним JOIN (для карточки)."""
    async with _db.execute(
        """
        SELECT f.*, l.name AS line_name, l.category AS category,
               l.common_photo_file_id AS line_photo,
               p.id AS producer_id, p.name AS producer_name, p.channel_url AS channel_url,
               p.owner_user_id AS producer_owner
        FROM flavors f
        JOIN lines l ON f.line_id = l.id
        JOIN producers p ON l.producer_id = p.id
        WHERE f.id=?
        """,
        (flavor_id,),
    ) as cur:
        return await cur.fetchone()


async def list_flavors(line_id: int | None = None, status: str | None = None) -> list[aiosqlite.Row]:
    q = "SELECT * FROM flavors WHERE 1=1"
    params: list = []
    if line_id is not None:
        q += " AND line_id=?"
        params.append(line_id)
    if status is not None:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at DESC"
    async with _db.execute(q, tuple(params)) as cur:
        return list(await cur.fetchall())


async def update_flavor_field(flavor_id: int, field: str, value) -> None:
    await _db.execute(
        f"UPDATE flavors SET {field}=?, updated_at=? WHERE id=?", (value, now(), flavor_id)
    )
    await _db.commit()


async def delete_flavor(flavor_id: int) -> None:
    await _db.execute("DELETE FROM flavors WHERE id=?", (flavor_id,))
    await _db.commit()
    invalidate_flavor_tags_cache(flavor_id, {})


async def count_producer_flavors(producer_id: int) -> int:
    """Сколько вкусов у производителя (для проверки лимита у бесплатных)."""
    async with _db.execute(
        """
        SELECT COUNT(*) AS c FROM flavors f
        JOIN lines l ON f.line_id = l.id
        WHERE l.producer_id=?
        """,
        (producer_id,),
    ) as cur:
        row = await cur.fetchone()
        return row["c"] if row else 0


async def flavors_needing_retag(line_id: int | None = None) -> list[aiosqlite.Row]:
    """
    Вкусы без тегов/без базового описания (или с флагом needs_retag).
    Используется командой /retag.
    """
    q = """
        SELECT f.* FROM flavors f
        WHERE (f.needs_retag=1
               OR f.ai_base_description IS NULL OR f.ai_base_description=''
               OR NOT EXISTS (SELECT 1 FROM flavor_tags t WHERE t.flavor_id=f.id))
    """
    params: list = []
    if line_id is not None:
        q += " AND f.line_id=?"
        params.append(line_id)
    async with _db.execute(q, tuple(params)) as cur:
        return list(await cur.fetchall())


# ========================================================================
# Теги вкусов
# ========================================================================

async def set_flavor_tags(flavor_id: int, tags: dict[str, float]) -> None:
    """
    Полностью заменить теги вкуса (используется после ИИ-тегирования).
    Обновляет и кеш в памяти.
    """
    await _db.execute("DELETE FROM flavor_tags WHERE flavor_id=?", (flavor_id,))
    for tag, value in tags.items():
        await _db.execute(
            "INSERT INTO flavor_tags (flavor_id, tag_name, value) VALUES (?, ?, ?)",
            (flavor_id, tag, value),
        )
    await _db.execute("UPDATE flavors SET needs_retag=0 WHERE id=?", (flavor_id,))
    await _db.commit()
    invalidate_flavor_tags_cache(flavor_id, tags)


async def get_flavor_tags(flavor_id: int) -> dict[str, float]:
    async with _db.execute(
        "SELECT tag_name, value FROM flavor_tags WHERE flavor_id=?", (flavor_id,)
    ) as cur:
        return {r["tag_name"]: r["value"] for r in await cur.fetchall()}


# ========================================================================
# Свайпы и избранное
# ========================================================================

async def record_swipe(user_id: int, flavor_id: int, action: str) -> None:
    """Записать/обновить действие пользователя по вкусу (UPSERT)."""
    await _db.execute(
        """
        INSERT INTO swipes (user_id, flavor_id, action, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, flavor_id) DO UPDATE SET action=excluded.action,
                                                      created_at=excluded.created_at
        """,
        (user_id, flavor_id, action, now()),
    )
    await _db.commit()


async def get_swipe(user_id: int, flavor_id: int) -> aiosqlite.Row | None:
    async with _db.execute(
        "SELECT * FROM swipes WHERE user_id=? AND flavor_id=?", (user_id, flavor_id)
    ) as cur:
        return await cur.fetchone()


async def get_swiped_flavor_ids(user_id: int) -> set[int]:
    async with _db.execute(
        "SELECT flavor_id FROM swipes WHERE user_id=?", (user_id,)
    ) as cur:
        return {r["flavor_id"] for r in await cur.fetchall()}


async def get_disliked_flavor_ids(user_id: int) -> set[int]:
    async with _db.execute(
        "SELECT flavor_id FROM swipes WHERE user_id=? AND action='dislike'", (user_id,)
    ) as cur:
        return {r["flavor_id"] for r in await cur.fetchall()}


async def reset_dislikes(user_id: int) -> int:
    """
    Вернуть дизлайкнутые вкусы в очередь показа: удаляем их свайпы.
    Возвращает число возвращённых карточек.
    """
    cur = await _db.execute(
        "DELETE FROM swipes WHERE user_id=? AND action='dislike'", (user_id,)
    )
    await _db.commit()
    return cur.rowcount


async def add_favorite(user_id: int, flavor_id: int) -> None:
    await _db.execute(
        """
        INSERT INTO favorites (user_id, flavor_id, created_at) VALUES (?, ?, ?)
        ON CONFLICT(user_id, flavor_id) DO NOTHING
        """,
        (user_id, flavor_id, now()),
    )
    await _db.commit()


async def remove_favorite(user_id: int, flavor_id: int) -> None:
    await _db.execute(
        "DELETE FROM favorites WHERE user_id=? AND flavor_id=?", (user_id, flavor_id)
    )
    await _db.commit()


async def is_favorite(user_id: int, flavor_id: int) -> bool:
    async with _db.execute(
        "SELECT 1 FROM favorites WHERE user_id=? AND flavor_id=?", (user_id, flavor_id)
    ) as cur:
        return await cur.fetchone() is not None


async def list_favorites(user_id: int) -> list[aiosqlite.Row]:
    async with _db.execute(
        """
        SELECT f.id, f.name, l.name AS line_name, p.name AS producer_name
        FROM favorites fav
        JOIN flavors f ON fav.flavor_id = f.id
        JOIN lines l ON f.line_id = l.id
        JOIN producers p ON l.producer_id = p.id
        WHERE fav.user_id=?
        ORDER BY fav.created_at DESC
        """,
        (user_id,),
    ) as cur:
        return list(await cur.fetchall())


# ========================================================================
# Показы (impressions) и ИИ-описания
# ========================================================================

async def record_impression(user_id: int, flavor_id: int) -> None:
    await _db.execute(
        "INSERT INTO impressions (user_id, flavor_id, created_at) VALUES (?, ?, ?)",
        (user_id, flavor_id, now()),
    )
    await _db.commit()


async def get_cached_ai_description(user_id: int, flavor_id: int) -> str | None:
    async with _db.execute(
        "SELECT text FROM ai_descriptions WHERE user_id=? AND flavor_id=?",
        (user_id, flavor_id),
    ) as cur:
        row = await cur.fetchone()
        return row["text"] if row else None


async def save_ai_description(user_id: int, flavor_id: int, text: str) -> None:
    await _db.execute(
        """
        INSERT INTO ai_descriptions (user_id, flavor_id, text, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, flavor_id) DO UPDATE SET text=excluded.text,
                                                      created_at=excluded.created_at
        """,
        (user_id, flavor_id, text, now()),
    )
    await _db.commit()


# ========================================================================
# Логи модерации и правок
# ========================================================================

async def log_moderation(entity_type: str, entity_id: int, action: str,
                         comment: str | None, admin_id: int) -> None:
    await _db.execute(
        """
        INSERT INTO moderation_log (entity_type, entity_id, action, comment, admin_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, action, comment, admin_id, now()),
    )
    await _db.commit()


async def log_edit(entity_type: str, entity_id: int, editor_user_id: int,
                   field: str, old_value, new_value) -> None:
    await _db.execute(
        """
        INSERT INTO edit_log (entity_type, entity_id, editor_user_id, field,
                             old_value, new_value, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, editor_user_id, field,
         str(old_value), str(new_value), now()),
    )
    await _db.commit()


# ========================================================================
# Статистика (агрегаты)
# ========================================================================

async def stats_overview() -> dict:
    """Общая статистика для админа."""
    result: dict = {}
    week_ago = now() - 7 * 86400

    async def scalar(query: str, params: tuple = ()) -> int:
        async with _db.execute(query, params) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    result["users_total"] = await scalar("SELECT COUNT(*) FROM users WHERE is_registered=1")
    result["users_active_week"] = await scalar(
        "SELECT COUNT(DISTINCT user_id) FROM swipes WHERE created_at>=?", (week_ago,)
    )
    result["flavors_approved"] = await scalar("SELECT COUNT(*) FROM flavors WHERE status='approved'")
    result["flavors_pending"] = await scalar("SELECT COUNT(*) FROM flavors WHERE status='pending'")
    result["flavors_rejected"] = await scalar("SELECT COUNT(*) FROM flavors WHERE status='rejected'")
    result["likes_total"] = await scalar("SELECT COUNT(*) FROM swipes WHERE action='like'")
    result["dislikes_total"] = await scalar("SELECT COUNT(*) FROM swipes WHERE action='dislike'")
    result["favorites_total"] = await scalar("SELECT COUNT(*) FROM favorites")
    result["producers_paid"] = await scalar(
        "SELECT COUNT(*) FROM users WHERE producer_paid_until IS NOT NULL AND producer_paid_until>?",
        (now(),),
    )
    return result


async def stats_top_flavors(action: str, limit: int = 5) -> list[aiosqlite.Row]:
    """Топ вкусов по числу лайков/дизлайков."""
    async with _db.execute(
        """
        SELECT f.name AS name, COUNT(*) AS cnt
        FROM swipes s JOIN flavors f ON s.flavor_id=f.id
        WHERE s.action=?
        GROUP BY s.flavor_id ORDER BY cnt DESC LIMIT ?
        """,
        (action, limit),
    ) as cur:
        return list(await cur.fetchall())


async def producer_basic_stats(producer_id: int) -> dict:
    """Базовая статистика производителя: показы/лайки/дизлайки/избранное."""
    async def scalar(query: str) -> int:
        async with _db.execute(query, (producer_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    impressions = await scalar(
        """SELECT COUNT(*) FROM impressions i
           JOIN flavors f ON i.flavor_id=f.id JOIN lines l ON f.line_id=l.id
           WHERE l.producer_id=?"""
    )
    likes = await scalar(
        """SELECT COUNT(*) FROM swipes s
           JOIN flavors f ON s.flavor_id=f.id JOIN lines l ON f.line_id=l.id
           WHERE l.producer_id=? AND s.action='like'"""
    )
    dislikes = await scalar(
        """SELECT COUNT(*) FROM swipes s
           JOIN flavors f ON s.flavor_id=f.id JOIN lines l ON f.line_id=l.id
           WHERE l.producer_id=? AND s.action='dislike'"""
    )
    favs = await scalar(
        """SELECT COUNT(*) FROM favorites fav
           JOIN flavors f ON fav.flavor_id=f.id JOIN lines l ON f.line_id=l.id
           WHERE l.producer_id=?"""
    )
    return {"impressions": impressions, "likes": likes, "dislikes": dislikes, "favorites": favs}


async def producer_per_flavor_stats(producer_id: int) -> list[dict]:
    """Расширенная статистика по каждому вкусу (для платных)."""
    async with _db.execute(
        """
        SELECT f.id, f.name,
            (SELECT COUNT(*) FROM impressions i WHERE i.flavor_id=f.id) AS impressions,
            (SELECT COUNT(*) FROM swipes s WHERE s.flavor_id=f.id AND s.action='like') AS likes,
            (SELECT COUNT(*) FROM swipes s WHERE s.flavor_id=f.id AND s.action='dislike') AS dislikes,
            (SELECT COUNT(*) FROM favorites fav WHERE fav.flavor_id=f.id) AS favorites
        FROM flavors f JOIN lines l ON f.line_id=l.id
        WHERE l.producer_id=?
        ORDER BY favorites DESC
        """,
        (producer_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ========================================================================
# Модерация / уведомления о платном статусе
# ========================================================================

async def list_pending_lines_and_flavors() -> tuple[list[aiosqlite.Row], list[aiosqlite.Row]]:
    """Очереди модерации: линейки и вкусы со статусом pending."""
    async with _db.execute(
        """SELECT l.*, p.owner_user_id AS owner FROM lines l
           JOIN producers p ON l.producer_id=p.id
           WHERE l.status='pending' ORDER BY l.created_at ASC"""
    ) as cur:
        lines = list(await cur.fetchall())
    async with _db.execute(
        """SELECT f.*, p.owner_user_id AS owner, p.id AS producer_id FROM flavors f
           JOIN lines l ON f.line_id=l.id JOIN producers p ON l.producer_id=p.id
           WHERE f.status='pending' ORDER BY f.created_at ASC"""
    ) as cur:
        flavors = list(await cur.fetchall())
    return lines, flavors


async def producers_expiring_in(seconds_from: int, seconds_to: int) -> list[aiosqlite.Row]:
    """Пользователи, у кого платный статус истекает в окне [now+from, now+to]."""
    lo = now() + seconds_from
    hi = now() + seconds_to
    async with _db.execute(
        "SELECT * FROM users WHERE producer_paid_until IS NOT NULL "
        "AND producer_paid_until BETWEEN ? AND ?",
        (lo, hi),
    ) as cur:
        return list(await cur.fetchall())


async def producers_just_expired() -> list[aiosqlite.Row]:
    """Пользователи с истёкшим статусом, которым ещё не отправлено уведомление об окончании."""
    async with _db.execute(
        "SELECT * FROM users WHERE producer_paid_until IS NOT NULL "
        "AND producer_paid_until <= ? AND paid_notified_expired=0",
        (now(),),
    ) as cur:
        return list(await cur.fetchall())
