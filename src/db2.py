"""Объект управления базой данных"""
from __future__ import annotations

import json
import logging
import sqlite3
import typing
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Literal
from warnings import catch_warnings

from _pytest.recwarn import deprecated_call
from returns.iterables import Fold
from returns.maybe import Maybe, Nothing
from returns.result import Failure, Result, Success, safe

from .types import (
    GameInfo,
    GamePollStats,
    GamePollStatsSummary,
    Player,
    PlayerStats,
    PollTemplate,
)
from .utils import normalize_telegram_username

LOGGER = logging.getLogger(__name__)

# Ключи хранения в kv_store
BOT_STATE_KEY = "bot_state"
POLL_STATE_KEY = "poll_state"
FUND_BALANCE_KEY = "fund_balance"
SCHEMA_VERSION = 9
BACKUP_RETENTION_DAYS = 10


EXPECTED_BUSINESS_TABLES = {
    "poll_templates",
    "poll_subscriptions",
    "transactions",
    "hall_payments",
    "games",
    "game_participants",
    "monthly_poll_votes",
    "messages"
}

EXPECTED_TABLE_COLUMNS: dict[str, set[str]] = {
    "poll_templates": {
        "id",
        "name",
        "place",
        "message",
        "open_day",
        "open_hour_utc",
        "open_minute_utc",
        "game_day",
        "game_hour_utc",
        "game_minute_utc",
        "cost",
        "cost_per_game",
        "enabled",
        "created_at",
        "updated_at",
    },
    "poll_subscriptions": {"poll_template_id", "user_id"},
    "transactions": {
        "id",
        "player_id",
        "amount",
        "description",
        "poll_template_id",
        "poll_name_snapshot",
        "created_at",
    },
    "hall_payments": {"id", "poll_template_id", "month", "amount", "paid_at"},
    "games": {
        "poll_id",
        "kind",
        "status",
        "poll_template_id",
        "poll_name_snapshot",
        "question_snapshot",
        "chat_id",
        "poll_message_id",
        "info_message_id",
        "final_message_id",
        "opened_at",
        "closed_at",
        "game_date",
        "place_snapshot",
        "cost_snapshot",
        "cost_per_game_snapshot",
        "options_json",
        "option_poll_names_json",
        "target_month_snapshot",
        "last_info_text",
        "created_at",
        "updated_at",
    },
    "game_participants": {
        "game_poll_id",
        "player_id",
        "roster_bucket",
        "sort_order",
        "is_subscriber",
        "is_guest",
        "guest_free_reason",
        "charged_amount",
        "charge_source",
        "balance_before",
        "balance_after",
        "created_at",
    },
    "monthly_poll_votes": {
        "game_poll_id",
        "player_id",
        "option_ids_json",
        "updated_at",
    },
    "messages": {
        "message_id",
        "chat_id",
        "user_id",
        "text",
        "date",
    }
}

EXPECTED_PRIMARY_KEYS: dict[str, list[str]] = {
    "poll_templates": ["id"],
}


# --- DB class ---
class DB:
    def __init__(
        self,
        path: str,
        logger: logging.Logger | None = None,
    ):
        self.logger = logger or LOGGER
        self.logger.debug(f"Инициализация БД: {path}")

        # Для in-memory соединения каталоги не нужны
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row
        _create_base_tables(self.conn)
        _ensure_current_schema(self.conn)

    def close(self):
        self.conn.close()


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
            is_guest INTEGER NOT NULL DEFAULT 0 CHECK (is_guest IN (0, 1)),
            balance INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _create_current_schema(conn: sqlite3.Connection) -> None:
    """Создаёт актуальную схему бизнес-данных."""
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
            cost_per_game INTEGER NOT NULL DEFAULT 0 CHECK (cost_per_game >= 0),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS games (
            poll_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('regular', 'monthly_subscription')),
            status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'cancelled')),
            poll_template_id INTEGER,
            poll_name_snapshot TEXT NOT NULL,
            question_snapshot TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            poll_message_id INTEGER NOT NULL,
            info_message_id INTEGER,
            final_message_id INTEGER,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            game_date TEXT,
            place_snapshot TEXT,
            cost_snapshot INTEGER NOT NULL DEFAULT 0,
            cost_per_game_snapshot INTEGER NOT NULL DEFAULT 0,
            options_json TEXT NOT NULL DEFAULT '[]',
            option_poll_names_json TEXT NOT NULL DEFAULT '[]',
            target_month_snapshot TEXT,
            last_info_text TEXT NOT NULL DEFAULT '⏳ Идёт сбор голосов...',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (poll_template_id) REFERENCES poll_templates(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_participants (
            game_poll_id TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            roster_bucket TEXT NOT NULL CHECK (roster_bucket IN ('main', 'reserve', 'booked')),
            sort_order INTEGER NOT NULL,
            is_subscriber INTEGER NOT NULL DEFAULT 0,
            is_guest INTEGER NOT NULL DEFAULT 0,
            guest_free_reason TEXT NOT NULL DEFAULT 'none'
                CHECK (guest_free_reason IN ('first_games', 'fill_min_players', 'none')),
            charged_amount INTEGER NOT NULL DEFAULT 0,
            charge_source TEXT NOT NULL DEFAULT 'none'
                CHECK (charge_source IN ('single_game', 'subscription', 'none')),
            balance_before INTEGER,
            balance_after INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_poll_id, player_id),
            FOREIGN KEY (game_poll_id) REFERENCES games(poll_id) ON DELETE CASCADE,
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_poll_votes (
            game_poll_id TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            option_ids_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_poll_id, player_id),
            FOREIGN KEY (game_poll_id) REFERENCES games(poll_id) ON DELETE CASCADE,
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            message_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            date INTEGER NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (message_id, chat_id)
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_games_status_kind ON games(status, kind)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_games_status ON games(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_games_game_date ON games(game_date)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_template_opened_at
        ON games(poll_template_id, opened_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_games_chat_opened_at
        ON games(chat_id, opened_at DESC)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_games_opened_at ON games(opened_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hall_payments_poll_template ON hall_payments(poll_template_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_monthly_poll_votes_game_poll_id ON monthly_poll_votes(game_poll_id)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_game_participants_player_created_at
        ON game_participants(player_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_game_participants_game_bucket_sort
        ON game_participants(game_poll_id, roster_bucket, sort_order)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_chat_created_at
        ON messages(chat_id, date DESC)
        """
    )


def _ensure_current_schema(conn: sqlite3.Connection) -> None:
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    existing_business_tables = existing_tables & EXPECTED_BUSINESS_TABLES
    if not existing_business_tables:
        _create_current_schema(conn)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return

    if existing_business_tables != EXPECTED_BUSINESS_TABLES:
        missing = sorted(EXPECTED_BUSINESS_TABLES - existing_business_tables)
        present = sorted(existing_business_tables)
        raise sqlite3.DatabaseError(
            "Incompatible DB schema: partial business schema detected; "
            f"present={present}; missing={missing}"
        )

    _migrate_schema(conn)

    mismatches = _validate_schema_strict(conn)
    if mismatches:
        raise sqlite3.DatabaseError("Incompatible DB schema: " + "; ".join(mismatches))

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Применяет совместимые миграции поверх предыдущей актуальной схемы."""


def _ensure_column(
    conn: sqlite3.Connection, table_name: str, column_name: str, alter_sql: str
) -> None:
    columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        conn.execute(alter_sql)


def _validate_schema_strict(conn: sqlite3.Connection) -> list[str]:
    mismatches: list[str] = []
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        table_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        actual_columns = {row[1] for row in table_info}
        missing_columns = sorted(expected_columns - actual_columns)
        unexpected_columns = sorted(actual_columns - expected_columns)
        if missing_columns:
            mismatches.append(f"{table_name}: missing columns {missing_columns}")
        if unexpected_columns:
            mismatches.append(f"{table_name}: unexpected columns {unexpected_columns}")
        expected_primary_key = EXPECTED_PRIMARY_KEYS.get(table_name)
        if expected_primary_key is not None:
            actual_primary_key = [
                row[1] for row in sorted(table_info, key=lambda item: item[5]) if row[5]
            ]
            if actual_primary_key != expected_primary_key:
                mismatches.append(
                    f"{table_name}: primary key {actual_primary_key}, "
                    f"expected {expected_primary_key}"
                )
    return mismatches


def transactional[T](operation: Callable[..., Result[T, str]]) \
-> Callable[..., Result[T, str]]:
    """Выполняет callback в транзакции и возвращает его результат.

    Callback получает соединение и может выполнить несколько SQL-запросов.
    Все их изменения фиксируются одним commit либо полностью отменяются при ошибке.
    """
    @wraps(operation)
    def wrapper(db: DB, *args, **kwargs) -> Result[T, str]:
        try:
            result = operation(db, *args, **kwargs)
            match result:
                case Success(_):
                    db.conn.commit()

                case Failure(_):
                    db.conn.rollback()

            return result
        except Exception as e:
            db.conn.rollback()
            db.logger.exception("Ошибка транзакции БД; выполнен rollback")
            return Failure(f"Ошибка транзакции БД: {e}")
    return wrapper


@safe(exceptions=(TypeError, ValueError))
def serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)

@safe(exceptions=(TypeError, ValueError))
def deserialize_json(value: str) -> Any:
    return json.loads(value)


@transactional
def save_state(db: DB, key: str, value: Any) -> Result[None, str]:
    serialized = serialize_json(value).alt(
        lambda e: (f"Не удалось сериализовать данные в JSON для ключа '{key}'. Ошибка: {e}")
    )

    match serialized:
        case Failure(error):
            return Failure(error)

        case Success(payload):
            db.logger.debug(
                f"Сохранение состояния: ключ='{key}', "
                f"размер данных={len(payload)} байт"
            )

            db.conn.execute(
                """
                    INSERT INTO kv_store(key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(KEY) DO UPDATE SET
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                """,
                (key, payload),
            )

            db.logger.debug(f"✅ Состояние '{key}' успешно сохранено")
            return Success(None)
    raise RuntimeError("Недостижимый код")


@transactional
def load_state(db: DB, key: str, default: Any = None) -> Result[Any, str]:
    """Загружает состояние по ключу, возвращает default при ошибке/отсутствии."""
    cursor = db.conn.execute(
        "SELECT value FROM kv_store WHERE key = ?",
        (key,),
    )
    result = cursor.fetchone()
    return Success(json.loads(result[0]) if result else default)


# --- PLAYER ---

def _player_from_row(row: sqlite3.Row) -> Player:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "fullname": row["fullname"],
        "ball_donate": bool(row["ball_donate"]),
        "is_guest": bool(row["is_guest"]),
        "balance": int(row["balance"] or 0),
    }

@transactional
def insert_player(db: DB, player_id: int) -> Result[None, str]:
    db.conn.execute(
        "INSERT OR IGNORE INTO players (id, name, fullname) VALUES (?, ?, ?)",
        (player_id, f"user{player_id}", f"User {player_id}"),
    )
    return Success(None)

@transactional
def get_all_players(db: DB) -> Result[list[Player], str]:
    cursor = db.conn.execute(
        "SELECT id, name, fullname, ball_donate, is_guest, balance FROM players"
    )
    return Success([_player_from_row(row) for row in cursor.fetchall()])


@transactional
def get_players_with_balance(db: DB) -> Result[list[Player], str]:
    cursor = db.conn.execute(
        "SELECT id, name, fullname, balance FROM players "
        "WHERE balance != 0 ORDER BY fullname ASC"
    )
    return Success([_player_from_row(row) for row in cursor.fetchall()])


@transactional
def get_player_balance(db: DB, user_id: int) -> Result[int, str]:
    """Возвращает баланс конкретного игрока."""
    row = db.conn.execute(
        "SELECT balance FROM players WHERE id = ?",
        (user_id,),
    ).fetchone()
    return Success(int(row["balance"])) if row else Failure("Player not found")


@transactional
def get_player_info(db: DB, user_id: int) -> Result[Player, str]:
    row = db.conn.execute(
        "SELECT id, name, fullname, ball_donate, is_guest, balance FROM players WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return Failure("Player not found")
    return Success(_player_from_row(row))


@transactional
def update_player_balance(db: DB, user_id: int, amount: int) -> Result[None, str]:
    db.conn.execute(
        "UPDATE players SET balance = balance + ? WHERE id = ?",
        (amount, user_id),
    )
    return Success(None)


# TODO: проверить, необходима ли эта избыточность
# когда ты задаёшь значение, а потом проверяешь его
@transactional
def toggle_player_ball_donate(db: DB, user_id: int) -> Result[bool, str]:
    cursor = db.conn.execute(
        """
        UPDATE players
        SET ball_donate = CASE
            WHEN COALESCE(ball_donate, 0) = 0 THEN 1
            ELSE 0
        END
        WHERE id = ?
        """,
        (user_id,),
    )
    if cursor.rowcount == 0:
        return Failure("Player not found")

    row = db.conn.execute(
        "SELECT ball_donate FROM players WHERE id = ?", (user_id,)
    ).fetchone()

    if row:
        return Success(bool(row[0]))
    return Failure("Player not found")


@transactional
def set_player_guest(db: DB, user_id: int, is_guest: bool) -> Result[None, str]:
    cursor = db.conn.execute(
        """
        UPDATE players
        SET is_guest = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (1 if is_guest else 0, user_id),
    )
    if cursor.rowcount > 0:
        return Success(None)
    return Failure("❌ Ошибка при изменении гостевого статуса игрока {user_id}")

@transactional
def get_guest_players(db: DB) -> Result[list[Player], str]:
    cursor = db.conn.execute(
        """
        SELECT id, name, fullname, ball_donate, is_guest, balance
        FROM players
        WHERE is_guest = 1
        ORDER BY COALESCE(fullname, name, id)
        """
    )
    return Success([_player_from_row(row) for row in cursor.fetchall()])


@transactional
def find_player_by_name(db: DB, query: str) -> Result[list[Player], str]:
    """Ищет игроков по части имени или fullname."""
    pattern = f"%{query}%"
    cursor = db.conn.execute(
        "SELECT id, name, fullname, is_guest, balance FROM players WHERE name LIKE ? OR fullname LIKE ? ORDER BY fullname ASC",
        (pattern, pattern),
    )
    return Success([_player_from_row(row) for row in cursor.fetchall()])


@transactional
def ensure_player(db: DB, user_id: int, name: str | None = None, fullname: str | None = None) \
    -> Result[None, str]:
    """
    Гарантирует наличие игрока в базе данных.

    При конфликте (игрок уже существует):
    - name обновляется свежим Telegram username, если он валидный и не пустой
    - fullname обновляется только если поле в БД пустое (NULL)

    Это сохраняет вручную установленные отображаемые имена, но не держит
    устаревшие Telegram username.
    """
    name = normalize_telegram_username(name)
    db.conn.execute(
        """
        INSERT INTO players (id, name, fullname)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = COALESCE(excluded.name, players.name),
            fullname = COALESCE(players.fullname, excluded.fullname),
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, name, fullname),
    )
    return Success(None)


@transactional
def get_poll_templates(db: DB) -> Result[list[PollTemplate], str]:
    """Возвращает все шаблоны опросов из БД."""
    template_rows = db.conn.execute(
        "SELECT * FROM poll_templates ORDER BY id"
    ).fetchall()
    subs_by_template: dict[int, list[int]] = defaultdict(list)
    sub_rows = db.conn.execute(
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
    return Success(templates)


@transactional
def add_poll_subscription(db: DB, poll_template_id: int, user_id: int) \
    -> Result[None, str]:
    """Добавляет игрока в подписчики конкретного шаблона опроса."""
    hall_row = db.conn.execute(
        "SELECT 1 FROM poll_templates WHERE id = ?",
        (poll_template_id,),
    ).fetchone()
    if hall_row is None:
        return Failure("missing_hall")

    player_row = db.conn.execute(
        "SELECT 1 FROM players WHERE id = ?",
        (user_id,),
    ).fetchone()
    if player_row is None:
        return Failure("missing_player")

    existing_row = db.conn.execute(
        """
        SELECT 1 FROM poll_subscriptions
        WHERE poll_template_id = ? AND user_id = ?
        """,
        (poll_template_id, user_id),
    ).fetchone()
    if existing_row is not None:
        return Failure("duplicate")

    db.conn.execute(
        """
        INSERT INTO poll_subscriptions (poll_template_id, user_id)
        VALUES (?, ?)
        """,
        (poll_template_id, user_id),
    )
    return Success(None)

@transactional
def save_poll_template(
    db: DB,
    template: typing.Mapping[str, typing.Any],
    *,
    match_by: Literal["name", "id"] = "name",
) -> Result[int, str]:
    """Сохраняет или обновляет шаблон опроса и его подписчиков.

    По умолчанию сохраняет старое поведение: upsert по уникальному ``name``.
    ``match_by="id"`` используется для админского редактирования и выполняет
    только UPDATE существующей строки без создания нового шаблона.
    """
    if match_by not in {"name", "id"}:
        return Failure("match_by должен быть 'name' или 'id'")

    values = (
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
        template.get("cost_per_game", 1500),
        template.get("enabled", 1),
    )

    if match_by == "name":
        db.conn.execute(
            """
            INSERT INTO poll_templates (
                name, place, message, open_day, open_hour_utc, open_minute_utc,
                game_day, game_hour_utc, game_minute_utc, cost, cost_per_game, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                cost_per_game = excluded.cost_per_game,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        row = db.conn.execute(
            "SELECT id FROM poll_templates WHERE name = ?", (template["name"],)
        ).fetchone()
        poll_template_id = int(row[0])
    else:
        poll_template_id = int(template["id"])
        try:
            cursor = db.conn.execute(
                """
                UPDATE poll_templates
                SET
                    name = ?,
                    place = ?,
                    message = ?,
                    open_day = ?,
                    open_hour_utc = ?,
                    open_minute_utc = ?,
                    game_day = ?,
                    game_hour_utc = ?,
                    game_minute_utc = ?,
                    cost = ?,
                    cost_per_game = ?,
                    enabled = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (*values, poll_template_id),
            )
        except sqlite3.IntegrityError as e:
            return Failure("Имя должно быть уникальным")
        if cursor.rowcount == 0:
            return Failure("❌ Ошибка при обновлении шаблона опроса")

    # Обновляем подписчиков если они переданы
    if "subs" in template:
        db.conn.execute(
            "DELETE FROM poll_subscriptions WHERE poll_template_id = ?",
            (poll_template_id,),
        )
        for user_id in template["subs"]:
            db.conn.execute(
                """
                INSERT OR IGNORE INTO poll_subscriptions (poll_template_id, user_id)
                VALUES (?, ?)
                """,
                (poll_template_id, user_id),
            )
    return Success(poll_template_id)


@transactional
def clear_paid_poll_subscriptions(db: DB) -> Result[None, str]:
    db.conn.execute(
        """
        DELETE FROM poll_subscriptions
        WHERE poll_template_id IN (
            SELECT id FROM poll_templates WHERE cost > 0
        )
        """
    )
    return Success(None)

@transactional
def add_transaction(
    db: DB,
    player_id: int,
    amount: int,
    description: str,
    poll_template_id: int | None = None,
    poll_name_snapshot: str | None = None,
) -> Result[None, str]:
    """
    Добавляет транзакцию в историю.

    Args:
        player_id: ID игрока
        amount: Сумма транзакции (отрицательная для списания)
        description: Описание транзакции
        poll_template_id: ID шаблона опроса (необязательно)
        poll_name_snapshot: Историческое имя зала в момент транзакции
    """
    db.conn.execute(
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
    db.logger.debug(
        "✅ Транзакция добавлена: "
        f"player_id={player_id}, amount={amount}, poll_template_id={poll_template_id}, "
        f"poll_name_snapshot={poll_name_snapshot}"
    )
    return Result(None)


# ── Fund (касса) ────────────────────────────────────────────────────────────

@transactional
def get_fund_balance(db: DB) -> Result[int, str]:
    """Возвращает текущий баланс кассы."""
    row = db.conn.execute(
        "SELECT value FROM kv_store WHERE key = ?", (FUND_BALANCE_KEY,)
    ).fetchone()

    if row:
        return Result(int(row[0]))
    return Failure("❌ Ошибка при получении баланса кассы")

@transactional
def update_fund_balance(db: DB, amount: int) -> Result[int, str]:
    """
    Атомарно изменяет баланс кассы на указанную сумму.

    Args:
        amount: Сумма изменения (положительная — пополнение, отрицательная — списание)
    """
    row = db.conn.execute(
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
    new_balance = int(row[0]) if row else 0
    db.logger.info(f"💰 Касса изменена на {amount:+d}, новый баланс: {new_balance}")
    return Result(new_balance)


@transactional
def update_player_and_fund_balance_atomic(
    db: DB,
    player_id: int,
    amount: int,
    description: str,
    poll_template_id: int | None = None,
    poll_name_snapshot: str | None = None,
) -> Result[None, str]:
    """
    Атомарно изменяет баланс игрока, кассу и добавляет транзакцию в одной транзакции.

    Используется для оплаты игроков, где все три операции должны выполниться
    вместе или откатиться целиком.

    Args:
        player_id: ID игрока
        amount: Сумма изменения баланса (положительная — оплата)
        description: Описание транзакции
        poll_template_id: ID шаблона опроса (необязательно)
        poll_name_snapshot: Историческое имя зала (необязательно)

    Returns:
        True если все операции успешны, иначе False
    """
    # 1. Обновить баланс игрока
    cursor = db.conn.execute(
        "UPDATE players SET balance = balance + ? WHERE id = ?",
        (amount, player_id),
    )
    if cursor.rowcount == 0:
        return Failure(f"Игрок {player_id} не найден для обновления баланса")

    # 2. Обновить баланс кассы (атомарно в той же транзакции)
    db.conn.execute(
        """
        INSERT INTO kv_store(key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = CAST(kv_store.value AS INTEGER) + CAST(excluded.value AS INTEGER),
            updated_at = CURRENT_TIMESTAMP
        """,
        (FUND_BALANCE_KEY, amount),
    )

    # 3. Добавить транзакцию
    db.conn.execute(
        """
        INSERT INTO transactions (
            player_id, amount, description, poll_template_id, poll_name_snapshot
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (player_id, amount, description, poll_template_id, poll_name_snapshot),
    )

    db.logger.info(
        f"💰 Атомарно обновлён баланс игрока {player_id}: {amount:+d}, "
        f"касса +{amount:+d}, транзакция: {description}"
    )
    return Success(None)


@transactional
def update_player_and_transaction_atomic(
    db: DB,
    player_id: int,
    amount: int,
    description: str,
    poll_template_id: int | None = None,
    poll_name_snapshot: str | None = None,
) -> Result[None, str]:
    """
    Атомарно изменяет баланс игрока и добавляет транзакцию (без изменения кассы).

    Используется для восстановления баланса (/restore), где касса не меняется.

    Args:
        player_id: ID игрока
        amount: Сумма изменения баланса
        description: Описание транзакции
        poll_template_id: ID шаблона опроса (необязательно)
        poll_name_snapshot: Историческое имя зала (необязательно)

    Returns:
        True если все операции успешны, иначе False
    """
    # 1. Обновить баланс игрока
    cursor = db.conn.execute(
        "UPDATE players SET balance = balance + ? WHERE id = ?",
        (amount, player_id),
    )
    if cursor.rowcount == 0:
        return Failure(f"Игрок {player_id} не найден для восстановления баланса")

    # 2. Добавить транзакцию (касса НЕ меняется)
    db.conn.execute(
        """
        INSERT INTO transactions (
            player_id, amount, description, poll_template_id, poll_name_snapshot
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (player_id, amount, description, poll_template_id, poll_name_snapshot),
    )

    db.logger.info(
        f"🔄 Атомарно восстановлен баланс игрока {player_id}: {amount:+d}, "
        f"касса не изменена, транзакция: {description}"
    )
    return Success(None)


# ── Hall payments (оплата залов) ─────────────────────────────────────────────

@transactional
def get_unpaid_halls(db: DB, month: str) -> Result[list[PollTemplate], str]:
    """
    Возвращает платные залы (cost_per_game > 0), ещё не оплаченные в данном месяце.

    Args:
        month: Месяц в формате "YYYY-MM"

    Returns:
        Список шаблонов опросов с cost_per_game > 0, не имеющих записи в hall_payments
    """
    from typing import cast

    template_rows = db.conn.execute(
        """
        SELECT pt.*
        FROM poll_templates pt
        WHERE pt.cost_per_game > 0
            AND pt.id NOT IN (
                SELECT hp.poll_template_id FROM hall_payments hp WHERE hp.month = ?
            )
        ORDER BY pt.id
        """,
        (month,),
    ).fetchall()
    subs_by_template: dict[int, list[int]] = defaultdict(list)
    sub_rows = db.conn.execute(
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
    return Success(templates)

# deprecated
@transactional
def record_hall_payment(db: DB, poll_template_id: int, month: str, amount: int) -> Result[None, str]:
    """
    Записывает оплату зала за месяц.

    Args:
        poll_template_id: ID шаблона опроса
        month: Месяц в формате "YYYY-MM"
        amount: Сумма оплаты

    Returns:
        True если запись успешно добавлена, False при ошибке
    """
    db.conn.execute(
        """
        INSERT INTO hall_payments (poll_template_id, month, amount)
        VALUES (?, ?, ?)
        """,
        (poll_template_id, month, amount),
    )
    db.logger.info(
        "✅ Оплата зала записана: "
        f"poll_template_id={poll_template_id}, месяц={month}, сумма={amount}"
    )
    return Success(None)

@transactional
def record_hall_payment_atomic(
    db: DB,
    payer_id: int,
    poll_template_id: int,
    month: str,
    amount: int,
    poll_name: str,
) -> Result[str, str]:
    """
    Атомарно записывает оплату зала, уменьшает кассу и добавляет транзакцию.

    Используется при оплате зала из кассы, где все три операции должны
    выполниться вместе или откатиться целиком.

    Args:
        payer_id: ID администратора, производящего оплату
        poll_template_id: ID шаблона опроса
        month: Месяц в формате "YYYY-MM"
        amount: Сумма оплаты (положительная)
        poll_name: Название зала для транзакции

    Returns:
        "success" если все операции успешны
        "duplicate" если зал уже был оплачен за этот месяц
        "error" для прочих ошибок
    """
    # 1. Записать оплату зала
    try:
        db.conn.execute(
            """
            INSERT INTO hall_payments (poll_template_id, month, amount)
            VALUES (?, ?, ?)
            """,
            (poll_template_id, month, amount),
        )
    except sqlite3.IntegrityError:
        db.logger.warning(
            f"⚠️ Зал {poll_name} за {month} уже оплачен"
        )
        return Failure("duplicate")

    # 2. Уменьшить баланс кассы
    db.conn.execute(
        """
        INSERT INTO kv_store(key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = CAST(kv_store.value AS INTEGER) + CAST(excluded.value AS INTEGER),
            updated_at = CURRENT_TIMESTAMP
        """,
        (FUND_BALANCE_KEY, -amount),
    )

    # 3. Добавить транзакцию
    db.conn.execute(
        """
        INSERT INTO transactions (
            player_id, amount, description, poll_template_id, poll_name_snapshot
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            payer_id,
            -amount,
            f"Оплата зала: {poll_name} ({month})",
            poll_template_id,
            poll_name,
        ),
    )

    db.logger.info(
        f"🏟 Атомарно оплачен зал {poll_name} за {month}: {amount}₽, "
        f"касса -{amount}₽, транзакция добавлена"
    )
    return Success("success")


@transactional
def create_game(
    db: DB,
    *,
    poll_id: str,
    kind: str,
    status: str,
    poll_template_id: int | None,
    poll_name_snapshot: str,
    question_snapshot: str,
    chat_id: int,
    poll_message_id: int,
    opened_at: str,
    game_date: str | None = None,
    place_snapshot: str | None = None,
    cost_snapshot: int = 0,
    cost_per_game_snapshot: int = 0,
    target_month_snapshot: str | None = None,
    options: list[str] | None = None,
    option_poll_names: list[str | None] | None = None,
    info_message_id: int | None = None,
    final_message_id: int | None = None,
    last_info_text: str = "⏳ Идёт сбор голосов...",
) -> Result[None, str]:
    """
    Создаёт или обновляет запись игры/голосования.

    Returns:
        True если запись успешно создана/обновлена, иначе False
    """
    db.conn.execute(
        """
        INSERT INTO games (
            poll_id, kind, status, poll_template_id, poll_name_snapshot,
            question_snapshot, chat_id, poll_message_id, info_message_id,
            final_message_id, opened_at, game_date, place_snapshot,
            cost_snapshot, cost_per_game_snapshot, options_json,
            option_poll_names_json, target_month_snapshot, last_info_text, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(poll_id) DO UPDATE SET
            kind = excluded.kind,
            status = excluded.status,
            poll_template_id = excluded.poll_template_id,
            poll_name_snapshot = excluded.poll_name_snapshot,
            question_snapshot = excluded.question_snapshot,
            chat_id = excluded.chat_id,
            poll_message_id = excluded.poll_message_id,
            info_message_id = COALESCE(excluded.info_message_id, games.info_message_id),
            final_message_id = COALESCE(excluded.final_message_id, games.final_message_id),
            opened_at = excluded.opened_at,
            game_date = excluded.game_date,
            place_snapshot = excluded.place_snapshot,
            cost_snapshot = excluded.cost_snapshot,
            cost_per_game_snapshot = excluded.cost_per_game_snapshot,
            options_json = excluded.options_json,
            option_poll_names_json = excluded.option_poll_names_json,
            target_month_snapshot = excluded.target_month_snapshot,
            last_info_text = excluded.last_info_text,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            poll_id,
            kind,
            status,
            poll_template_id,
            poll_name_snapshot,
            question_snapshot,
            chat_id,
            poll_message_id,
            info_message_id,
            final_message_id,
            opened_at,
            game_date,
            place_snapshot,
            cost_snapshot,
            cost_per_game_snapshot,
            json.dumps(options or [], ensure_ascii=False),
            json.dumps(option_poll_names or [], ensure_ascii=False),
            target_month_snapshot,
            last_info_text,
        ),
    )
    db.logger.info(f"✅ Запись игры создана/обновлена: poll_id={poll_id}")
    return Success(None)


@transactional
def update_game_info_message(
    db: DB,
    poll_id: str,
    *,
    info_message_id: int | None,
    last_info_text: str | None = None,
) -> Result[None, str]:
    """Обновляет ID информационного сообщения и кеш текста."""
    db.conn.execute(
        """
        UPDATE games
        SET info_message_id = ?,
            last_info_text = COALESCE(?, last_info_text),
            updated_at = CURRENT_TIMESTAMP
        WHERE poll_id = ?
        """,
        (info_message_id, last_info_text, poll_id),
    )
    return Success(None)

@transactional
def update_game_last_info_text(db: DB, poll_id: str, text: str) -> Result[None, str]:
    """Обновляет последний отправленный текст промежуточного сообщения."""
    db.conn.execute(
        """
        UPDATE games
        SET last_info_text = ?, updated_at = CURRENT_TIMESTAMP
        WHERE poll_id = ?
        """,
        (text, poll_id),
    )
    return Success(None)


@transactional
def close_game(
    db: DB,
    poll_id: str,
    *,
    status: str = "closed",
    closed_at: str,
    final_message_id: int | None = None,
) -> Result[None, str]:
    """Закрывает игру в БД."""
    db.conn.execute(
        """
        UPDATE games
        SET status = ?,
            closed_at = ?,
            final_message_id = COALESCE(?, final_message_id),
            updated_at = CURRENT_TIMESTAMP
        WHERE poll_id = ?
        """,
        (status, closed_at, final_message_id, poll_id),
    )
    return Success(None)


@transactional
def save_monthly_vote(db: DB, game_poll_id: str, player_id: int, option_ids: list[int]) \
-> Result[None, str]:
    """Сохраняет выбор пользователя в месячном голосовании."""
    db.conn.execute(
        """
        INSERT INTO monthly_poll_votes (game_poll_id, player_id, option_ids_json, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(game_poll_id, player_id) DO UPDATE SET
            option_ids_json = excluded.option_ids_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (game_poll_id, player_id, json.dumps(option_ids)),
    )
    return Success(None)


@transactional
def load_monthly_votes(db: DB, game_poll_id: str) -> Result[dict[int, list[int]], str]:
    """Возвращает сохранённые голоса месячного опроса."""
    rows = db.conn.execute(
        """
        SELECT player_id, option_ids_json
        FROM monthly_poll_votes
        WHERE game_poll_id = ?
        """,
        (game_poll_id,),
    ).fetchall()
    result: dict[int, list[int]] = {}
    for player_id, option_ids_json in rows:
        loaded = deserialize_json(option_ids_json)
        match loaded:
            case Failure(error):
                return Failure(error).alt(str)
            case Success(payload):
                result[int(player_id)] = payload
    return Success(result)


def _make_game_info(row: sqlite3.Row) -> Result[GameInfo, str]:
    if not row:
        return Failure("❌ Ошибка при десереализации игры")
    return Success({
        "poll_id": row["poll_id"],
        "kind": row["kind"],
        "status": row["status"],
        "poll_template_id": row["poll_template_id"],
        "poll_name_snapshot": row["poll_name_snapshot"],
        "question_snapshot": row["question_snapshot"],
        "chat_id": row["chat_id"],
        "poll_message_id": row["poll_message_id"],
        "info_message_id": row["info_message_id"],
        "final_message_id": row["final_message_id"],
        "opened_at": row["opened_at"],
        "closed_at": row["closed_at"],
        "game_date": row["game_date"],
        "place_snapshot": row["place_snapshot"],
        "cost_snapshot": row["cost_snapshot"],
        "cost_per_game_snapshot": row["cost_per_game_snapshot"],
        "options_json": row["options_json"],
        "option_poll_names_json": row["option_poll_names_json"],
        "last_info_text": row["last_info_text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "target_month_snapshot": row["target_month_snapshot"],
    })

@transactional
def get_game(db: DB, poll_id: str) -> Result[GameInfo, str]:
    """Возвращает игру по poll_id."""
    row = db.conn.execute(
        "SELECT * FROM games WHERE poll_id = ?",
        (poll_id,),
    ).fetchone()

    return _make_game_info(row)


@transactional
def get_open_games(db: DB) -> Result[list[GameInfo], str]:
    """Возвращает все открытые игры."""
    rows = db.conn.execute(
        "SELECT * FROM games WHERE status = 'open' ORDER BY opened_at"
    ).fetchall()
    return Fold.collect(
        map(_make_game_info, rows),
        Success(()),
    ).map(list)  # pyright: ignore[reportAttributeAccessIssue]


@transactional
def get_open_game_by_template_id(db: DB, poll_template_id: int) -> Result[GameInfo, str]:
    """Возвращает открытую regular-игру по шаблону."""
    row = db.conn.execute(
        """
        SELECT *
        FROM games
        WHERE status = 'open'
            AND kind = 'regular'
            AND poll_template_id = ?
        ORDER BY opened_at DESC
        LIMIT 1
        """,
        (poll_template_id,),
    ).fetchone()
    return _make_game_info(row)


@transactional
def get_open_monthly_game(db: DB) -> Result[GameInfo, str]:
    """Возвращает открытый месячный опрос."""
    row = db.conn.execute(
        """
        SELECT *
        FROM games
        WHERE status = 'open' AND kind = 'monthly_subscription'
        ORDER BY opened_at DESC
        LIMIT 1
        """
    ).fetchone()
    return _make_game_info(row)


@transactional
def save_game_participants(
    db: DB, game_poll_id: str, participants: list[dict[str, Any]]
) -> Result[None, str]:
    """Сохраняет состав и финансовый итог игры."""
    db.conn.execute(
        "DELETE FROM game_participants WHERE game_poll_id = ?", (game_poll_id,)
    )
    for participant in participants:
        db.conn.execute(
            """
            INSERT INTO game_participants (
                game_poll_id, player_id, roster_bucket, sort_order,
                is_subscriber, is_guest, guest_free_reason,
                charged_amount, charge_source, balance_before, balance_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_poll_id,
                participant["player_id"],
                participant["roster_bucket"],
                participant["sort_order"],
                1 if participant.get("is_subscriber") else 0,
                1 if participant.get("is_guest") else 0,
                participant.get("guest_free_reason", "none"),
                int(participant.get("charged_amount", 0) or 0),
                participant.get("charge_source", "none"),
                participant.get("balance_before"),
                participant.get("balance_after"),
            ),
        )
    return Success(None)


@transactional
def count_player_regular_participations(db: DB, player_id: int) -> Result[int, str]:
    """Считает прошлые участия игрока в regular-играх в main/reserve."""
    row = db.conn.execute(
        """
        SELECT COUNT(*)
        FROM game_participants gp
        JOIN games g ON g.poll_id = gp.game_poll_id
        WHERE gp.player_id = ?
            AND g.kind = 'regular'
            AND g.status = 'closed'
            AND gp.roster_bucket IN ('main', 'reserve')
        """,
        (player_id,),
    ).fetchone()
    return Success(int(row[0] or 0)) if row else Failure("no data")

# --- Stats

@safe
def _month_bounds(month: str) -> tuple[str, str]:
    year_str, month_str = month.split("-")
    year = int(year_str)
    month_num = int(month_str)
    start = f"{year:04d}-{month_num:02d}-01"
    if month_num == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month_num + 1:02d}-01"
    return start, end


def _shift_month(month: str, delta: int) -> str:
    """Сдвигает месяц ``YYYY-MM`` на delta месяцев."""
    year_str, month_str = month.split("-")
    year = int(year_str)
    month_num = int(month_str)
    month_index = year * 12 + (month_num - 1) + delta
    shifted_year = month_index // 12
    shifted_month = month_index % 12 + 1
    return f"{shifted_year:04d}-{shifted_month:02d}"


def _stats_from_row(row: sqlite3.Row, balance: int) -> Result[PlayerStats, str]:
    if not row:
        return Failure("no data")
    return Success({
        "games_total": int(row["games_total"] or 0),
        "main_count": int(row["main_count"] or 0),
        "reserve_count": int(row["reserve_count"] or 0),
        "booked_count": int(row["booked_count"] or 0),
        "subscription_games": int(row["subscription_games"] or 0),
        "single_game_count": int(row["single_game_count"] or 0),
        "single_game_sum": int(row["single_game_sum"] or 0),
        "balance": balance,
    })

@transactional
def get_single_game_income_stats(
    db: DB, months_back: int = 3, before_month: str = ""
) -> Result[dict[str, Any], str]:
    """
    Возвращает средний доход с разовых игроков по закрытым платным играм.

    Статистика считается только по regular-играм с положительной стоимостью
    аренды. Игроки из листа ожидания не учитываются в доходе с разовых.
    """
    months_back = max(months_back, 1)
    before = before_month or datetime.now(UTC).strftime("%Y-%m")
    start_month = _shift_month(before, -months_back)
    start, _ = _month_bounds(start_month).unwrap()
    end, _ = _month_bounds(before).unwrap()
    params: list[str] = [start, end]
    base_where = """
        g.kind = 'regular'
        AND g.status = 'closed'
        AND g.cost_per_game_snapshot > 0
        AND g.closed_at >= ?
        AND g.closed_at < ?
    """
    join_sql = """
        LEFT JOIN game_participants gp
            ON gp.game_poll_id = g.poll_id
            AND gp.charge_source = 'single_game'
            AND gp.roster_bucket != 'booked'
    """
    global_row = db.conn.execute(
        f"""
        SELECT
            COUNT(DISTINCT g.poll_id) AS games_count,
            COUNT(gp.player_id) AS single_game_charges,
            COALESCE(SUM(gp.charged_amount), 0) AS single_game_sum
        FROM games g
        {join_sql}
        WHERE {base_where}
        """,
        params,
    ).fetchone()
    hall_rows = db.conn.execute(
        f"""
        SELECT
            g.poll_template_id,
            COUNT(DISTINCT g.poll_id) AS games_count,
            COUNT(gp.player_id) AS single_game_charges,
            COALESCE(SUM(gp.charged_amount), 0) AS single_game_sum
        FROM games g
        {join_sql}
        WHERE {base_where}
            AND g.poll_template_id IS NOT NULL
        GROUP BY g.poll_template_id
        """,
        params,
    ).fetchall()

    def build_row(row: sqlite3.Row | None) -> dict[str, Any]:
        games_count = int(row["games_count"] or 0) if row else 0
        single_game_sum = int(row["single_game_sum"] or 0) if row else 0
        return {
            "games_count": games_count,
            "single_game_charges": int(row["single_game_charges"] or 0) if row else 0,
            "single_game_sum": single_game_sum,
            "avg_income_per_game": (
                single_game_sum / games_count if games_count > 0 else 0.0
            ),
        }

    return Success({
        "global": build_row(global_row),
        "by_poll_template_id": {
            int(row["poll_template_id"]): build_row(row) for row in hall_rows
        },
    })


@transactional
def get_stats_summary(db: DB, month: str = "") \
    -> Result[GamePollStatsSummary, str]:
    """Сводная статистика по regular-играм."""
    start, end = _month_bounds(month).value_or((None, None))
    filter_sql = ""
    params: list[Any] = []
    if start and end:
        filter_sql = "AND g.closed_at >= ? AND g.closed_at < ?"
        params.extend([start, end])

    summary_row = db.conn.execute(
        f"""
        SELECT
            COUNT(DISTINCT g.poll_id) AS games_count,
            COUNT(DISTINCT gp.player_id) AS unique_players,
            COUNT(CASE WHEN gp.charge_source = 'subscription' THEN 1 END) AS subscription_uses,
            COUNT(CASE WHEN gp.charge_source = 'single_game' THEN 1 END) AS single_game_charges,
            COALESCE(SUM(CASE WHEN gp.charge_source = 'single_game' THEN gp.charged_amount ELSE 0 END), 0) AS single_game_sum
        FROM games g
        LEFT JOIN game_participants gp ON gp.game_poll_id = g.poll_id
        WHERE g.kind = 'regular' AND g.status = 'closed' {filter_sql}
        """,
        params,
    ).fetchone()

    roster_rows = db.conn.execute(
        f"""
        SELECT g.poll_id, COUNT(gp.player_id) AS interested
        FROM games g
        LEFT JOIN game_participants gp ON gp.game_poll_id = g.poll_id
        WHERE g.kind = 'regular' AND g.status = 'closed' {filter_sql}
        GROUP BY g.poll_id
        """,
        params,
    ).fetchall()
    main_rows = db.conn.execute(
        f"""
        SELECT g.poll_id, COUNT(gp.player_id) AS main_count
        FROM games g
        LEFT JOIN game_participants gp
            ON gp.game_poll_id = g.poll_id AND gp.roster_bucket = 'main'
        WHERE g.kind = 'regular' AND g.status = 'closed' {filter_sql}
        GROUP BY g.poll_id
        """,
        params,
    ).fetchall()
    monthly_filter = ""
    monthly_params: list[Any] = []
    if start and end:
        monthly_filter = "AND opened_at >= ? AND opened_at < ?"
        monthly_params = [start, end]
    monthly_row = db.conn.execute(
        f"""
        SELECT COUNT(*) AS monthly_polls
        FROM games
        WHERE kind = 'monthly_subscription' {monthly_filter}
        """,
        monthly_params,
    ).fetchone()

    transactions_filter = ""
    tx_params: list[Any] = []
    hall_filter = ""
    hall_params: list[Any] = []
    if start and end:
        transactions_filter = "WHERE created_at >= ? AND created_at < ?"
        tx_params = [start, end]
        hall_filter = "WHERE paid_at >= ? AND paid_at < ?"
        hall_params = [start, end]

    payments_row = db.conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS topups_sum FROM transactions {transactions_filter} AND amount > 0"
        if transactions_filter
        else "SELECT COALESCE(SUM(amount), 0) AS topups_sum FROM transactions WHERE amount > 0",
        tx_params,
    ).fetchone()
    hall_row = db.conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS hall_payments_sum FROM hall_payments {hall_filter}"
        if hall_filter
        else "SELECT COALESCE(SUM(amount), 0) AS hall_payments_sum FROM hall_payments",
        hall_params,
    ).fetchone()

    games_count = int(summary_row["games_count"] or 0) if summary_row else 0
    unique_players = int(summary_row["unique_players"] or 0) if summary_row else 0
    avg_interest = (
        sum(int(row["interested"]) for row in roster_rows) / len(roster_rows)
        if roster_rows
        else 0.0
    )
    avg_main = (
        sum(int(row["main_count"]) for row in main_rows) / len(main_rows)
        if main_rows
        else 0.0
    )
    subscription_uses = int(summary_row["subscription_uses"] or 0) if summary_row else 0
    single_game_charges = int(summary_row["single_game_charges"] or 0) if summary_row else 0
    single_game_sum = int(summary_row["single_game_sum"] or 0) if summary_row else 0
    topups_sum = int(payments_row["topups_sum"] or 0) if payments_row else 0
    hall_payments_sum = int(hall_row["hall_payments_sum"] or 0) if hall_row else 0
    fund_balance = get_fund_balance(db)
    monthly_polls = int(monthly_row["monthly_polls"] or 0) if monthly_row else 0
    return Success({
        "games_count": games_count,
        "unique_players": unique_players,
        "avg_main": avg_main,
        "avg_interest": avg_interest,
        "subscription_uses": subscription_uses,
        "single_game_charges": single_game_charges,
        "single_game_sum": single_game_sum,
        "topups_sum": topups_sum,
        "hall_payments_sum": hall_payments_sum,
        "fund_balance": fund_balance.value_or(0),
        "monthly_polls": monthly_polls,
    })


@transactional
def get_poll_stats(db: DB, poll_template_id: int, month: str = "") \
    -> Result[GamePollStats, str]:
    """Статистика по одному залу."""
    start, end = _month_bounds(month).value_or((None, None))
    filter_sql = ""
    params: list[Any] = [poll_template_id]
    if start and end:
        filter_sql = "AND g.closed_at >= ? AND g.closed_at < ?"
        params.extend([start, end])
    games = db.conn.execute(
        f"""
        SELECT g.poll_id, g.poll_name_snapshot, g.closed_at
        FROM games g
        WHERE g.kind = 'regular' AND g.status = 'closed'
            AND g.poll_template_id = ? {filter_sql}
        ORDER BY g.closed_at DESC
        """,
        params,
    ).fetchall()
    stats_rows = db.conn.execute(
        f"""
        SELECT
            COUNT(DISTINCT g.poll_id) AS games_count,
            COUNT(DISTINCT gp.player_id) AS unique_players,
            COUNT(CASE WHEN gp.charge_source = 'subscription' THEN 1 END) AS subscription_uses,
            COALESCE(SUM(CASE WHEN gp.charge_source = 'single_game' THEN gp.charged_amount ELSE 0 END), 0) AS single_game_sum
        FROM games g
        LEFT JOIN game_participants gp ON gp.game_poll_id = g.poll_id
        WHERE g.kind = 'regular' AND g.status = 'closed'
            AND g.poll_template_id = ? {filter_sql}
        """,
        params,
    ).fetchone()
    avg_interest_rows = db.conn.execute(
        f"""
        SELECT g.poll_id, COUNT(gp.player_id) AS interested
        FROM games g
        LEFT JOIN game_participants gp ON gp.game_poll_id = g.poll_id
        WHERE g.kind = 'regular' AND g.status = 'closed'
            AND g.poll_template_id = ? {filter_sql}
        GROUP BY g.poll_id
        """,
        params,
    ).fetchall()
    main_rows = db.conn.execute(
        f"""
        SELECT g.poll_id, COUNT(gp.player_id) AS main_count
        FROM games g
        LEFT JOIN game_participants gp
            ON gp.game_poll_id = g.poll_id AND gp.roster_bucket = 'main'
        WHERE g.kind = 'regular' AND g.status = 'closed'
            AND g.poll_template_id = ? {filter_sql}
        GROUP BY g.poll_id
        """,
        params,
    ).fetchall()

    games_count = int(stats_rows["games_count"] or 0) if stats_rows else 0
    unique_players = int(stats_rows["unique_players"] or 0) if stats_rows else 0
    avg_main = (
        sum(int(row["main_count"]) for row in main_rows) / len(main_rows)
        if main_rows
        else 0.0
    )
    avg_interest = (
        sum(int(row["interested"]) for row in avg_interest_rows) / len(avg_interest_rows)
        if avg_interest_rows
        else 0.0
    )
    subscription_uses = int(stats_rows["subscription_uses"] or 0) if stats_rows else 0
    single_game_sum = int(stats_rows["single_game_sum"] or 0) if stats_rows else 0
    last_game = games[0]["closed_at"] if games else None
    poll_name_snapshot = games[0]["poll_name_snapshot"] if games else ""
    return Success({
        "games_count": games_count,
        "unique_players": unique_players,
        "avg_main": avg_main,
        "avg_interest": avg_interest,
        "subscription_uses": subscription_uses,
        "single_game_sum": single_game_sum,
        "last_game": last_game,
        "poll_name_snapshot": poll_name_snapshot,
    })

@transactional
def get_player_stats(
    db: DB,
    player_id: int,
    month: str = "",
) -> Result[PlayerStats, str]:
    """Статистика по игроку."""
    start, end = _month_bounds(month).value_or((None, None))
    filter_sql = ""
    params: list[Any] = [player_id]
    if start and end:
        filter_sql = "AND g.closed_at >= ? AND g.closed_at < ?"
        params.extend([start, end])
    row = db.conn.execute(
        f"""
        SELECT
            COUNT(*) AS games_total,
            COUNT(CASE WHEN gp.roster_bucket = 'main' THEN 1 END) AS main_count,
            COUNT(CASE WHEN gp.roster_bucket = 'reserve' THEN 1 END) AS reserve_count,
            COUNT(CASE WHEN gp.roster_bucket = 'booked' THEN 1 END) AS booked_count,
            COUNT(CASE WHEN gp.charge_source = 'subscription' THEN 1 END) AS subscription_games,
            COUNT(CASE WHEN gp.charge_source = 'single_game' THEN 1 END) AS single_game_count,
            COALESCE(SUM(CASE WHEN gp.charge_source = 'single_game' THEN gp.charged_amount ELSE 0 END), 0) AS single_game_sum
        FROM game_participants gp
        JOIN games g ON g.poll_id = gp.game_poll_id
        WHERE gp.player_id = ? AND g.kind = 'regular' AND g.status = 'closed' {filter_sql}
        """,
        params,
    ).fetchone()
    balance = get_player_balance(db, player_id)
    match balance:
        case Failure(_):
            return Failure("Баланс игрока не найден")
        case Success(_):
            return _stats_from_row(row, balance.value_or(0))
    raise RuntimeError("Недостижимый код")

# --- Messages

@transactional
def get_messages(
    db: DB,
    chat_id: int,
    limit: int = 100,
) -> Result[list[dict[str, Any]], str]:
    rows = db.conn.execute(
        """
        SELECT
            m.message_id,
            m.chat_id,
            m.user_id,
            m.text,
            m.date,
            COALESCE(p.name, 'unknown') AS username
        FROM messages m
        LEFT JOIN players p ON m.user_id = p.id
        WHERE m.chat_id = ?
        ORDER BY m.date DESC
        LIMIT ?
        """,
        (chat_id, limit),
    ).fetchall()
    return Success([dict(row) for row in rows])

@transactional
def insert_message(
    db: DB,
    message_id: int,
    chat_id: int,
    user_id: int,
    text: str,
    date: int,
) -> Result[None, str]:
    db.conn.execute(
        """
        INSERT INTO messages (message_id, chat_id, user_id, text, date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (message_id, chat_id, user_id, text, date),
    )
    return Result(None)
