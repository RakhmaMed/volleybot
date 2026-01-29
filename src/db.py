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
    return str(Path(__file__).parent.parent / "data" / "volleybot.db")


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

        # Таблица для шаблонов опросов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_templates (
                name TEXT PRIMARY KEY,
                place TEXT,
                message TEXT NOT NULL,
                open_day TEXT,
                open_hour_utc INTEGER,
                open_minute_utc INTEGER,
                game_day TEXT,
                game_hour_utc INTEGER,
                game_minute_utc INTEGER,
                cost INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Таблица для подписок на опросы
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS poll_subscriptions (
                poll_name TEXT,
                user_id INTEGER,
                PRIMARY KEY (poll_name, user_id),
                FOREIGN KEY (poll_name) REFERENCES poll_templates(name) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES players(id) ON DELETE CASCADE
            )
            """
        )

        # Таблица для истории транзакций
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                description TEXT,
                poll_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
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


def get_poll_templates() -> list[dict[str, Any]]:
    """Возвращает все шаблоны опросов из БД."""
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM poll_templates")
            templates = []
            for row in cursor.fetchall():
                template = dict(row)
                # Получаем подписчиков для этого опроса
                sub_cursor = conn.execute(
                    "SELECT user_id FROM poll_subscriptions WHERE poll_name = ?",
                    (template["name"],),
                )
                template["subs"] = [r[0] for r in sub_cursor.fetchall()]
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
                    game_day, game_hour_utc, game_minute_utc, cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )

            # Обновляем подписчиков если они переданы
            if "subs" in template:
                conn.execute(
                    "DELETE FROM poll_subscriptions WHERE poll_name = ?",
                    (template["name"],),
                )
                for user_id in template["subs"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO poll_subscriptions (poll_name, user_id) VALUES (?, ?)",
                        (template["name"], user_id),
                    )
            conn.commit()
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при сохранении шаблона опроса '{template.get('name')}'"
        )


def get_poll_subs(poll_name: str) -> list[int]:
    """Возвращает список ID подписчиков для конкретного опроса."""
    try:
        init_db()
        with _connect() as conn:
            cursor = conn.execute(
                "SELECT user_id FROM poll_subscriptions WHERE poll_name = ?",
                (poll_name,),
            )
            return [row[0] for row in cursor.fetchall()]
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при получении подписчиков для опроса '{poll_name}'"
        )
        return []


def add_transaction(
    player_id: int, amount: int, description: str, poll_name: str | None = None
) -> None:
    """
    Добавляет транзакцию в историю.

    Args:
        player_id: ID игрока
        amount: Сумма транзакции (отрицательная для списания)
        description: Описание транзакции
        poll_name: Название опроса (необязательно)
    """
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO transactions (player_id, amount, description, poll_name)
                VALUES (?, ?, ?, ?)
                """,
                (player_id, amount, description, poll_name),
            )
            conn.commit()
        logging.debug(
            f"✅ Транзакция добавлена: player_id={player_id}, amount={amount}, poll_name={poll_name}"
        )
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при добавлении транзакции для игрока {player_id}")


def get_player_transactions(player_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """
    Возвращает историю транзакций игрока.

    Args:
        player_id: ID игрока
        limit: Максимальное количество транзакций

    Returns:
        Список словарей с данными транзакций
    """
    try:
        init_db()
        with _connect() as conn:
            cursor = conn.execute(
                """
                SELECT id, amount, description, poll_name, created_at
                FROM transactions
                WHERE player_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (player_id, limit),
            )
            return [
                {
                    "id": row[0],
                    "amount": row[1],
                    "description": row[2],
                    "poll_name": row[3],
                    "created_at": row[4],
                }
                for row in cursor.fetchall()
            ]
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении транзакций для игрока {player_id}")
        return []


def get_transactions_by_poll(poll_name: str) -> list[dict[str, Any]]:
    """
    Возвращает все транзакции для конкретного опроса.

    Args:
        poll_name: Название опроса

    Returns:
        Список словарей с данными транзакций
    """
    try:
        init_db()
        with _connect() as conn:
            cursor = conn.execute(
                """
                SELECT t.id, t.player_id, p.name, p.fullname, t.amount, t.description, t.created_at
                FROM transactions t
                LEFT JOIN players p ON t.player_id = p.id
                WHERE t.poll_name = ?
                ORDER BY t.created_at DESC
                """,
                (poll_name,),
            )
            return [
                {
                    "id": row[0],
                    "player_id": row[1],
                    "player_name": row[3] if row[3] else row[2],  # fullname или name
                    "amount": row[4],
                    "description": row[5],
                    "created_at": row[6],
                }
                for row in cursor.fetchall()
            ]
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при получении транзакций для опроса '{poll_name}'"
        )
        return []
