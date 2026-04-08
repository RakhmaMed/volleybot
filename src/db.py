"""Утилиты для хранения состояния бота в SQLite."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import typing
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .types import PollTemplate

# Ключи хранения в kv_store
BOT_STATE_KEY = "bot_state"
POLL_STATE_KEY = "poll_state"
FUND_BALANCE_KEY = "fund_balance"
SCHEMA_VERSION = 8
BACKUP_RETENTION_DAYS = 10


def _get_db_path() -> str:
    """Возвращает путь к базе данных с учётом переменной окружения."""
    override: str | None = os.getenv("VOLLEYBOT_DB_PATH")
    if override:
        return override
    return str(Path(__file__).parent.parent / "data" / "volleybot.db")


def _get_backup_dir(db_path: str | None = None) -> Path | None:
    """Возвращает директорию для бэкапов рядом с основной БД."""
    resolved_db_path = db_path or _get_db_path()
    if resolved_db_path == ":memory:":
        return None
    return Path(resolved_db_path).resolve().parent / "backups"


def _sanitize_backup_reason(reason: str) -> str:
    """Нормализует причину бэкапа для имени файла."""
    normalized = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in reason.strip().lower()
    )
    normalized = normalized.strip("_")
    return normalized or "manual"


def create_backup(reason: str) -> Path | None:
    """Создаёт snapshot SQLite БД и возвращает путь к файлу бэкапа."""
    db_path = _get_db_path()
    backup_dir = _get_backup_dir(db_path)
    if db_path == ":memory:" or backup_dir is None:
        logging.debug("ℹ️ Бэкап пропущен: используется in-memory БД")
        return None

    source_path = Path(db_path)
    if not source_path.exists():
        logging.warning("⚠️ Бэкап пропущен: файл БД ещё не создан")
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{timestamp}_{_sanitize_backup_reason(reason)}.sqlite3"

    try:
        source_conn = sqlite3.connect(str(source_path))
        dest_conn = sqlite3.connect(str(backup_path))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            source_conn.close()
        logging.info(f"🗄️ Создан бэкап БД: {backup_path}")
        return backup_path
    except sqlite3.Error:
        logging.exception(f"❌ Не удалось создать бэкап БД для события '{reason}'")
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            logging.exception("❌ Не удалось удалить повреждённый файл бэкапа")
        return None


def cleanup_old_backups(
    retention_days: int = BACKUP_RETENTION_DAYS,
    now: datetime | None = None,
) -> int:
    """Удаляет бэкапы старше retention_days и возвращает число удалённых файлов."""
    backup_dir = _get_backup_dir()
    if backup_dir is None or not backup_dir.exists():
        return 0

    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=retention_days)
    deleted = 0

    for path in backup_dir.glob("*.sqlite3"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            logging.exception(f"❌ Не удалось обработать файл бэкапа: {path}")

    if deleted:
        logging.info(
            f"🧹 Удалены старые бэкапы: {deleted} шт. старше {retention_days} дней"
        )
    return deleted


def init_db() -> None:
    """Создаёт или строго валидирует актуальную схему БД."""
    db_path: str = _get_db_path()
    logging.debug(f"Инициализация базы данных: {db_path}")

    # Для in-memory соединения каталоги не нужны
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        _create_base_tables(conn)
        _ensure_current_schema(conn)
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
    _create_indexes(conn)


EXPECTED_BUSINESS_TABLES = {
    "poll_templates",
    "poll_subscriptions",
    "transactions",
    "hall_payments",
    "games",
    "game_participants",
    "monthly_poll_votes",
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
}


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

    mismatches = _validate_schema_strict(conn)
    if mismatches:
        raise sqlite3.DatabaseError("Incompatible DB schema: " + "; ".join(mismatches))

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _validate_schema_strict(conn: sqlite3.Connection) -> list[str]:
    mismatches: list[str] = []
    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        actual_columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        missing_columns = sorted(expected_columns - actual_columns)
        unexpected_columns = sorted(actual_columns - expected_columns)
        if missing_columns:
            mismatches.append(f"{table_name}: missing columns {missing_columns}")
        if unexpected_columns:
            mismatches.append(f"{table_name}: unexpected columns {unexpected_columns}")
    return mismatches


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


def toggle_player_ball_donate(user_id: int) -> bool | None:
    """Переключает флаг ball_donate игрока и возвращает новое значение."""
    try:
        with _connect() as conn:
            cursor = conn.execute(
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
                conn.rollback()
                return None

            row = conn.execute(
                "SELECT ball_donate FROM players WHERE id = ?", (user_id,)
            ).fetchone()
            conn.commit()
            return bool(row[0]) if row is not None else None
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при переключении ball_donate игрока {user_id}")
        return None


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


def save_poll_template(template: typing.Mapping[str, typing.Any]) -> None:
    """Сохраняет или обновляет шаблон опроса и его подписчиков."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
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
                    template.get("cost_per_game", 1500),
                    template.get("enabled", 1),
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


def update_player_and_fund_balance_atomic(
    player_id: int,
    amount: int,
    description: str,
    poll_template_id: int | None = None,
    poll_name_snapshot: str | None = None,
) -> bool:
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
    try:
        init_db()
        with _connect() as conn:
            # 1. Обновить баланс игрока
            cursor = conn.execute(
                "UPDATE players SET balance = balance + ? WHERE id = ?",
                (amount, player_id),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                logging.warning(f"⚠️ Игрок {player_id} не найден для обновления баланса")
                return False

            # 2. Обновить баланс кассы (атомарно в той же транзакции)
            conn.execute(
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
            conn.execute(
                """
                INSERT INTO transactions (
                    player_id, amount, description, poll_template_id, poll_name_snapshot
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (player_id, amount, description, poll_template_id, poll_name_snapshot),
            )

            conn.commit()
            logging.info(
                f"💰 Атомарно обновлён баланс игрока {player_id}: {amount:+d}, "
                f"касса +{amount:+d}, транзакция: {description}"
            )
            return True
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка атомарного обновления баланса игрока {player_id}"
        )
        return False


def update_player_and_transaction_atomic(
    player_id: int,
    amount: int,
    description: str,
    poll_template_id: int | None = None,
    poll_name_snapshot: str | None = None,
) -> bool:
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
    try:
        init_db()
        with _connect() as conn:
            # 1. Обновить баланс игрока
            cursor = conn.execute(
                "UPDATE players SET balance = balance + ? WHERE id = ?",
                (amount, player_id),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                logging.warning(f"⚠️ Игрок {player_id} не найден для восстановления баланса")
                return False

            # 2. Добавить транзакцию (касса НЕ меняется)
            conn.execute(
                """
                INSERT INTO transactions (
                    player_id, amount, description, poll_template_id, poll_name_snapshot
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (player_id, amount, description, poll_template_id, poll_name_snapshot),
            )

            conn.commit()
            logging.info(
                f"🔄 Атомарно восстановлен баланс игрока {player_id}: {amount:+d}, "
                f"касса не изменена, транзакция: {description}"
            )
            return True
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка атомарного восстановления баланса игрока {player_id}"
        )
        return False


# ── Hall payments (оплата залов) ─────────────────────────────────────────────


def get_unpaid_halls(month: str) -> list[PollTemplate]:
    """
    Возвращает платные залы (cost_per_game > 0), ещё не оплаченные в данном месяце.

    Args:
        month: Месяц в формате "YYYY-MM"

    Returns:
        Список шаблонов опросов с cost_per_game > 0, не имеющих записи в hall_payments
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
                WHERE pt.cost_per_game > 0
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


def record_hall_payment_atomic(
    payer_id: int,
    poll_template_id: int,
    month: str,
    amount: int,
    poll_name: str,
) -> Literal["success", "duplicate", "error"]:
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
    try:
        init_db()
        with _connect() as conn:
            # 1. Записать оплату зала
            try:
                conn.execute(
                    """
                    INSERT INTO hall_payments (poll_template_id, month, amount)
                    VALUES (?, ?, ?)
                    """,
                    (poll_template_id, month, amount),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                logging.warning(
                    f"⚠️ Зал {poll_name} за {month} уже оплачен"
                )
                return "duplicate"

            # 2. Уменьшить баланс кассы
            conn.execute(
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
            conn.execute(
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

            conn.commit()
            logging.info(
                f"🏟 Атомарно оплачен зал {poll_name} за {month}: {amount}₽, "
                f"касса -{amount}₽, транзакция добавлена"
            )
            return "success"
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка атомарной оплаты зала {poll_name} за {month}"
        )
        return "error"


def create_game(
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
) -> bool:
    """
    Создаёт или обновляет запись игры/голосования.

    Returns:
        True если запись успешно создана/обновлена, иначе False
    """
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
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
            conn.commit()
            logging.info(f"✅ Запись игры создана/обновлена: poll_id={poll_id}")
            return True
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при создании игры poll_id={poll_id}")
        return False


def update_game_info_message(
    poll_id: str,
    *,
    info_message_id: int | None,
    last_info_text: str | None = None,
) -> None:
    """Обновляет ID информационного сообщения и кеш текста."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE games
                SET info_message_id = ?,
                    last_info_text = COALESCE(?, last_info_text),
                    updated_at = CURRENT_TIMESTAMP
                WHERE poll_id = ?
                """,
                (info_message_id, last_info_text, poll_id),
            )
            conn.commit()
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при обновлении info_message_id для игры poll_id={poll_id}"
        )


def update_game_last_info_text(poll_id: str, text: str) -> None:
    """Обновляет последний отправленный текст промежуточного сообщения."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE games
                SET last_info_text = ?, updated_at = CURRENT_TIMESTAMP
                WHERE poll_id = ?
                """,
                (text, poll_id),
            )
            conn.commit()
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при обновлении last_info_text для игры poll_id={poll_id}"
        )


def close_game(
    poll_id: str,
    *,
    status: str = "closed",
    closed_at: str,
    final_message_id: int | None = None,
) -> None:
    """Закрывает игру в БД."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
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
            conn.commit()
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при закрытии игры poll_id={poll_id}")


def save_monthly_vote(game_poll_id: str, player_id: int, option_ids: list[int]) -> None:
    """Сохраняет выбор пользователя в месячном голосовании."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_poll_votes (game_poll_id, player_id, option_ids_json, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(game_poll_id, player_id) DO UPDATE SET
                    option_ids_json = excluded.option_ids_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (game_poll_id, player_id, json.dumps(option_ids)),
            )
            conn.commit()
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при сохранении monthly vote game_poll_id={game_poll_id}, player_id={player_id}"
        )


def load_monthly_votes(game_poll_id: str) -> dict[int, list[int]]:
    """Возвращает сохранённые голоса месячного опроса."""
    try:
        init_db()
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT player_id, option_ids_json
                FROM monthly_poll_votes
                WHERE game_poll_id = ?
                """,
                (game_poll_id,),
            ).fetchall()
        result: dict[int, list[int]] = {}
        for player_id, option_ids_json in rows:
            try:
                loaded = json.loads(option_ids_json)
            except (TypeError, ValueError):
                loaded = []
            result[int(player_id)] = [
                int(value) for value in loaded if isinstance(value, int)
            ]
        return result
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при загрузке monthly votes game_poll_id={game_poll_id}"
        )
        return {}


def get_game(poll_id: str) -> dict[str, Any] | None:
    """Возвращает игру по poll_id."""
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM games WHERE poll_id = ?",
                (poll_id,),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении игры poll_id={poll_id}")
        return None


def get_open_games() -> list[dict[str, Any]]:
    """Возвращает все открытые игры."""
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM games WHERE status = 'open' ORDER BY opened_at"
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        logging.exception("❌ Ошибка при получении списка открытых игр")
        return []


def get_open_game_by_template_id(poll_template_id: int) -> dict[str, Any] | None:
    """Возвращает открытую regular-игру по шаблону."""
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
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
        return dict(row) if row else None
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при получении открытой игры для шаблона {poll_template_id}"
        )
        return None


def get_open_monthly_game() -> dict[str, Any] | None:
    """Возвращает открытый месячный опрос."""
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT *
                FROM games
                WHERE status = 'open' AND kind = 'monthly_subscription'
                ORDER BY opened_at DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        logging.exception("❌ Ошибка при получении открытого месячного опроса")
        return None


def save_game_participants(
    game_poll_id: str, participants: list[dict[str, Any]]
) -> None:
    """Сохраняет состав и финансовый итог игры."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                "DELETE FROM game_participants WHERE game_poll_id = ?", (game_poll_id,)
            )
            for participant in participants:
                conn.execute(
                    """
                    INSERT INTO game_participants (
                        game_poll_id, player_id, roster_bucket, sort_order,
                        is_subscriber, charged_amount, charge_source,
                        balance_before, balance_after
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        game_poll_id,
                        participant["player_id"],
                        participant["roster_bucket"],
                        participant["sort_order"],
                        1 if participant.get("is_subscriber") else 0,
                        int(participant.get("charged_amount", 0) or 0),
                        participant.get("charge_source", "none"),
                        participant.get("balance_before"),
                        participant.get("balance_after"),
                    ),
                )
            conn.commit()
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при сохранении участников игры game_poll_id={game_poll_id}"
        )


def _month_bounds(month: str | None) -> tuple[str | None, str | None]:
    if month is None:
        return None, None
    year_str, month_str = month.split("-")
    year = int(year_str)
    month_num = int(month_str)
    start = f"{year:04d}-{month_num:02d}-01"
    if month_num == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month_num + 1:02d}-01"
    return start, end


def get_stats_summary(month: str | None = None) -> dict[str, Any]:
    """Сводная статистика по regular-играм."""
    start, end = _month_bounds(month)
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            filter_sql = ""
            params: list[Any] = []
            if start and end:
                filter_sql = "AND g.closed_at >= ? AND g.closed_at < ?"
                params.extend([start, end])

            summary_row = conn.execute(
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

            roster_rows = conn.execute(
                f"""
                SELECT g.poll_id, COUNT(gp.player_id) AS interested
                FROM games g
                LEFT JOIN game_participants gp ON gp.game_poll_id = g.poll_id
                WHERE g.kind = 'regular' AND g.status = 'closed' {filter_sql}
                GROUP BY g.poll_id
                """,
                params,
            ).fetchall()
            main_rows = conn.execute(
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
            monthly_row = conn.execute(
                f"""
                SELECT COUNT(*) AS monthly_polls
                FROM games
                WHERE kind = 'monthly_subscription' {monthly_filter}
                """,
                monthly_params,
            ).fetchone()
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

            transactions_filter = ""
            tx_params: list[Any] = []
            hall_filter = ""
            hall_params: list[Any] = []
            if start and end:
                transactions_filter = "WHERE created_at >= ? AND created_at < ?"
                tx_params = [start, end]
                hall_filter = "WHERE paid_at >= ? AND paid_at < ?"
                hall_params = [start, end]

            payments_row = conn.execute(
                f"SELECT COALESCE(SUM(amount), 0) AS topups_sum FROM transactions {transactions_filter} AND amount > 0"
                if transactions_filter
                else "SELECT COALESCE(SUM(amount), 0) AS topups_sum FROM transactions WHERE amount > 0",
                tx_params,
            ).fetchone()
            hall_row = conn.execute(
                f"SELECT COALESCE(SUM(amount), 0) AS hall_payments_sum FROM hall_payments {hall_filter}"
                if hall_filter
                else "SELECT COALESCE(SUM(amount), 0) AS hall_payments_sum FROM hall_payments",
                hall_params,
            ).fetchone()

        return {
            "games_count": int(summary_row["games_count"] or 0) if summary_row else 0,
            "unique_players": int(summary_row["unique_players"] or 0)
            if summary_row
            else 0,
            "avg_main": avg_main,
            "avg_interest": avg_interest,
            "subscription_uses": int(summary_row["subscription_uses"] or 0)
            if summary_row
            else 0,
            "single_game_charges": int(summary_row["single_game_charges"] or 0)
            if summary_row
            else 0,
            "single_game_sum": int(summary_row["single_game_sum"] or 0)
            if summary_row
            else 0,
            "topups_sum": int(payments_row["topups_sum"] or 0) if payments_row else 0,
            "hall_payments_sum": int(hall_row["hall_payments_sum"] or 0)
            if hall_row
            else 0,
            "fund_balance": get_fund_balance(),
            "monthly_polls": int(monthly_row["monthly_polls"] or 0)
            if monthly_row
            else 0,
        }
    except sqlite3.Error:
        logging.exception("❌ Ошибка при получении сводной статистики")
        return {
            "games_count": 0,
            "unique_players": 0,
            "avg_main": 0.0,
            "avg_interest": 0.0,
            "subscription_uses": 0,
            "single_game_charges": 0,
            "single_game_sum": 0,
            "topups_sum": 0,
            "hall_payments_sum": 0,
            "fund_balance": get_fund_balance(),
            "monthly_polls": 0,
        }


def get_poll_stats(poll_template_id: int, month: str | None = None) -> dict[str, Any]:
    """Статистика по одному залу."""
    start, end = _month_bounds(month)
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            filter_sql = ""
            params: list[Any] = [poll_template_id]
            if start and end:
                filter_sql = "AND g.closed_at >= ? AND g.closed_at < ?"
                params.extend([start, end])
            games = conn.execute(
                f"""
                SELECT g.poll_id, g.poll_name_snapshot, g.closed_at
                FROM games g
                WHERE g.kind = 'regular' AND g.status = 'closed'
                  AND g.poll_template_id = ? {filter_sql}
                ORDER BY g.closed_at DESC
                """,
                params,
            ).fetchall()
            stats_rows = conn.execute(
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
            avg_interest_rows = conn.execute(
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
            main_rows = conn.execute(
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
        return {
            "games_count": int(stats_rows["games_count"] or 0) if stats_rows else 0,
            "unique_players": int(stats_rows["unique_players"] or 0)
            if stats_rows
            else 0,
            "avg_main": (
                sum(int(row["main_count"]) for row in main_rows) / len(main_rows)
                if main_rows
                else 0.0
            ),
            "avg_interest": (
                sum(int(row["interested"]) for row in avg_interest_rows)
                / len(avg_interest_rows)
                if avg_interest_rows
                else 0.0
            ),
            "subscription_uses": int(stats_rows["subscription_uses"] or 0)
            if stats_rows
            else 0,
            "single_game_sum": int(stats_rows["single_game_sum"] or 0)
            if stats_rows
            else 0,
            "last_game": games[0]["closed_at"] if games else None,
            "poll_name_snapshot": games[0]["poll_name_snapshot"] if games else "",
        }
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при получении статистики по шаблону {poll_template_id}"
        )
        return {
            "games_count": 0,
            "unique_players": 0,
            "avg_main": 0.0,
            "avg_interest": 0.0,
            "subscription_uses": 0,
            "single_game_sum": 0,
            "last_game": None,
            "poll_name_snapshot": "",
        }


def get_player_stats(player_id: int, month: str | None = None) -> dict[str, Any]:
    """Статистика по игроку."""
    start, end = _month_bounds(month)
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            filter_sql = ""
            params: list[Any] = [player_id]
            if start and end:
                filter_sql = "AND g.closed_at >= ? AND g.closed_at < ?"
                params.extend([start, end])
            row = conn.execute(
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
        balance = get_player_balance(player_id)
        return {
            "games_total": int(row["games_total"] or 0) if row else 0,
            "main_count": int(row["main_count"] or 0) if row else 0,
            "reserve_count": int(row["reserve_count"] or 0) if row else 0,
            "booked_count": int(row["booked_count"] or 0) if row else 0,
            "subscription_games": int(row["subscription_games"] or 0) if row else 0,
            "single_game_count": int(row["single_game_count"] or 0) if row else 0,
            "single_game_sum": int(row["single_game_sum"] or 0) if row else 0,
            "balance": int(balance["balance"]) if balance else 0,
        }
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении статистики по игроку {player_id}")
        balance = get_player_balance(player_id)
        return {
            "games_total": 0,
            "main_count": 0,
            "reserve_count": 0,
            "booked_count": 0,
            "subscription_games": 0,
            "single_game_count": 0,
            "single_game_sum": 0,
            "balance": int(balance["balance"]) if balance else 0,
        }
