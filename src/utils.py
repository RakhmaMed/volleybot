"""Вспомогательные функции."""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import logging
import os
import time
import traceback
from collections import defaultdict
from datetime import timezone
from pathlib import Path
from typing import Any

from aiogram.types import User

# Глобальный кэш списка игроков
PLAYERS: list[dict[str, Any]] = []

# Rate limiting: хранение времени последних запросов
# Структура: {user_id: [timestamp1, timestamp2, ...]}
_RATE_LIMIT_CACHE: dict[int, list[float]] = defaultdict(list)

# Настройки rate limiting
RATE_LIMIT_WINDOW = 60  # Окно в секундах
RATE_LIMIT_MAX_REQUESTS = 10  # Максимум запросов в окне


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
            "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
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


def load_players() -> None:
    """
    Загружает список игроков из файла players.json при старте приложения.
    Результат кэшируется в глобальной переменной PLAYERS.
    """
    global PLAYERS

    # Определяем путь к файлу заранее
    script_dir: Path = Path(__file__).parent.parent
    players_file: Path = script_dir / "players.json"

    logging.debug("Загрузка списка игроков из players.json...")
    try:
        if not players_file.exists():
            logging.warning(
                f"⚠️ Файл players.json не найден по пути {players_file}. "
                f"Список игроков будет пустым. Будут использоваться имена из Telegram."
            )
            PLAYERS = []
            return

        with open(players_file, "r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)

        PLAYERS = data
        logging.info(f"✅ Загружено {len(PLAYERS)} игроков из {players_file}")
        # Логируем детали на уровне DEBUG
        ball_donors = sum(1 for p in PLAYERS if p.get("ball_donate") is True)
        if ball_donors > 0:
            logging.debug(f"  Донатов мячей: {ball_donors}")
    except OSError:
        logging.exception(
            f"❌ Ошибка ввода-вывода при загрузке {players_file}. "
            f"Проверьте существование и права доступа к файлу. Список игроков будет пустым."
        )
        PLAYERS = []
    except json.JSONDecodeError:
        logging.exception(
            f"❌ Ошибка парсинга JSON в файле {players_file}. "
            f"Проверьте синтаксис файла. Список игроков будет пустым."
        )
        PLAYERS = []
    except (KeyError, TypeError):
        logging.exception(
            f"❌ Неверная структура данных в файле {players_file}. "
            f"Проверьте формат данных. Список игроков будет пустым."
        )
        PLAYERS = []


def get_player_name(user: User, subs: list[int] | None = None) -> str:
    """
    Получает имя игрока по ID из players.json, используя fullname если он есть.
    Если fullname пустой или не найден, возвращает имя из Telegram.
    Возвращает текст с упоминанием @username для открытия профиля (не чата).

    Args:
        user: Объект пользователя Telegram

    Returns:
        Текст с именем игрока и упоминанием @username (кликабельно, открывает профиль)
    """
    # Получаем имя из Telegram как fallback
    telegram_name: str = (
        f"@{user.username}" if user.username else (user.full_name or "Неизвестный")
    )
    display_name: str = telegram_name
    emojis: str = ""

    # Если список игроков не загружен, используем имя из Telegram
    if not PLAYERS:
        logging.debug(
            f"Список игроков пуст, используем имя из Telegram для пользователя {user.id}"
        )
    else:
        # Ищем игрока по ID в заранее загруженном списке
        for player in PLAYERS:
            if player.get("id") == user.id:
                fullname: str | None = player.get("fullname")
                # Если fullname есть и не пустой, используем его
                if fullname and fullname.strip():
                    display_name = fullname

                # Проверяем на донат мячей
                if player.get("ball_donate") is True:
                    emojis += "🏐"
                break

    # Проверяем подписку (если передан список подписчиков)
    if subs and user.id in subs:
        emojis = "⭐️" + emojis

    # Добавляем пробел после эмодзи, если они есть
    if emojis:
        display_name = f"{emojis} {display_name}"

    # Для открытия профиля (а не чата) используем упоминание @username в тексте
    # Telegram автоматически делает такие упоминания кликабельными и они открывают профиль
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
