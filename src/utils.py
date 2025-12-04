"""Вспомогательные функции."""

import datetime
from datetime import timezone
import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any

from aiogram.types import User

from .config import ADMIN_USERNAME


# Глобальный кэш списка игроков
PLAYERS: list[dict[str, Any]] = []


def save_error_dump(error: Exception, poll_name: str, question: str, chat_id: int) -> None:
    """
    Сохраняет дамп ошибки в файл рядом с исходником.
    
    Args:
        error: Исключение, которое произошло
        poll_name: Название опроса
        question: Текст вопроса опроса
        chat_id: ID чата
    """
    try:
        error_data: dict[str, Any] = {
            'timestamp': datetime.datetime.now(timezone.utc).isoformat(),
            'poll_name': poll_name,
            'question': question,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
            'chat_id': chat_id
        }
        
        # Сохраняем error_dump.json в корне проекта
        script_dir: Path = Path(__file__).parent.parent
        error_file: str = str(script_dir / 'error_dump.json')
        
        existing_errors: list[dict[str, Any]] = []
        if os.path.exists(error_file):
            try:
                with open(error_file, 'r', encoding='utf-8') as f:
                    existing_errors = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_errors = []
        
        existing_errors.append(error_data)
        
        with open(error_file, 'w', encoding='utf-8') as f:
            json.dump(existing_errors[-50:], f, ensure_ascii=False, indent=2)
        
        logging.info(f"Дамп ошибки сохранен в {error_file}")
    except Exception as e:
        logging.error(f"Не удалось сохранить дамп ошибки: {e}")


def escape_html(text: str) -> str:
    """
    Экранирует специальные HTML-символы в тексте для безопасной
    отправки сообщений с parse_mode='HTML' в Telegram.
    
    Args:
        text: Исходный текст
    
    Returns:
        Текст с экранированными символами &, < и >
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def is_admin(user: User) -> bool:
    """
    Проверяет, является ли пользователь администратором.
    
    Args:
        user: Объект пользователя Telegram
        
    Returns:
        True если пользователь является администратором, иначе False
    """
    username: str | None = user.username
    if not username:
        return False
    admin_username_clean: str = ADMIN_USERNAME.replace("@", "")
    username_clean: str = username.replace("@", "")
    return username_clean == admin_username_clean


def load_players() -> None:
    """
    Загружает список игроков из файла players.json при старте приложения.
    Результат кэшируется в глобальной переменной PLAYERS.
    """
    global PLAYERS

    try:
        script_dir: Path = Path(__file__).parent.parent
        players_file: Path = script_dir / "players.json"

        if not players_file.exists():
            logging.warning("Файл players.json не найден. Список игроков будет пустым.")
            PLAYERS = []
            return

        with open(players_file, "r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)

        PLAYERS = data
        logging.info(f"Загружено {len(PLAYERS)} игроков из players.json")
    except Exception as e:
        logging.error(f"Ошибка при загрузке players.json: {e}")
        PLAYERS = []


def get_player_name(user: User) -> str:
    """
    Получает имя игрока по ID из players.json, используя fullname если он есть.
    Если fullname пустой или не найден, возвращает имя из Telegram.
    
    Args:
        user: Объект пользователя Telegram
        
    Returns:
        Имя игрока (fullname из players.json или имя из Telegram)
    """
    # Получаем имя из Telegram как fallback
    telegram_name: str = f"@{user.username}" if user.username else (user.full_name or "Неизвестный")

    # Если список игроков не загружен, используем имя из Telegram
    if not PLAYERS:
        logging.warning("Список игроков пуст или не загружен, используем имя из Telegram")
        return telegram_name

    # Ищем игрока по ID в заранее загруженном списке
    for player in PLAYERS:
        if player.get("id") == user.id:
            fullname: str | None = player.get("fullname")
            # Если fullname есть и не пустой, используем его
            if fullname and fullname.strip():
                return fullname
            # Иначе используем имя из Telegram
            return telegram_name

    # Игрок не найден в списке
    logging.debug(f"Игрок с ID {user.id} не найден в players.json, используем имя из Telegram: {telegram_name}")
    return telegram_name
