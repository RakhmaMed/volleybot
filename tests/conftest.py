"""Конфигурация pytest и общие фикстуры."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiogram import Bot
from aiogram.types import User

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
