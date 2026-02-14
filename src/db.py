"""Утилиты для хранения состояния бота в SQLite."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .types import PollTemplate

# Ключи хранения в kv_store
BOT_STATE_KEY = "bot_state"
POLL_STATE_KEY = "poll_state"
FUND_BALANCE_KEY = "fund_balance"


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

        # Таблица для отслеживания оплаты залов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hall_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_name TEXT NOT NULL,
                month TEXT NOT NULL,
                amount INTEGER NOT NULL,
                paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (poll_name) REFERENCES poll_templates(name) ON DELETE CASCADE,
                UNIQUE(poll_name, month)
            )
            """
        )

        # Миграция: добавление monthly_cost если его ещё нет
        cursor = conn.execute("PRAGMA table_info(poll_templates)")
        columns = [row[1] for row in cursor.fetchall()]
        if "monthly_cost" not in columns:
            conn.execute(
                "ALTER TABLE poll_templates ADD COLUMN monthly_cost INTEGER DEFAULT 0"
            )
            logging.info("✅ Миграция: добавлен столбец monthly_cost в poll_templates")

        conn.commit()
    logging.debug(f"✅ База данных инициализирована: {db_path}")


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


def clear_paid_poll_subscriptions() -> None:
    """Очищает подписки для всех платных опросов (cost > 0)."""
    try:
        init_db()
        with _connect() as conn:
            conn.execute(
                """
                DELETE FROM poll_subscriptions
                WHERE poll_name IN (
                    SELECT name FROM poll_templates WHERE cost > 0
                )
                """
            )
            conn.commit()
        logging.info("✅ Подписки для платных опросов очищены")
    except sqlite3.Error:
        logging.exception("❌ Ошибка при очистке подписок для платных опросов")


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
    try:
        init_db()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT pt.*
                FROM poll_templates pt
                WHERE pt.monthly_cost > 0
                  AND pt.name NOT IN (
                      SELECT hp.poll_name FROM hall_payments hp WHERE hp.month = ?
                  )
                """,
                (month,),
            )
            templates: list[PollTemplate] = []
            for row in cursor.fetchall():
                template = dict(row)
                sub_cursor = conn.execute(
                    "SELECT user_id FROM poll_subscriptions WHERE poll_name = ?",
                    (template["name"],),
                )
                template["subs"] = [r[0] for r in sub_cursor.fetchall()]
                templates.append(template)
            return templates
    except sqlite3.Error:
        logging.exception(f"❌ Ошибка при получении неоплаченных залов за {month}")
        return []


def record_hall_payment(poll_name: str, month: str, amount: int) -> bool:
    """
    Записывает оплату зала за месяц.

    Args:
        poll_name: Название опроса (зала)
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
                INSERT INTO hall_payments (poll_name, month, amount)
                VALUES (?, ?, ?)
                """,
                (poll_name, month, amount),
            )
            conn.commit()
        logging.info(
            f"✅ Оплата зала записана: {poll_name}, месяц={month}, сумма={amount}"
        )
        return True
    except sqlite3.IntegrityError:
        logging.warning(
            f"⚠️ Зал '{poll_name}' за {month} уже оплачен (дубликат)"
        )
        return False
    except sqlite3.Error:
        logging.exception(
            f"❌ Ошибка при записи оплаты зала '{poll_name}' за {month}"
        )
        return False
