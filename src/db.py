"""Утилиты для хранения состояния бота в SQLite."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .types import PollTemplate

# Ключи хранения в kv_store
BOT_STATE_KEY = "bot_state"
POLL_STATE_KEY = "poll_state"
FUND_BALANCE_KEY = "fund_balance"
SCHEMA_VERSION = 3


def _get_db_path() -> str:
    """Возвращает путь к базе данных с учётом переменной окружения."""
    override: str | None = os.getenv("VOLLEYBOT_DB_PATH")
    if override:
        return override
    return str(Path(__file__).parent.parent / "data" / "volleybot.db")


def init_db() -> None:
    """Создаёт файл базы и таблицу kv_store при необходимости."""
    db_path: str = _get_db_path()
    logging.debug(f"Инициализация базы данных: {db_path}")

    # Для in-memory соединения каталоги не нужны
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        _create_base_tables(conn)
        _migrate_schema(conn)
        conn.commit()
    logging.debug(f"✅ База данных инициализирована: {db_path}")


def _create_base_tables(conn: sqlite3.Connection) -> None:
    """Создаёт таблицы, не зависящие от версии схемы бизнес-данных."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_store (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY,
            name TEXT,
            fullname TEXT,
            ball_donate INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Пошагово обновляет схему БД до актуальной версии."""
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if user_version == 0:
        _bootstrap_schema(conn)
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])

    if user_version < 2:
        _migrate_to_v2(conn)
        user_version = 2
    if user_version < 3:
        _migrate_to_v3(conn)
        user_version = 3

    if user_version != SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Создаёт схему с нуля в актуальном виде или определяет версию legacy БД."""
    has_poll_templates = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='poll_templates'"
    ).fetchone()
    if has_poll_templates:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(poll_templates)")}
        if "id" in columns:
            _create_indexes(conn)
            conn.execute("PRAGMA user_version = 3")
            return
        if "monthly_cost" in columns:
            conn.execute("PRAGMA user_version = 2")
            return
        conn.execute("PRAGMA user_version = 1")
        return

    _create_schema_v3(conn)
    conn.execute("PRAGMA user_version = 3")


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Добавляет monthly_cost в legacy-схему."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(poll_templates)")}
    if "monthly_cost" not in columns:
        conn.execute("ALTER TABLE poll_templates ADD COLUMN monthly_cost INTEGER DEFAULT 0")
        logging.info("✅ Миграция: добавлен столбец monthly_cost в poll_templates")
    conn.execute("PRAGMA user_version = 2")


def _create_schema_v3(conn: sqlite3.Connection) -> None:
    """Создаёт актуальную схему данных опросов и финансов."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            place TEXT,
            message TEXT NOT NULL,
            open_day TEXT NOT NULL DEFAULT '*',
            open_hour_utc INTEGER NOT NULL DEFAULT 0 CHECK (open_hour_utc BETWEEN 0 AND 23),
            open_minute_utc INTEGER NOT NULL DEFAULT 0 CHECK (open_minute_utc BETWEEN 0 AND 59),
            game_day TEXT NOT NULL DEFAULT '*',
            game_hour_utc INTEGER NOT NULL DEFAULT 0 CHECK (game_hour_utc BETWEEN 0 AND 23),
            game_minute_utc INTEGER NOT NULL DEFAULT 0 CHECK (game_minute_utc BETWEEN 0 AND 59),
            cost INTEGER NOT NULL DEFAULT 0 CHECK (cost >= 0),
            monthly_cost INTEGER NOT NULL DEFAULT 0 CHECK (monthly_cost >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (open_day IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', '*')),
            CHECK (game_day IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', '*'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_subscriptions (
            poll_template_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (poll_template_id, user_id),
            FOREIGN KEY (poll_template_id) REFERENCES poll_templates(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            description TEXT,
            poll_template_id INTEGER,
            poll_name_snapshot TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE,
            FOREIGN KEY (poll_template_id) REFERENCES poll_templates(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hall_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_template_id INTEGER NOT NULL,
            month TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK (amount >= 0),
            paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (poll_template_id) REFERENCES poll_templates(id) ON DELETE CASCADE,
            UNIQUE(poll_template_id, month)
        )
        """
    )
    _create_indexes(conn)


def _create_indexes(conn: sqlite3.Connection) -> None:
    """Создаёт индексы для актуальной схемы."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_poll_subscriptions_user_id ON poll_subscriptions(user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hall_payments_month ON hall_payments(month)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_player_created_at
        ON transactions(player_id, created_at DESC)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_poll_template_id ON transactions(poll_template_id)"
    )


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    """Перестраивает legacy-таблицы на схему с poll_template_id."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(
            """
            CREATE TABLE poll_templates_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                place TEXT,
                message TEXT NOT NULL,
                open_day TEXT NOT NULL DEFAULT '*',
                open_hour_utc INTEGER NOT NULL DEFAULT 0 CHECK (open_hour_utc BETWEEN 0 AND 23),
                open_minute_utc INTEGER NOT NULL DEFAULT 0 CHECK (open_minute_utc BETWEEN 0 AND 59),
                game_day TEXT NOT NULL DEFAULT '*',
                game_hour_utc INTEGER NOT NULL DEFAULT 0 CHECK (game_hour_utc BETWEEN 0 AND 23),
                game_minute_utc INTEGER NOT NULL DEFAULT 0 CHECK (game_minute_utc BETWEEN 0 AND 59),
                cost INTEGER NOT NULL DEFAULT 0 CHECK (cost >= 0),
                monthly_cost INTEGER NOT NULL DEFAULT 0 CHECK (monthly_cost >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (open_day IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', '*')),
                CHECK (game_day IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', '*'))
            )
            """
        )
        conn.execute(
            """
            INSERT INTO poll_templates_new (
                name, place, message, open_day, open_hour_utc, open_minute_utc,
                game_day, game_hour_utc, game_minute_utc, cost, monthly_cost,
                created_at, updated_at
            )
            SELECT
                name,
                place,
                message,
                COALESCE(open_day, '*'),
                COALESCE(open_hour_utc, 0),
                COALESCE(open_minute_utc, 0),
                COALESCE(game_day, '*'),
                COALESCE(game_hour_utc, 0),
                COALESCE(game_minute_utc, 0),
                COALESCE(cost, 0),
                COALESCE(monthly_cost, 0),
                COALESCE(updated_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, CURRENT_TIMESTAMP)
            FROM poll_templates
            """
        )
        conn.execute(
            """
            CREATE TABLE poll_subscriptions_new (
                poll_template_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (poll_template_id, user_id),
                FOREIGN KEY (poll_template_id) REFERENCES poll_templates_new(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES players(id) ON DELETE CASCADE
            )
            """
        )
        if _table_exists(conn, "poll_subscriptions"):
            conn.execute(
                """
                INSERT INTO poll_subscriptions_new (poll_template_id, user_id)
                SELECT pt.id, ps.user_id
                FROM poll_subscriptions ps
                JOIN poll_templates_new pt ON pt.name = ps.poll_name
                """
            )
        conn.execute(
            """
            CREATE TABLE transactions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                description TEXT,
                poll_template_id INTEGER,
                poll_name_snapshot TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE,
                FOREIGN KEY (poll_template_id) REFERENCES poll_templates_new(id) ON DELETE SET NULL
            )
            """
        )
        if _table_exists(conn, "transactions"):
            conn.execute(
                """
                INSERT INTO transactions_new (
                    id, player_id, amount, description, poll_template_id,
                    poll_name_snapshot, created_at
                )
                SELECT
                    t.id,
                    t.player_id,
                    t.amount,
                    t.description,
                    pt.id,
                    t.poll_name,
                    t.created_at
                FROM transactions t
                LEFT JOIN poll_templates_new pt ON pt.name = t.poll_name
                """
            )
        conn.execute(
            """
            CREATE TABLE hall_payments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_template_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount >= 0),
                paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (poll_template_id) REFERENCES poll_templates_new(id) ON DELETE CASCADE,
                UNIQUE (poll_template_id, month)
            )
            """
        )
        if _table_exists(conn, "hall_payments"):
            conn.execute(
                """
                INSERT INTO hall_payments_new (id, poll_template_id, month, amount, paid_at)
                SELECT hp.id, pt.id, hp.month, hp.amount, hp.paid_at
                FROM hall_payments hp
                JOIN poll_templates_new pt ON pt.name = hp.poll_name
                """
            )

        if _table_exists(conn, "poll_subscriptions"):
            conn.execute("DROP TABLE poll_subscriptions")
        if _table_exists(conn, "hall_payments"):
            conn.execute("DROP TABLE hall_payments")
        if _table_exists(conn, "transactions"):
            conn.execute("DROP TABLE transactions")
        if _table_exists(conn, "poll_templates"):
            conn.execute("DROP TABLE poll_templates")

        conn.execute("ALTER TABLE poll_templates_new RENAME TO poll_templates")
        conn.execute("ALTER TABLE poll_subscriptions_new RENAME TO poll_subscriptions")
        conn.execute("ALTER TABLE transactions_new RENAME TO transactions")
        conn.execute("ALTER TABLE hall_payments_new RENAME TO hall_payments")

        _create_indexes(conn)
        conn.execute("PRAGMA user_version = 3")
        conn.execute("PRAGMA foreign_keys = ON")
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise sqlite3.IntegrityError(f"foreign_key_check failed: {fk_errors}")
        logging.info("✅ Миграция схемы БД до версии 3 завершена")
    except Exception:
        conn.execute("PRAGMA foreign_keys = ON")
        raise


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,)
        ).fetchone()
        is not None
    )


@contextmanager
def _connect() -> Any:
    """
    Контекстный менеджер для подключения к БД.
    Явно закрывает соединение, чтобы избежать ResourceWarning.
    """
    conn = sqlite3.connect(_get_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def save_state(key: str, value: Any) -> None:
    """Сохраняет JSON-сериализуемое значение по ключу."""
    try:
        init_db()
        payload: str = json.dumps(value, ensure_ascii=False)
        logging.debug(
            f"Сохранение состояния: ключ='{key}', размер данных={len(payload)} байт"
        )
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_store(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, payload),
            )
            conn.commit()
        logging.debug(f"✅ Состояние '{key}' успешно сохранено")
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка SQLite при сохранении состояния '{key}'. "
            f"Проверьте доступ к БД: {_get_db_path()}"
        )
    except (TypeError, ValueError):
        logging.exception(
            f"❌ Не удалось сериализовать данные в JSON для ключа '{key}'. "
            f"Проверьте, что данные сериализуемы."
        )
    except OSError:
        logging.exception(
            f"❌ Ошибка ввода-вывода при сохранении состояния '{key}'. "
            f"Проверьте права доступа к: {_get_db_path()}"
        )


def load_state(key: str, default: Any = None) -> Any:
    """Загружает состояние по ключу, возвращает default при ошибке/отсутствии."""
    try:
        init_db()
        logging.debug(f"Загрузка состояния для ключа: '{key}'")
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            logging.debug(f"Состояние для ключа '{key}' не найдено, используем default")
            return default
        result = json.loads(row[0])
        logging.debug(f"✅ Состояние '{key}' успешно загружено")
        return result
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка SQLite при загрузке состояния '{key}'. "
            f"Возвращаем значение по умолчанию. БД: {_get_db_path()}"
        )
        return default
    except json.JSONDecodeError:
        logging.exception(
            f"❌ Повреждённые данные JSON для ключа '{key}'. "
            f"Возвращаем значение по умолчанию."
        )
        return default
    except OSError:
        logging.exception(
            f"❌ Ошибка ввода-вывода при загрузке состояния '{key}'. "
            f"Возвращаем значение по умолчанию. БД: {_get_db_path()}"
        )
        return default


def get_all_players() -> list[dict[str, Any]]:
    """Возвращает список всех игроков из базы данных."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, name, fullname, ball_donate, balance FROM players"
            )
            players = []
            for row in cursor.fetchall():
                player = dict(row)
                # Преобразуем 0/1 в bool для совместимости с логикой, ожидавшей JSON
                player["ball_donate"] = bool(player["ball_donate"])
                players.append(player)
            return players
    except sqlite3.Error:
        logging.exception("❌ Ошибка при получении списка всех игроков")
        return []


def get_players_with_balance() -> list[dict[str, Any]]:
    """Возвращает список игроков с ненулевым балансом."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, name, fullname, balance FROM players WHERE balance != 0 ORDER BY fullname ASC"
            )
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error:
        logging.exception("❌ Ошибка при получении баланса игроков")
        return []


def get_player_balance(user_id: int) -> dict[str, Any] | None:
    """Возвращает баланс конкретного игрока."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, name, fullname, balance FROM players WHERE id = ?",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении баланса игрока {user_id}")
        return None


def get_player_info(user_id: int) -> dict[str, Any] | None:
    """Возвращает полную информацию об игроке (id, name, fullname, ball_donate, balance)."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, name, fullname, ball_donate, balance FROM players WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            player = dict(row)
            player["ball_donate"] = bool(player["ball_donate"])
            return player
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении информации об игроке {user_id}")
        return None


def update_player_balance(user_id: int, amount: int) -> bool:
    """Изменяет баланс игрока на указанную сумму (может быть отрицательной)."""
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "UPDATE players SET balance = balance + ? WHERE id = ?",
                (amount, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при обновлении баланса игрока {user_id}")
        return False


def find_player_by_name(query: str) -> list[dict[str, Any]]:
    """Ищет игроков по части имени или fullname."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            pattern = f"%{query}%"
            cursor = conn.execute(
                "SELECT id, name, fullname, balance FROM players WHERE name LIKE ? OR fullname LIKE ? ORDER BY fullname ASC",
                (pattern, pattern),
            )
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при поиске игрока: {query}")
        return []


def ensure_player(
    user_id: int, name: str | None = None, fullname: str | None = None
) -> None:
    """
    Гарантирует наличие игрока в базе данных.

    При конфликте (игрок уже существует):
    - Если в БД уже есть name или fullname, они НЕ перезаписываются
    - Обновляются только пустые (NULL) поля

    Это предотвращает случайную перезапись вручную установленных имён.
    """
    # Нормализуем username: если содержит пробелы или другие недопустимые символы, очищаем
    if name and name.strip():
        normalized_name = name.strip()
        # Username в Telegram не может содержать пробелы
        if " " in normalized_name or "\t" in normalized_name or "\n" in normalized_name:
            name = None
        else:
            name = normalized_name
    else:
        name = None

    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO players (id, name, fullname)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = COALESCE(players.name, excluded.name),
                    fullname = COALESCE(players.fullname, excluded.fullname)
                """,
                (user_id, name, fullname),
            )
            conn.commit()
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при регистрации/обновлении игрока {user_id}")


def get_poll_templates() -> list[PollTemplate]:
    """Возвращает все шаблоны опросов из БД."""
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            template_rows = conn.execute(
                "SELECT * FROM poll_templates ORDER BY id"
            ).fetchall()
            subs_by_template: dict[int, list[int]] = defaultdict(list)
            sub_rows = conn.execute(
                "SELECT poll_template_id, user_id FROM poll_subscriptions"
            ).fetchall()
            for sub_row in sub_rows:
                subs_by_template[int(sub_row["poll_template_id"])].append(
                    int(sub_row["user_id"])
                )

            templates = []
            for row in template_rows:
                template = dict(row)
                template_id = int(template["id"])
                template["subs"] = subs_by_template.get(template_id, [])
                templates.append(template)
            return templates
    except sqlite3.Error:
        logging.exception("❌ Ошибка при получении шаблонов опросов")
        return []


def save_poll_template(template: dict[str, Any]) -> None:
    """Сохраняет или обновляет шаблон опроса и его подписчиков."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO poll_templates (
                    name, place, message, open_day, open_hour_utc, open_minute_utc,
                    game_day, game_hour_utc, game_minute_utc, cost, monthly_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    place = excluded.place,
                    message = excluded.message,
                    open_day = excluded.open_day,
                    open_hour_utc = excluded.open_hour_utc,
                    open_minute_utc = excluded.open_minute_utc,
                    game_day = excluded.game_day,
                    game_hour_utc = excluded.game_hour_utc,
                    game_minute_utc = excluded.game_minute_utc,
                    cost = excluded.cost,
                    monthly_cost = excluded.monthly_cost,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    template["name"],
                    template.get("place", ""),
                    template["message"],
                    template.get("open_day", "*"),
                    template.get("open_hour_utc", 0),
                    template.get("open_minute_utc", 0),
                    template.get("game_day", "*"),
                    template.get("game_hour_utc", 0),
                    template.get("game_minute_utc", 0),
                    template.get("cost", 0),
                    template.get("monthly_cost", 0),
                ),
            )
            row = conn.execute(
                "SELECT id FROM poll_templates WHERE name = ?", (template["name"],)
            ).fetchone()
            poll_template_id = int(row[0])

            # Обновляем подписчиков если они переданы
            if "subs" in template:
                conn.execute(
                    "DELETE FROM poll_subscriptions WHERE poll_template_id = ?",
                    (poll_template_id,),
                )
                for user_id in template["subs"]:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO poll_subscriptions (poll_template_id, user_id)
                        VALUES (?, ?)
                        """,
                        (poll_template_id, user_id),
                    )
            conn.commit()
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при сохранении шаблона опроса '{template.get('name')}'"
        )


def clear_paid_poll_subscriptions() -> None:
    """Очищает подписки для всех платных опросов (cost > 0)."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                DELETE FROM poll_subscriptions
                WHERE poll_template_id IN (
                    SELECT id FROM poll_templates WHERE cost > 0
                )
                """
            )
            conn.commit()
        logging.info("✅ Подписки для платных опросов очищены")
    except sqlite3.Error:
        logging.exception("❌ Ошибка при очистке подписок для платных опросов")


def add_transaction(
    player_id: int,
    amount: int,
    description: str,
    poll_template_id: int | None = None,
    poll_name_snapshot: str | None = None,
) -> None:
    """
    Добавляет транзакцию в историю.

    Args:
        player_id: ID игрока
        amount: Сумма транзакции (отрицательная для списания)
        description: Описание транзакции
        poll_template_id: ID шаблона опроса (необязательно)
        poll_name_snapshot: Историческое имя зала в момент транзакции
    """
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions (
                    player_id, amount, description, poll_template_id, poll_name_snapshot
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    amount,
                    description,
                    poll_template_id,
                    poll_name_snapshot,
                ),
            )
            conn.commit()
        logging.debug(
            "✅ Транзакция добавлена: "
            f"player_id={player_id}, amount={amount}, poll_template_id={poll_template_id}, "
            f"poll_name_snapshot={poll_name_snapshot}"
        )
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при добавлении транзакции для игрока {player_id}")


# ── Fund (касса) ────────────────────────────────────────────────────────────


def get_fund_balance() -> int:
    """Возвращает текущий баланс кассы."""
    try:
        init_db()
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (FUND_BALANCE_KEY,)
            ).fetchone()
        if row is None:
            return 0
        return int(row[0])
    except (sqlite3.Error, json.JSONDecodeError, ValueError):
        logging.exception("❌ Ошибка при получении баланса кассы")
        return 0


def update_fund_balance(amount: int) -> None:
    """
    Атомарно изменяет баланс кассы на указанную сумму.

    Args:
        amount: Сумма изменения (положительная — пополнение, отрицательная — списание)
    """
    try:
        init_db()
        with _connect() as conn:
            row = conn.execute(
                """
                INSERT INTO kv_store(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = CAST(kv_store.value AS INTEGER) + CAST(excluded.value AS INTEGER),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING CAST(value AS INTEGER)
                """,
                (FUND_BALANCE_KEY, amount),
            ).fetchone()
            conn.commit()
        new_balance = int(row[0]) if row else 0
        logging.info(f"💰 Касса изменена на {amount:+d}, новый баланс: {new_balance}")
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при обновлении баланса кассы на {amount}")


# ── Hall payments (оплата залов) ─────────────────────────────────────────────


def get_unpaid_halls(month: str) -> list[PollTemplate]:
    """
    Возвращает платные залы (monthly_cost > 0), ещё не оплаченные в данном месяце.

    Args:
        month: Месяц в формате "YYYY-MM"

    Returns:
        Список шаблонов опросов с monthly_cost > 0, не имеющих записи в hall_payments
    """
    from typing import cast

    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            template_rows = conn.execute(
                """
                SELECT pt.*
                FROM poll_templates pt
                WHERE pt.monthly_cost > 0
                  AND pt.id NOT IN (
                      SELECT hp.poll_template_id FROM hall_payments hp WHERE hp.month = ?
                  )
                ORDER BY pt.id
                """,
                (month,),
            ).fetchall()
            subs_by_template: dict[int, list[int]] = defaultdict(list)
            sub_rows = conn.execute(
                "SELECT poll_template_id, user_id FROM poll_subscriptions"
            ).fetchall()
            for sub_row in sub_rows:
                subs_by_template[int(sub_row["poll_template_id"])].append(
                    int(sub_row["user_id"])
                )
            templates: list[PollTemplate] = []
            for row in template_rows:
                template = cast(PollTemplate, dict(row))
                template_id = int(template["id"])
                template["subs"] = subs_by_template.get(template_id, [])
                templates.append(template)
            return templates
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении неоплаченных залов за {month}")
        return []


def record_hall_payment(poll_template_id: int, month: str, amount: int) -> bool:
    """
    Записывает оплату зала за месяц.

    Args:
        poll_template_id: ID шаблона опроса
        month: Месяц в формате "YYYY-MM"
        amount: Сумма оплаты

    Returns:
        True если запись успешно добавлена, False при ошибке
    """
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO hall_payments (poll_template_id, month, amount)
                VALUES (?, ?, ?)
                """,
                (poll_template_id, month, amount),
            )
            conn.commit()
        logging.info(
            "✅ Оплата зала записана: "
            f"poll_template_id={poll_template_id}, месяц={month}, сумма={amount}"
        )
        return True
    except sqlite3.IntegrityError:
        logging.warning(
            f"⚠️ Зал с poll_template_id={poll_template_id} за {month} уже оплачен (дубликат)"
        )
        return False
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при записи оплаты зала poll_template_id={poll_template_id} за {month}"
        )
        return False
