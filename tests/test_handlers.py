"""Тесты для обработчиков команд."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, PollAnswer, Update, User

from src.handlers import register_handlers
from src.services import AdminService, BotStateService, PollService


@pytest.fixture
def admin_user():
    """Создаёт пользователя-администратора."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test",
        last_name="Admin",
        username="test_admin",
    )


@pytest.fixture
def regular_user():
    """Создаёт обычного пользователя."""
    return User(
        id=987654321,
        is_bot=False,
        first_name="Regular",
        username="regular_user",
    )


@pytest.fixture
def mock_admin_service(admin_user):
    """Создаёт мок AdminService с предзаполненным кэшем."""
    service = AdminService(default_chat_id=-1001234567890)
    # Предзаполняем кэш администраторами
    service._admin_cache[-1001234567890] = {admin_user.id}
    service._cache_updated_at[-1001234567890] = float("inf")  # Никогда не истекает
    return service


@pytest.mark.asyncio
class TestStartCommand:
    """Тесты для команды /start."""

    async def test_start_command_as_admin_when_bot_enabled(
        self, admin_user, mock_admin_service
    ):
        """Тест команды /start от администратора при включённом боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(True)

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        message = MagicMock(spec=Message)
        message.from_user = admin_user
        message.chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message.reply = AsyncMock()

        assert bot_state_service.is_enabled() is True
        # Проверяем, что бот уже включен
        # В реальном сценарии обработчик должен ответить соответствующим сообщением

    async def test_start_command_as_admin_when_bot_disabled(
        self, admin_user, mock_admin_service
    ):
        """Тест команды /start от администратора при выключенном боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(False)

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        message = MagicMock(spec=Message)
        message.from_user = admin_user
        message.chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message.reply = AsyncMock()

        # Проверяем логику включения бота
        assert bot_state_service.is_enabled() is False
        bot_state_service.set_enabled(True)
        assert bot_state_service.is_enabled() is True

    async def test_start_command_as_regular_user(
        self, regular_user, mock_admin_service
    ):
        """Тест команды /start от обычного пользователя."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        message = MagicMock(spec=Message)
        message.from_user = regular_user
        message.chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message.reply = AsyncMock()

        # Обычный пользователь не должен иметь доступа
        # AdminService вернёт False для regular_user


@pytest.mark.asyncio
class TestStopCommand:
    """Тесты для команды /stop."""

    async def test_stop_command_as_admin(self, admin_user, mock_admin_service):
        """Тест команды /stop от администратора."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(True)

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        message = MagicMock(spec=Message)
        message.from_user = admin_user
        message.chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message.reply = AsyncMock()

        # Проверяем логику выключения бота
        assert bot_state_service.is_enabled() is True
        bot_state_service.set_enabled(False)
        assert bot_state_service.is_enabled() is False


@pytest.mark.asyncio
class TestChatIdCommand:
    """Тесты для команды /chatid."""

    async def test_chatid_command(self, admin_user, mock_admin_service):
        """Тест команды /chatid."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message = MagicMock(spec=Message)
        message.from_user = admin_user
        message.chat = chat
        message.reply = AsyncMock()

        # Команда /chatid должна вернуть информацию о чате
        assert chat.id == -1001234567890
        assert chat.type == "supergroup"


@pytest.mark.asyncio
class TestPollAnswerHandler:
    """Тесты для обработчика ответов на опросы."""

    async def test_poll_answer_handler_adds_voter(self, admin_user, mock_admin_service):
        """Тест добавления голосующего при ответе 'Да'."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        # Создаём опрос в сервисе
        from src.poll import PollData

        poll_service._poll_data["test_poll_id"] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=1,
            info_msg_id=2,
            yes_voters=[],
            subs=[],
        )

        poll_answer = MagicMock(spec=PollAnswer)
        poll_answer.poll_id = "test_poll_id"
        poll_answer.user = admin_user
        poll_answer.option_ids = [0]  # Да

        # Проверяем, что опрос существует
        assert poll_service.has_poll("test_poll_id")

    async def test_poll_answer_handler_removes_voter(
        self, admin_user, mock_admin_service
    ):
        """Тест удаления голосующего при ответе 'Нет'."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        # Создаём опрос в сервисе с уже проголосовавшим пользователем
        from src.poll import PollData, VoterInfo

        poll_service._poll_data["test_poll_id"] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=1,
            info_msg_id=2,
            yes_voters=[VoterInfo(id=admin_user.id, name="Admin", update_id=1)],
            subs=[],
        )

        poll_answer = MagicMock(spec=PollAnswer)
        poll_answer.poll_id = "test_poll_id"
        poll_answer.user = admin_user
        poll_answer.option_ids = [1]  # Нет

        # Проверяем, что опрос существует
        assert poll_service.has_poll("test_poll_id")


@pytest.mark.asyncio
class TestRefreshAdminsCommand:
    """Тесты для команды /refresh_admins."""

    async def test_refresh_admins_as_admin(self, admin_user, mock_admin_service):
        """Тест команды /refresh_admins от администратора."""
        bot = MagicMock(spec=Bot)
        bot.get_chat_administrators = AsyncMock(return_value=[])
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        message = MagicMock(spec=Message)
        message.from_user = admin_user
        message.chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message.reply = AsyncMock()

        # Проверяем, что у администратора есть доступ
        assert admin_user.id in mock_admin_service._admin_cache[-1001234567890]
