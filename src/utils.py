"""Вспомогательные функции."""

from __future__ import annotations

import asyncio
import calendar
import functools
import hashlib
import ipaddress
import json
import logging
import os
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram.types import User

# Rate limiting: хранение времени последних запросов
# Структура: {user_id: [timestamp1, timestamp2, ...]}
_RATE_LIMIT_CACHE: dict[int, list[float]] = defaultdict(list)

# Настройки rate limiting
RATE_LIMIT_WINDOW = 60  # Окно в секундах
RATE_LIMIT_MAX_REQUESTS = 20  # Максимум запросов в окне
DEFAULT_GAMES_PER_MONTH = 4
WEEKDAY_TO_INDEX: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def count_games_in_month(
    game_day: str,
    month: str,
    default_games: int = DEFAULT_GAMES_PER_MONTH,
) -> int:
    """
    Возвращает количество игровых дней в месяце для указанного дня недели.

    Args:
        game_day: День недели в формате mon..sun, '*' или произвольная строка.
        month: Месяц в формате YYYY-MM.
        default_games: Значение по умолчанию для '*' и некорректных данных.
    """
    day_key = (game_day or "").strip().lower()
    weekday_idx = WEEKDAY_TO_INDEX.get(day_key)
    if weekday_idx is None:
        return default_games

    try:
        year_str, month_str = month.split("-", 1)
        year = int(year_str)
        month_num = int(month_str)
        if month_num < 1 or month_num > 12:
            return default_games
    except (ValueError, AttributeError):
        return default_games

    month_matrix = calendar.monthcalendar(year, month_num)
    count = sum(1 for week in month_matrix if week[weekday_idx] != 0)
    return count if count > 0 else default_games


def get_next_month_str(now: datetime | None = None) -> str:
    """Возвращает следующий месяц в формате YYYY-MM."""
    current = now or datetime.now()
    year = current.year + (1 if current.month == 12 else 0)
    month = 1 if current.month == 12 else current.month + 1
    return f"{year:04d}-{month:02d}"


def retry_async(
    exceptions: type[Exception] | tuple[type[Exception], ...],
    tries: int | None = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    logger: logging.Logger | None = None,
):
    """
    Декоратор для повторных попыток выполнения асинхронной функции.

    Args:
        exceptions: Исключение или кортеж исключений для отлова
        tries: Максимальное количество попыток (0 или None для бесконечности)
        delay: Начальная задержка между попытками (сек)
        backoff: Множитель задержки после каждой попытки
        max_delay: Максимальная задержка между попытками (сек)
        logger: Логгер для записи предупреждений о попытках
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            _tries, _delay = tries, delay
            attempt = 1
            while True:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    # Если количество попыток ограничено и мы его достигли
                    if _tries is not None and _tries > 0 and attempt >= _tries:
                        raise e

                    tries_left = f"{_tries - attempt}" if _tries and _tries > 0 else "∞"
                    msg = (
                        f"⚠️ Ошибка в {func.__name__}: {type(e).__name__}: {e}. "
                        f"Повтор через {_delay}с... (осталось попыток: {tries_left})"
                    )
                    if logger:
                        logger.warning(msg)
                    else:
                        logging.warning(msg)

                    await asyncio.sleep(_delay)
                    attempt += 1
                    _delay = min(_delay * backoff, max_delay)

        return wrapper

    return decorator


def save_error_dump(
    error: Exception, poll_name: str, question: str, chat_id: int
) -> None:
    """
    Сохраняет дамп ошибки в файл рядом с исходником.

    Args:
        error: Исключение, которое произошло
        poll_name: Название опроса
        question: Текст вопроса опроса
        chat_id: ID чата
    """
    # Определяем путь к файлу заранее
    script_dir: Path = Path(__file__).parent.parent
    error_file: str = str(script_dir / "error_dump.json")

    logging.debug(f"Сохранение дампа ошибки для опроса '{poll_name}' в чате {chat_id}")
    try:
        error_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "poll_name": poll_name,
            "question": question,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "chat_id": chat_id,
        }

        existing_errors: list[dict[str, Any]] = []
        if os.path.exists(error_file):
            try:
                with open(error_file, "r", encoding="utf-8") as f:
                    existing_errors = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_errors = []

        existing_errors.append(error_data)

        with open(error_file, "w", encoding="utf-8") as f:
            json.dump(existing_errors[-50:], f, ensure_ascii=False, indent=2)

        logging.info(
            f"✅ Дамп ошибки сохранен в {error_file} "
            f"(всего ошибок в файле: {len(existing_errors[-50:])})"
        )
    except json.JSONDecodeError:
        logging.exception(
            "❌ Ошибка кодирования JSON при сохранении дампа ошибки. "
            "Проверьте данные ошибки на сериализуемость."
        )
    except OSError:
        logging.exception(
            f"❌ Ошибка ввода-вывода при сохранении дампа ошибки в {error_file}. "
            f"Проверьте права доступа к файлу."
        )


def escape_html(text: str) -> str:
    """
    Экранирует специальные HTML-символы в тексте для безопасной
    отправки сообщений с parse_mode='HTML' в Telegram.

    Args:
        text: Исходный текст

    Returns:
        Текст с экранированными символами &, < и >
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_rate_limited(user_id: int) -> bool:
    """
    Проверяет, превышен ли лимит запросов для пользователя.

    Args:
        user_id: ID пользователя Telegram

    Returns:
        True если лимит превышен, иначе False
    """
    current_time = time.time()
    window_start = current_time - RATE_LIMIT_WINDOW

    # Очищаем старые записи
    _RATE_LIMIT_CACHE[user_id] = [
        t for t in _RATE_LIMIT_CACHE[user_id] if t > window_start
    ]

    # Проверяем лимит
    if len(_RATE_LIMIT_CACHE[user_id]) >= RATE_LIMIT_MAX_REQUESTS:
        return True

    # Добавляем текущий запрос
    _RATE_LIMIT_CACHE[user_id].append(current_time)
    return False


def rate_limit_check(user: User | None, is_admin: bool = False) -> str | None:
    """
    Проверяет rate limit для пользователя.

    Args:
        user: Объект пользователя Telegram или None
        is_admin: Флаг, является ли пользователь администратором

    Returns:
        Сообщение об ошибке если лимит превышен, иначе None
    """
    if user is None:
        return None

    # Администраторы не ограничены
    if is_admin:
        return None

    if is_rate_limited(user.id):
        logging.warning(
            f"⚠️ Rate limit превышен для пользователя @{user.username} (ID: {user.id})"
        )
        return "⚠️ Слишком много запросов. Подождите минуту."

    return None


def generate_webhook_secret_path(token: str) -> str:
    """
    Генерирует секретный путь для webhook на основе токена бота.

    Использует хеш токена для создания непредсказуемого пути,
    который невозможно угадать без знания токена.

    Args:
        token: Токен бота Telegram

    Returns:
        Секретный путь вида /webhook_<hash>
    """
    # Используем SHA256 хеш токена
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:32]
    return f"/webhook_{token_hash}"


# Диапазоны IP-адресов Telegram для webhook
# Источник: https://core.telegram.org/bots/webhooks#the-short-version
TELEGRAM_IP_RANGES = [
    ipaddress.ip_network("149.154.160.0/20"),
    ipaddress.ip_network("91.108.4.0/22"),
]


def is_telegram_ip(ip_str: str) -> bool:
    """
    Проверяет, принадлежит ли IP-адрес диапазонам Telegram.

    Args:
        ip_str: IP-адрес в строковом формате

    Returns:
        True если IP принадлежит Telegram, иначе False
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        for network in TELEGRAM_IP_RANGES:
            if ip in network:
                return True
        return False
    except ValueError:
        logging.warning(f"⚠️ Некорректный IP-адрес: {ip_str}")
        return False


def get_player_name(user: User, subs: list[int] | None = None) -> str:
    """
    Получает имя игрока по ID из базы данных, используя fullname если он есть.
    Если fullname пустой или не найден, возвращает имя из Telegram.
    Возвращает текст с упоминанием @username для открытия профиля (не чата).

    Args:
        user: Объект пользователя Telegram
        subs: Список ID пользователей с подпиской

    Returns:
        Текст с именем игрока и упоминанием @username (кликабельно)
    """
    from .db import get_player_info

    telegram_name: str = (
        f"@{user.username}" if user.username else (user.full_name or "Неизвестный")
    )
    display_name: str = telegram_name
    emojis: str = ""

    player = get_player_info(user.id)
    if player:
        fullname: str | None = player.get("fullname")
        if fullname and fullname.strip():
            display_name = fullname

        # Проверяем на донат мячей
        if player.get("ball_donate"):
            emojis += "🏐"

    # Проверяем подписку (если передан список подписчиков)
    if subs and user.id in subs:
        emojis = "⭐️" + emojis

    # Добавляем пробел после эмодзи, если они есть
    if emojis:
        display_name = f"{emojis} {display_name}"

    # Формируем упоминание с username
    if user.username:
        # Убираем @ если есть в username
        username_clean: str = user.username.replace("@", "")
        username_mention: str = f"@{username_clean}"

        # Если display_name уже является @username, не дублируем
        if display_name == username_mention:
            return escape_html(username_mention)

        # Формат: "ИМЯ (@username)" - упоминание будет кликабельным и откроет профиль
        escaped_name: str = escape_html(display_name)
        return f"{escaped_name} ({username_mention})"
    else:
        # Если нет username, просто возвращаем имя (без ссылки на профиль)
        return escape_html(display_name)


def format_player_link(
    player_data: dict[str, Any] | None, user_id: int | None = None
) -> str:
    """
    Форматирует имя игрока как HTML-гиперссылку на его профиль в Telegram.

    Приоритет отображения:
    1. Если есть username (name) - создаёт ссылку t.me/username с fullname или username как текст
    2. Если нет username - создаёт ссылку tg://user?id=ID с fullname или ID как текст

    Args:
        player_data: Словарь с данными игрока из БД (id, name, fullname)
        user_id: ID пользователя (используется если player_data is None)

    Returns:
        HTML-строка с гиперссылкой на профиль пользователя
    """
    if player_data is None:
        if user_id is None:
            return "Неизвестный"
        # Если нет данных о игроке, используем ссылку по ID
        return f'<a href="tg://user?id={user_id}">ID: {user_id}</a>'

    pid = player_data.get("id")
    username = player_data.get("name")
    fullname = player_data.get("fullname")

    # Определяем текст ссылки (что будет видно пользователю)
    if fullname and fullname.strip():
        display_text = escape_html(fullname)
    elif username:
        display_text = f"@{escape_html(username)}"
    else:
        display_text = f"ID: {pid}"

    # Определяем URL ссылки
    if username and username.strip():
        # Приоритет: ссылка через username (t.me/username)
        clean_username = username.strip().lstrip("@")
        link = f"https://t.me/{clean_username}"
    else:
        # Fallback: ссылка через ID (tg://user?id=...)
        link = f"tg://user?id={pid}"

    return f'<a href="{link}">{display_text}</a>'
