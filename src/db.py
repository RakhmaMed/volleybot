"""Утилиты для хранения состояния бота в SQLite."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Ключи хранения в kv_store
BOT_STATE_KEY = "bot_state"
POLL_STATE_KEY = "poll_state"


def _get_db_path() -> str:
    """Возвращает путь к базе данных с учётом переменной окружения."""
    override: str | None = os.getenv("VOLLEYBOT_DB_PATH")
    if override:
        return override
    return str(Path(__file__).parent.parent / "volleybot.db")


def init_db() -> None:
    """Создаёт файл базы и таблицу kv_store при необходимости."""
    db_path: str = _get_db_path()
    logging.debug(f"Инициализация базы данных: {db_path}")

    # Для in-memory соединения каталоги не нужны
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        # Таблица для хранения ключ-значение (состояние бота)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Таблица для хранения данных игроков и их баланса
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
        conn.commit()
    logging.debug(f"✅ База данных инициализирована: {db_path}")


@contextmanager
def _connect() -> Any:
    """
    Контекстный менеджер для подключения к БД.
    Явно закрывает соединение, чтобы избежать ResourceWarning.
    """
    conn = sqlite3.connect(_get_db_path())
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


def delete_state(key: str) -> None:
    """Удаляет значение по ключу (без ошибок, если ключа нет)."""
    try:
        init_db()
        logging.debug(f"Удаление состояния для ключа: '{key}'")
        with _connect() as conn:
            cursor = conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            conn.commit()
            if cursor.rowcount > 0:
                logging.debug(f"✅ Состояние '{key}' успешно удалено")
            else:
                logging.debug(f"Состояние '{key}' не найдено в БД (ничего не удалено)")
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка SQLite при удалении состояния '{key}'. БД: {_get_db_path()}"
        )
    except OSError:
        logging.exception(
            f"❌ Ошибка ввода-вывода при удалении состояния '{key}'. БД: {_get_db_path()}"
        )


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


def set_player_balance(user_id: int, balance: int) -> bool:
    """Устанавливает точное значение баланса игрока."""
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "UPDATE players SET balance = ? WHERE id = ?",
                (balance, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при установке баланса игрока {user_id}")
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
    """Гарантирует наличие игрока в базе данных (создаёт или обновляет имена)."""
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO players (id, name, fullname)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = COALESCE(excluded.name, players.name),
                    fullname = COALESCE(excluded.fullname, players.fullname)
                """,
                (user_id, name, fullname),
            )
            conn.commit()
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при регистрации/обновлении игрока {user_id}")
