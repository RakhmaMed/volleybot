"""Конфигурация pytest и общие фикстуры."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Poll, PollAnswer, User

from config import ADMIN_USERNAME


@pytest.fixture
def temp_config_file(tmp_path: Path) -> Path:
    """Создаёт временный файл конфигурации для тестов."""
    config_data: dict[str, Any] = {
        "telegram_token": "test_token",
        "chat_id": "-1001234567890",
        "admin_username": "test_admin",
        "webhook_path": "/test_webhook",
        "webhook_port": 8443,
        "ssl_cert_path": "/test/cert.pem",
        "ssl_key_path": "/test/key.pem",
        "polls": [
            {
                "name": "test_poll",
                "message": "Test poll message",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "close_day": "tue",
                "close_hour_utc": 10,
                "close_minute_utc": 0
            }
        ]
    }
    config_file = tmp_path / "config.json"
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f)
    return config_file


@pytest.fixture
def mock_bot() -> Bot:
    """Создаёт мок-объект бота."""
    bot = MagicMock(spec=Bot)
    bot.token = "test_token"
    return bot


@pytest.fixture
def admin_user() -> User:
    """Создаёт пользователя-администратора."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test",
        last_name="Admin",
        username=ADMIN_USERNAME.replace("@", ""),
        language_code="ru"
    )


@pytest.fixture
def regular_user() -> User:
    """Создаёт обычного пользователя."""
    return User(
        id=987654321,
        is_bot=False,
        first_name="Regular",
        last_name="User",
        username="regular_user",
        language_code="ru"
    )


@pytest.fixture
def mock_message(admin_user: User) -> Message:
    """Создаёт мок-объект сообщения."""
    message = MagicMock(spec=Message)
    message.from_user = admin_user
    message.chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
    message.message_id = 1
    message.reply = MagicMock()
    return message


@pytest.fixture
def mock_poll() -> Poll:
    """Создаёт мок-объект опроса."""
    poll = MagicMock(spec=Poll)
    poll.id = "test_poll_id"
    poll.question = "Test poll question"
    poll.options = [MagicMock(text="Да"), MagicMock(text="Нет")]
    poll.is_anonymous = False
    return poll


@pytest.fixture
def mock_poll_answer(admin_user: User) -> PollAnswer:
    """Создаёт мок-объект ответа на опрос."""
    answer = MagicMock(spec=PollAnswer)
    answer.poll_id = "test_poll_id"
    answer.user = admin_user
    answer.option_ids = [0]  # Выбран "Да"
    return answer

