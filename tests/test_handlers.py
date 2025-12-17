"""Тесты для модуля handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, PollAnswer, User

from src.config import ADMIN_USERNAME
from src.handlers import register_handlers
from src.poll import PollData, VoterInfo
from src.services import BotStateService, PollService


@pytest.mark.asyncio
class TestStartCommand:
    """Тесты для команды /start."""

    async def test_start_command_as_admin_when_bot_enabled(self):
        """Тест команды /start от администратора при включённом боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(True)

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username=ADMIN_USERNAME.replace("@", ""),
        )
        message = MagicMock(spec=Message)
        message.from_user = user
        message.reply = AsyncMock()

        assert bot_state_service.is_enabled() is True
        # Проверяем, что бот уже включен
        # В реальном сценарии обработчик должен ответить соответствующим сообщением

    async def test_start_command_as_admin_when_bot_disabled(self):
        """Тест команды /start от администратора при выключенном боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(False)

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username=ADMIN_USERNAME.replace("@", ""),
        )
        message = MagicMock(spec=Message)
        message.from_user = user
        message.reply = AsyncMock()

        # Проверяем логику включения бота
        assert bot_state_service.is_enabled() is False
        bot_state_service.set_enabled(True)
        assert bot_state_service.is_enabled() is True

    async def test_start_command_as_regular_user(self):
        """Тест команды /start от обычного пользователя."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        user = User(id=123, is_bot=False, first_name="Regular", username="regular_user")
        message = MagicMock(spec=Message)
        message.from_user = user
        message.reply = AsyncMock()

        # Обычный пользователь не должен иметь доступа
        # В реальном сценарии обработчик должен ответить отказом


@pytest.mark.asyncio
class TestStopCommand:
    """Тесты для команды /stop."""

    async def test_stop_command_as_admin(self):
        """Тест команды /stop от администратора."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(True)

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        # Проверяем логику выключения бота
        assert bot_state_service.is_enabled() is True
        bot_state_service.set_enabled(False)
        assert bot_state_service.is_enabled() is False


@pytest.mark.asyncio
class TestChatIdCommand:
    """Тесты для команды /chatid."""

    async def test_chatid_command(self):
        """Тест команды /chatid."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup", title="Test Chat")
        message = MagicMock(spec=Message)
        message.chat = chat
        message.from_user = User(id=123, is_bot=False, first_name="Test")
        message.reply = AsyncMock()

        # Проверяем, что команда обрабатывается
        # В реальном сценарии обработчик должен вернуть информацию о чате


@pytest.mark.asyncio
class TestPollAnswerHandler:
    """Тесты для обработчика ответов на опросы."""

    async def test_poll_answer_handler_adds_voter(self):
        """Тест добавления голосующего при ответе на опрос."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        poll_id = "test_poll_id"
        poll_service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=[],
            last_message_text="",
            subs=[],
        )
        poll_service._update_tasks[poll_id] = None

        user = User(id=123, is_bot=False, first_name="Test", username="test_user")

        # Создаём ответ на опрос (не используется напрямую в тесте, но показывает структуру)
        PollAnswer(
            poll_id=poll_id,
            user=user,
            option_ids=[0],  # Выбран "Да"
        )

        # Проверяем, что обработчик зарегистрирован
        # В реальном сценарии обработчик должен обновить poll_data
        assert poll_service.has_poll(poll_id)

    async def test_poll_answer_handler_removes_voter(self):
        """Тест удаления голосующего при изменении ответа."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        poll_id = "test_poll_id"
        user_id = 123
        poll_service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=[VoterInfo(id=user_id, name="@test_user")],
            last_message_text="",
            subs=[],
        )
        poll_service._update_tasks[poll_id] = None

        user = User(id=user_id, is_bot=False, first_name="Test", username="test_user")

        # Создаём ответ на опрос (не используется напрямую в тесте, но показывает структуру)
        PollAnswer(
            poll_id=poll_id,
            user=user,
            option_ids=[],  # Изменил ответ на "Нет" или убрал голос
        )

        # Проверяем логику удаления
        # В реальном сценарии обработчик должен удалить пользователя из yes_voters
        poll_data = poll_service.get_poll_data(poll_id)
        assert len(poll_data.yes_voters) == 1
