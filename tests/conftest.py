"""Конфигурация pytest и общие фикстуры."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, ChatMemberOwner, Message, Poll, PollAnswer, User

from src.services import AdminService


@pytest.fixture(autouse=True)
def temp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Изолирует SQLite базу для каждого теста."""
    db_path = tmp_path / "volleybot.db"
    monkeypatch.setenv("VOLLEYBOT_DB_PATH", str(db_path))


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
                "game_day": "tue",
                "game_hour_utc": 10,
                "game_minute_utc": 0,
            }
        ],
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
        username="test_admin",
        language_code="ru",
    )


@pytest.fixture
def admin_service(admin_user: User) -> AdminService:
    """Создаёт сервис администраторов с предзаполненным кэшем."""
    service = AdminService(default_chat_id=-1001234567890)
    # Предзаполняем кэш администраторами для тестов
    service._admin_cache[-1001234567890] = {admin_user.id}
    service._cache_updated_at[-1001234567890] = float("inf")  # Никогда не истекает
    return service


@pytest.fixture
def regular_user() -> User:
    """Создаёт обычного пользователя."""
    return User(
        id=987654321,
        is_bot=False,
        first_name="Regular",
        last_name="User",
        username="regular_user",
        language_code="ru",
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
