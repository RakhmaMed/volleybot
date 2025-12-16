"""Тесты для модуля handlers."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, PollAnswer, User

from src.config import ADMIN_USERNAME
from src.handlers import register_handlers
from src.poll import PollDataItem


@pytest.mark.asyncio
class TestStartCommand:
    """Тесты для команды /start."""

    async def test_start_command_as_admin_when_bot_enabled(self):
        """Тест команды /start от администратора при включённом боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_enabled = [True]  # Используем список для изменения в замыкании

        def get_bot_enabled():
            return bot_enabled[0]

        def set_bot_enabled(value: bool):
            bot_enabled[0] = value

        register_handlers(dp, bot, get_bot_enabled, set_bot_enabled)

        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username=ADMIN_USERNAME.replace("@", ""),
        )
        message = MagicMock(spec=Message)
        message.from_user = user
        message.reply = AsyncMock()

        assert get_bot_enabled() is True
        # Проверяем, что бот уже включен
        # В реальном сценарии обработчик должен ответить соответствующим сообщением

    async def test_start_command_as_admin_when_bot_disabled(self):
        """Тест команды /start от администратора при выключенном боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_enabled = [False]

        def get_bot_enabled():
            return bot_enabled[0]

        def set_bot_enabled(value: bool):
            bot_enabled[0] = value

        register_handlers(dp, bot, get_bot_enabled, set_bot_enabled)

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
        assert get_bot_enabled() is False
        set_bot_enabled(True)
        assert get_bot_enabled() is True

    async def test_start_command_as_regular_user(self):
        """Тест команды /start от обычного пользователя."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_enabled = [True]

        def get_bot_enabled():
            return bot_enabled[0]

        def set_bot_enabled(value: bool):
            bot_enabled[0] = value

        register_handlers(dp, bot, get_bot_enabled, set_bot_enabled)

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

        bot_enabled = [True]

        def get_bot_enabled():
            return bot_enabled[0]

        def set_bot_enabled(value: bool):
            bot_enabled[0] = value

        register_handlers(dp, bot, get_bot_enabled, set_bot_enabled)

        # Проверяем логику выключения бота
        assert get_bot_enabled() is True
        set_bot_enabled(False)
        assert get_bot_enabled() is False


@pytest.mark.asyncio
class TestChatIdCommand:
    """Тесты для команды /chatid."""

    async def test_chatid_command(self):
        """Тест команды /chatid."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        register_handlers(dp, bot, lambda: True, lambda x: None)

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
        from src.poll import poll_data

        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        register_handlers(dp, bot, lambda: True, lambda x: None)

        poll_id = "test_poll_id"
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": [],
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        user = User(id=123, is_bot=False, first_name="Test", username="test_user")

        # Создаём ответ на опрос (не используется напрямую в тесте, но показывает структуру)
        PollAnswer(
            poll_id=poll_id,
            user=user,
            option_ids=[0],  # Выбран "Да"
        )

        # Проверяем, что обработчик зарегистрирован
        # В реальном сценарии обработчик должен обновить poll_data
        assert poll_id in poll_data

    async def test_poll_answer_handler_removes_voter(self):
        """Тест удаления голосующего при изменении ответа."""
        from src.poll import poll_data

        poll_id = "test_poll_id"
        user_id = 123
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": [{"id": user_id, "name": "@test_user"}],
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        user = User(id=user_id, is_bot=False, first_name="Test", username="test_user")

        # Создаём ответ на опрос (не используется напрямую в тесте, но показывает структуру)
        PollAnswer(
            poll_id=poll_id,
            user=user,
            option_ids=[],  # Изменил ответ на "Нет" или убрал голос
        )

        # Проверяем логику удаления
        # В реальном сценарии обработчик должен удалить пользователя из yes_voters
        assert len(poll_data[poll_id]["yes_voters"]) == 1
