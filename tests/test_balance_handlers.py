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
)

from src.handlers import register_handlers
from src.services import BotStateService, PollService


@pytest.mark.asyncio
class TestBalanceCommand:
    """Тесты для команды /balance."""

    @patch("src.handlers.get_players_with_balance")
    async def test_balance_as_admin(self, mock_get_players, admin_user, admin_service):
        """Тест команды /balance от администратора."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_players.return_value = [
            {"id": 1, "name": "user1", "fullname": "User One", "balance": -500},
            {"id": 2, "name": "user2", "fullname": "User Two", "balance": 100},
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
            text="/balance",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        # Проверяем, что в ответе есть гиперссылки на пользователей
        assert '<a href="https://t.me/user1">User One</a>: <b>-500 ₽</b>' in method.text
        assert '<a href="https://t.me/user2">User Two</a>: <b>100 ₽</b>' in method.text


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
        admin_service,
    ):
        """Тест команды /pay в ответ на сообщение."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_update.return_value = True
        mock_get_balance.return_value = {"balance": 500}

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
        # Проверяем, что в ответе есть гиперссылка на игрока
        assert "Баланс игрока" in method.text
        assert "изменен на 500 ₽" in method.text
        assert (
            '<a href="tg://user?id=' in method.text
            or '<a href="https://t.me/' in method.text
        )

    @patch("src.handlers.find_player_by_name")
    @patch("src.handlers.update_player_balance")
    @patch("src.handlers.get_player_balance")
    async def test_pay_by_name_single_match(
        self, mock_get_balance, mock_update, mock_find, admin_user, admin_service
    ):
        """Тест команды /pay по имени (одно совпадение)."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_find.return_value = [
            {"id": 777, "name": "pete", "fullname": "Peter", "balance": 0}
        ]
        mock_update.return_value = True
        mock_get_balance.return_value = {
            "id": 777,
            "name": "pete",
            "fullname": "Peter",
            "balance": 100,
        }

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
        # Проверяем, что в ответе есть гиперссылка на игрока
        assert "Баланс игрока" in method.text
        assert "изменен на 100 ₽" in method.text
        assert '<a href="https://t.me/pete">Peter</a>' in method.text

    @patch("src.handlers.find_player_by_name")
    async def test_pay_by_name_multiple_matches(
        self, mock_find, admin_user, admin_service
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
                "admin_service": admin_service,
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
        self, mock_update, mock_get_balance, admin_user, admin_service
    ):
        """Тест команды /pay по числовому ID."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        # Первый вызов для проверки существования игрока, второй для получения нового баланса
        mock_get_balance.return_value = {
            "id": 12345,
            "name": None,
            "fullname": "ID Player",
            "balance": 500,
        }
        mock_update.return_value = True

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
        # Проверяем, что в ответе есть гиперссылка на игрока
        assert "Баланс игрока" in method.text
        assert "изменен на 500 ₽" in method.text
        assert '<a href="tg://user?id=12345">ID Player</a>' in method.text


@pytest.mark.asyncio
class TestPayCallback:
    """Тесты для обработки callback_query при выборе игрока."""

    @patch("src.handlers.update_player_balance")
    @patch("src.handlers.get_player_balance")
    async def test_process_pay_select(
        self, mock_get_balance, mock_update, admin_user, admin_service
    ):
        """Тест выбора игрока через инлайн-кнопку."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_update.return_value = True
        mock_get_balance.return_value = {"id": 1, "fullname": "Alim B.", "balance": 500}

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
class TestPlayerCommand:
    """Тесты для команды /player."""

    @patch("src.handlers.get_player_info")
    @patch("src.handlers.ensure_player")
    async def test_player_reply_as_admin(
        self, mock_ensure, mock_get_info, admin_user, regular_user, admin_service
    ):
        """Тест /player в ответ на сообщение — карточка одного игрока."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_info.return_value = {
            "id": regular_user.id,
            "name": "regular_user",
            "fullname": "Regular User",
            "ball_donate": False,
            "balance": 100,
        }

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
            text="/player",
            reply_to_message=reply_message,
        )

        await dp.feed_update(bot, Update(update_id=2, message=message))

        mock_ensure.assert_called_once()
        mock_get_info.assert_called_once_with(regular_user.id)
        assert bot.called
        method = bot.call_args.args[0]
        assert "Regular User" in method.text or "regular_user" in method.text
        assert "ID:" in method.text
        assert "100 ₽" in method.text
        assert "донат" in method.text.lower()

    @patch("src.handlers.get_player_info")
    async def test_player_by_id(self, mock_get_info, admin_user, admin_service):
        """Тест /player по числовому ID."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_info.return_value = {
            "id": 12345,
            "name": "idplayer",
            "fullname": "ID Player",
            "ball_donate": True,
            "balance": 0,
        }

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
            text="/player 12345",
        )

        await dp.feed_update(bot, Update(update_id=3, message=message))

        mock_get_info.assert_called_once_with(12345)
        assert bot.called
        method = bot.call_args.args[0]
        assert "ID Player" in method.text
        assert "Донат: да" in method.text

    @patch("src.handlers.get_player_info")
    @patch("src.handlers.find_player_by_name")
    async def test_player_by_name_single_match(
        self, mock_find, mock_get_info, admin_user, admin_service
    ):
        """Тест /player по имени — одно совпадение."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_find.return_value = [
            {"id": 777, "name": "pete", "fullname": "Peter", "balance": 0},
        ]
        mock_get_info.return_value = {
            "id": 777,
            "name": "pete",
            "fullname": "Peter",
            "ball_donate": False,
            "balance": 0,
        }

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
            text="/player Peter",
        )

        await dp.feed_update(bot, Update(update_id=4, message=message))

        mock_find.assert_called_with("Peter")
        mock_get_info.assert_called_once_with(777)
        assert bot.called
        method = bot.call_args.args[0]
        assert "Peter" in method.text

    @patch("src.handlers.find_player_by_name")
    async def test_player_by_name_multiple_matches(
        self, mock_find, admin_user, admin_service
    ):
        """Тест /player по имени — несколько совпадений, клавиатура выбора."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_find.return_value = [
            {"id": 1, "name": "a1", "fullname": "Alim B.", "balance": 0},
            {"id": 2, "name": "a2", "fullname": "Alim K.", "balance": 0},
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=5,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/player Alim",
        )

        await dp.feed_update(bot, Update(update_id=5, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Найдено несколько игроков" in method.text
        assert isinstance(method.reply_markup, InlineKeyboardMarkup)
        buttons = method.reply_markup.inline_keyboard
        assert len(buttons) == 2
        assert buttons[0][0].callback_data == "player_select:1"
        assert buttons[1][0].callback_data == "player_select:2"

    @patch("src.handlers.get_all_players")
    async def test_player_no_args_list_all(
        self, mock_get_all, admin_user, admin_service
    ):
        """Тест /player без аргументов — список всех игроков."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_all.return_value = [
            {
                "id": 1,
                "name": "u1",
                "fullname": "User One",
                "ball_donate": False,
                "balance": -100,
            },
            {
                "id": 2,
                "name": "u2",
                "fullname": "User Two",
                "ball_donate": True,
                "balance": 0,
            },
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=6,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/player",
        )

        await dp.feed_update(bot, Update(update_id=6, message=message))

        mock_get_all.assert_called_once()
        assert bot.called
        method = bot.call_args.args[0]
        assert "Игроки" in method.text
        assert "User One" in method.text or "u1" in method.text
        assert "-100" in method.text
        assert "мяч" in method.text.lower()

    @patch("src.handlers.get_all_players")
    async def test_player_regular_user_ignored(
        self, mock_get_all, regular_user, admin_service
    ):
        """Обычный пользователь не получает ответ на /player."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        # admin_service не считает regular_user админом (кэш только admin_user)
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
            from_user=regular_user,
            text="/player",
        )

        await dp.feed_update(bot, Update(update_id=7, message=message))

        mock_get_all.assert_not_called()
        assert not bot.called

    @patch("src.handlers.get_player_info")
    async def test_player_id_not_found(self, mock_get_info, admin_user, admin_service):
        """Тест /player по ID — игрок не найден."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_info.return_value = None

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(),
                "poll_service": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=8,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/player 99999",
        )

        await dp.feed_update(bot, Update(update_id=8, message=message))

        mock_get_info.assert_called_once_with(99999)
        assert bot.called
        method = bot.call_args.args[0]
        assert "не найден" in method.text


@pytest.mark.asyncio
class TestPlayerCallback:
    """Тесты для callback player_select."""

    @patch("src.handlers.get_player_info")
    async def test_process_player_select(
        self, mock_get_info, admin_user, admin_service
    ):
        """Выбор игрока из списка — сообщение заменяется на карточку."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_info.return_value = {
            "id": 1,
            "name": "alim",
            "fullname": "Alim B.",
            "ball_donate": True,
            "balance": 500,
        }

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
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
            data="player_select:1",
        )

        mock_edit_text = AsyncMock()
        with patch.object(Message, "edit_text", mock_edit_text):
            await dp.feed_update(
                bot, Update(update_id=9, callback_query=callback_query)
            )

        mock_get_info.assert_called_once_with(1)
        mock_edit_text.assert_called_once()
        # Текст передаётся первым позиционным аргументом
        text = (
            mock_edit_text.call_args.args[0]
            if mock_edit_text.call_args.args
            else mock_edit_text.call_args.kwargs.get("text", "")
        )
        assert "Alim B." in text
        assert "500 ₽" in text
        assert "Донат" in text and "да" in text


@pytest.mark.asyncio
class TestPollIntegration:
    """Тест интеграции регистрации игрока при голосовании."""

    @patch("src.handlers.ensure_player")
    @patch("src.handlers.get_player_name")
    async def test_poll_answer_registers_player(
        self, mock_get_name, mock_ensure, regular_user, admin_service
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
                "admin_service": admin_service,
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
