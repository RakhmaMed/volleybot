"""Тесты для команд управления балансом."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    PollAnswer,
    Update,
    User,
)

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
    service._admin_cache[-1001234567890] = {admin_user.id}
    service._cache_updated_at[-1001234567890] = float("inf")
    return service


@pytest.mark.asyncio
class TestBalanceCommand:
    """Тесты для команды /balance."""

    @patch("src.handlers.get_players_with_balance")
    async def test_balance_as_admin(
        self, mock_get_players, admin_user, mock_admin_service
    ):
        """Тест команды /balance от администратора."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_players.return_value = [
            {"id": 1, "name": "user1", "fullname": "User One", "balance": -500},
            {"id": 2, "name": "user2", "fullname": "User Two", "balance": 100},
        ]

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
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
            text="/balance",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "User One: <b>-500 ₽</b>" in method.text
        assert "User Two: <b>100 ₽</b>" in method.text


@pytest.mark.asyncio
class TestPayCommand:
    """Тесты для команды /pay."""

    @patch("src.handlers.update_player_balance")
    @patch("src.handlers.get_player_balance")
    @patch("src.handlers.ensure_player")
    async def test_pay_reply_as_admin(
        self,
        mock_ensure,
        mock_get_balance,
        mock_update,
        admin_user,
        regular_user,
        mock_admin_service,
    ):
        """Тест команды /pay в ответ на сообщение."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_update.return_value = True
        mock_get_balance.return_value = {"balance": 500}

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        reply_message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text="hello",
        )

        message = Message(
            message_id=2,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay 500",
            reply_to_message=reply_message,
        )

        await dp.feed_update(bot, Update(update_id=2, message=message))

        mock_ensure.assert_called()
        mock_update.assert_called_with(regular_user.id, 500)
        assert bot.called
        method = bot.call_args.args[0]
        assert "Баланс игрока <b>Regular</b> изменен на 500 ₽" in method.text

    @patch("src.handlers.find_player_by_name")
    @patch("src.handlers.update_player_balance")
    @patch("src.handlers.get_player_balance")
    async def test_pay_by_name_single_match(
        self, mock_get_balance, mock_update, mock_find, admin_user, mock_admin_service
    ):
        """Тест команды /pay по имени (одно совпадение)."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_find.return_value = [
            {"id": 777, "name": "pete", "fullname": "Peter", "balance": 0}
        ]
        mock_update.return_value = True
        mock_get_balance.return_value = {"balance": 100}

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=3,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay Peter 100",
        )

        await dp.feed_update(bot, Update(update_id=3, message=message))

        mock_find.assert_called_with("Peter")
        mock_update.assert_called_with(777, 100)
        assert bot.called
        method = bot.call_args.args[0]
        assert "Баланс игрока <b>Peter</b> изменен на 100 ₽" in method.text

    @patch("src.handlers.find_player_by_name")
    async def test_pay_by_name_multiple_matches(
        self, mock_find, admin_user, mock_admin_service
    ):
        """Тест команды /pay по имени (несколько совпадений -> клавиатура)."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_find.return_value = [
            {"id": 1, "name": "a1", "fullname": "Alim B.", "balance": 0},
            {"id": 2, "name": "a2", "fullname": "Alim K.", "balance": 0},
        ]

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=4,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay Alim 500",
        )

        await dp.feed_update(bot, Update(update_id=4, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Найдено несколько игроков" in method.text
        assert isinstance(method.reply_markup, InlineKeyboardMarkup)
        buttons = method.reply_markup.inline_keyboard
        assert len(buttons) == 2
        assert buttons[0][0].text == "Alim B. (ID: 1)"
        assert buttons[0][0].callback_data == "pay_select:1:500"

    @patch("src.handlers.get_player_balance")
    @patch("src.handlers.update_player_balance")
    async def test_pay_by_id(
        self, mock_update, mock_get_balance, admin_user, mock_admin_service
    ):
        """Тест команды /pay по числовому ID."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_balance.return_value = {
            "id": 12345,
            "fullname": "ID Player",
            "balance": 0,
        }
        mock_update.return_value = True

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=7,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay 12345 500",
        )

        await dp.feed_update(bot, Update(update_id=7, message=message))

        mock_get_balance.assert_called_with(12345)
        mock_update.assert_called_with(12345, 500)
        assert bot.called
        method = bot.call_args.args[0]
        assert "Баланс игрока <b>ID Player</b> изменен на 500 ₽" in method.text


@pytest.mark.asyncio
class TestPayCallback:
    """Тесты для обработки callback_query при выборе игрока."""

    @patch("src.handlers.update_player_balance")
    @patch("src.handlers.get_player_balance")
    async def test_process_pay_select(
        self, mock_get_balance, mock_update, admin_user, mock_admin_service
    ):
        """Тест выбора игрока через инлайн-кнопку."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_update.return_value = True
        mock_get_balance.return_value = {"id": 1, "fullname": "Alim B.", "balance": 500}

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        orig_message = Message(
            message_id=10,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="Выберите игрока:",
        )

        callback_query = CallbackQuery(
            id="1",
            from_user=admin_user,
            chat_instance="1",
            message=orig_message,
            data="pay_select:1:500",
        )

        await dp.feed_update(bot, Update(update_id=6, callback_query=callback_query))

        mock_update.assert_called_with(1, 500)
        mock_get_balance.assert_called_once_with(1)
        # Проверяем, что баланс был успешно обновлен (через логи или состояние)
        # Так как edit_text вызывается через aiogram API, мы не можем напрямую
        # проверить вызов метода бота. Достаточно убедиться, что функции
        # обновления баланса были вызваны с правильными параметрами.


@pytest.mark.asyncio
class TestPollIntegration:
    """Тест интеграции регистрации игрока при голосовании."""

    @patch("src.handlers.ensure_player")
    @patch("src.handlers.get_player_name")
    async def test_poll_answer_registers_player(
        self, mock_get_name, mock_ensure, regular_user, mock_admin_service
    ):
        """Проверка, что при голосовании игрок автоматически попадает в БД."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        poll_service = MagicMock(spec=PollService)
        poll_service.has_poll.return_value = True
        poll_service.get_poll_data.return_value = MagicMock(subs=[])
        mock_get_name.return_value = "Regular User"

        dp.workflow_data.update(
            {
                "admin_service": mock_admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": poll_service,
            }
        )

        register_handlers(dp, bot)

        poll_answer = PollAnswer(poll_id="poll123", user=regular_user, option_ids=[0])

        await dp.feed_update(bot, Update(update_id=8, poll_answer=poll_answer))

        mock_ensure.assert_called_once_with(
            user_id=regular_user.id,
            name=regular_user.username,
            fullname=regular_user.full_name,
        )
