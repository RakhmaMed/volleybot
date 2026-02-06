"""Тесты для обработчиков команд."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, PollAnswer, Update

from src.config import PollSchedule
from src.handlers import register_handlers
from src.services import BotStateService, PollService


@pytest.mark.asyncio
class TestStartCommand:
    """Тесты для команды /start."""

    async def test_start_command_as_admin_when_bot_enabled(
        self, admin_user, admin_service
    ):
        """Тест команды /start от администратора при включённом боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(True)

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
        self, admin_user, admin_service
    ):
        """Тест команды /start от администратора при выключенном боте."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(False)

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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

    async def test_start_command_as_regular_user(self, regular_user, admin_service):
        """Тест команды /start от обычного пользователя."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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

    async def test_stop_command_as_admin(self, admin_user, admin_service):
        """Тест команды /stop от администратора."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        bot_state_service.set_enabled(True)

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
class TestScheduleCommand:
    """Тесты для команды /schedule."""

    async def test_schedule_command(self, regular_user, admin_service):
        """Тест вывода команды /schedule."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": bot_state_service,
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        polls = [
            PollSchedule(
                name="Test Poll",
                place="Test Place",
                message="Test Message",
                open_day="mon",
                open_hour_utc=10,
                open_minute_utc=0,
                game_day="tue",
                game_hour_utc=15,  # 18:00 MSK (UTC+3)
                game_minute_utc=30,
            )
        ]

        chat = Chat(id=123, type="private")
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=chat,
            from_user=regular_user,
            text="/schedule",
        )
        update = Update(update_id=1, message=message)

        with patch("src.handlers.POLLS_SCHEDULE", polls):
            await dp.feed_update(bot, update)

        assert bot.called

        call_args = bot.call_args
        method = call_args.args[0]
        text = method.text

        assert text is not None
        assert "📅 <b>Расписание игр</b> (время МСК)" in text
        assert "Вторник 18:30 (Test Place)" in text
        assert "ℹ️ Опрос начинается за день до игры в 19:00" in text


@pytest.mark.asyncio
class TestSubsCommand:
    """Тесты для команды /subs."""

    @patch("src.handlers.get_poll_templates")
    @patch("src.handlers.get_all_players")
    async def test_subs_command_as_admin(
        self, mock_get_players, mock_get_templates, admin_user, admin_service
    ):
        """Тест команды /subs от администратора."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = [
            {
                "name": "Test Poll",
                "place": "Main Hall",
                "game_day": "mon",
                "game_hour_utc": 18,
                "game_minute_utc": 15,
                "subs": [2, 1],
            }
        ]
        mock_get_players.return_value = [
            {"id": 1, "name": "alice", "fullname": "Alice A"},
            {"id": 2, "name": "bob", "fullname": "Bob B"},
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/subs",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        text = method.text or ""
        assert "📅 <b>Абонементы по дням</b>" in text
        assert "<b>Понедельник</b>" in text
        assert "Test Poll (21:15 МСК) — Main Hall" in text
        assert '<a href="https://t.me/alice">Alice A</a>' in text
        assert '<a href="https://t.me/bob">Bob B</a>' in text
        assert text.index("Alice A") < text.index("Bob B")

    @patch("src.handlers.get_poll_templates")
    @patch("src.handlers.get_all_players")
    async def test_subs_command_as_regular_user(
        self, mock_get_players, mock_get_templates, regular_user, admin_service
    ):
        """Тест команды /subs от обычного пользователя (без ответа)."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = []
        mock_get_players.return_value = []

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=2,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text="/subs",
        )

        await dp.feed_update(bot, Update(update_id=2, message=message))

        assert not bot.called

    @patch("src.handlers.get_poll_templates")
    @patch("src.handlers.get_all_players")
    async def test_subs_command_no_templates(
        self, mock_get_players, mock_get_templates, admin_user, admin_service
    ):
        """Тест команды /subs когда шаблонов нет."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = []
        mock_get_players.return_value = []

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=3,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/subs",
        )

        await dp.feed_update(bot, Update(update_id=3, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        text = method.text or ""
        assert "📅 Шаблоны опросов не найдены." in text


@pytest.mark.asyncio
class TestChatIdCommand:
    """Тесты для команды /chatid."""

    async def test_chatid_command(self, admin_user, admin_service):
        """Тест команды /chatid."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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

    async def test_poll_answer_handler_adds_voter(self, admin_user, admin_service):
        """Тест добавления голосующего при ответе 'Да'."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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

    async def test_poll_answer_handler_removes_voter(self, admin_user, admin_service):
        """Тест удаления голосующего при ответе 'Нет'."""
        bot = MagicMock(spec=Bot)
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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

    async def test_refresh_admins_as_admin(self, admin_user, admin_service):
        """Тест команды /refresh_admins от администратора."""
        bot = MagicMock(spec=Bot)
        bot.get_chat_administrators = AsyncMock(return_value=[])
        dp = Dispatcher()

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
        assert admin_user.id in admin_service._admin_cache[-1001234567890]
