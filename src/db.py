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

    # Для in-memory соединения каталоги не нужны
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


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
    except Exception as exc:  # noqa: BLE001
        logging.error("Не удалось сохранить состояние %s: %s", key, exc)


def load_state(key: str, default: Any = None) -> Any:
    """Загружает состояние по ключу, возвращает default при ошибке/отсутствии."""
    try:
        init_db()
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        return json.loads(row[0])
    except Exception as exc:  # noqa: BLE001
        logging.error("Не удалось загрузить состояние %s: %s", key, exc)
        return default


def delete_state(key: str) -> None:
    """Удаляет значение по ключу (без ошибок, если ключа нет)."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logging.error("Не удалось удалить состояние %s: %s", key, exc)
