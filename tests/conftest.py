"""Конфигурация pytest и общие фикстуры."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Poll, PollAnswer, User

from src.services import AdminService


@pytest.fixture(autouse=True)
def temp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Изолирует SQLite базу для каждого теста."""
    db_path = tmp_path / "volleybot.db"
    monkeypatch.setenv("VOLLEYBOT_DB_PATH", str(db_path))


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
