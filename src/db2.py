"""Объект управления базой данных"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from returns.result import Failure, Result, Success, safe

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
        except Exception:
            db.conn.rollback()
            db.logger.exception("Ошибка транзакции БД; выполнен rollback")
            return Failure("Ошибка транзакции БД")
    return wrapper


@safe(exceptions=(TypeError, ValueError))
def serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


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


class DB:
    def __init__(self, path: str, logger: logging.Logger):
        self.logger = logger
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
