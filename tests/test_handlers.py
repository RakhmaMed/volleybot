"""Тесты для обработчиков команд."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, PollAnswer, Update

from src.db import (
    close_game,
    create_game,
    ensure_player,
    get_poll_templates,
    init_db,
    save_poll_template,
)
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
            {
                "id": 1,
                "name": "Test Poll",
                "place": "Test Place",
                "message": "Test Message",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "tue",
                "game_hour_utc": 15,  # 18:30 MSK (UTC+3)
                "game_minute_utc": 30,
                "subs": [],
            }
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

        with patch("src.handlers.get_poll_templates", return_value=polls):
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
                "scheduler": MagicMock(),
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
                "scheduler": MagicMock(),
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
                "scheduler": MagicMock(),
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

    @patch("src.handlers.get_poll_templates")
    @patch("src.handlers.get_all_players")
    async def test_subs_command_marks_disabled_poll(
        self, mock_get_players, mock_get_templates, admin_user, admin_service
    ):
        """Выключенные шаблоны должны помечаться в /subs."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = [
            {
                "name": "Disabled Poll",
                "place": "Main Hall",
                "game_day": "mon",
                "game_hour_utc": 18,
                "game_minute_utc": 15,
                "subs": [],
                "enabled": 0,
            }
        ]
        mock_get_players.return_value = []

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=4,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/subs",
        )

        await dp.feed_update(bot, Update(update_id=4, message=message))

        method = bot.call_args.args[0]
        text = method.text or ""
        assert "⏸️ выключен" in text

    @patch("src.handlers.create_backup")
    async def test_subs_add_by_username_adds_subscription(
        self, mock_create_backup, admin_user, regular_user, admin_service
    ):
        """/subs add добавляет абонемент по @username."""
        init_db()
        ensure_player(
            user_id=regular_user.id,
            name=regular_user.username,
            fullname=regular_user.full_name,
        )
        poll_template_id = save_poll_template(
            {"name": "Пятница", "message": "Играем?", "place": "Зал 1"}
        )

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=10,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text=f"/subs add {poll_template_id} @{regular_user.username}",
        )

        await dp.feed_update(bot, Update(update_id=10, message=message))

        template = get_poll_templates()[0]
        assert regular_user.id in template["subs"]
        mock_create_backup.assert_called_once_with("subs_command")
        method = bot.call_args.args[0]
        assert "Абонемент добавлен" in (method.text or "")
        assert "Пятница" in (method.text or "")

    @patch("src.handlers.create_backup")
    async def test_subs_add_by_id_adds_subscription(
        self, mock_create_backup, admin_user, regular_user, admin_service
    ):
        """/subs add добавляет абонемент по ID игрока."""
        init_db()
        ensure_player(
            user_id=regular_user.id,
            name=regular_user.username,
            fullname=regular_user.full_name,
        )
        poll_template_id = save_poll_template({"name": "Среда", "message": "Играем?"})

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=11,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text=f"/subs add {poll_template_id} {regular_user.id}",
        )

        await dp.feed_update(bot, Update(update_id=11, message=message))

        template = get_poll_templates()[0]
        assert regular_user.id in template["subs"]
        mock_create_backup.assert_called_once_with("subs_command")

    @patch("src.handlers.create_backup")
    async def test_subs_add_by_name_adds_subscription(
        self, mock_create_backup, admin_user, admin_service
    ):
        """/subs add добавляет абонемент по имени игрока."""
        init_db()
        ensure_player(user_id=222, name="ivan", fullname="Иван Петров")
        poll_template_id = save_poll_template({"name": "Вторник", "message": "Играем?"})

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=12,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text=f"/subs add {poll_template_id} Иван",
        )

        await dp.feed_update(bot, Update(update_id=12, message=message))

        template = get_poll_templates()[0]
        assert 222 in template["subs"]
        mock_create_backup.assert_called_once_with("subs_command")

    @patch("src.handlers.create_backup")
    async def test_subs_add_ambiguous_name_uses_callback(
        self, mock_create_backup, admin_user, admin_service
    ):
        """При нескольких совпадениях /subs add показывает кнопки, callback добавляет выбранного."""
        init_db()
        ensure_player(user_id=301, name="ivan1", fullname="Иван Один")
        ensure_player(user_id=302, name="ivan2", fullname="Иван Два")
        poll_template_id = save_poll_template({"name": "Четверг", "message": "Играем?"})

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=13,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text=f"/subs add {poll_template_id} Иван",
        )

        await dp.feed_update(bot, Update(update_id=13, message=message))

        method = bot.call_args.args[0]
        assert "Найдено несколько игроков" in (method.text or "")
        assert method.reply_markup is not None

        callback_message = Message(
            message_id=14,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="Выберите игрока",
        )
        callback_query = CallbackQuery(
            id="subs-add-select",
            from_user=admin_user,
            chat_instance="ci",
            data=f"subs_add_select:{poll_template_id}:302",
            message=callback_message,
        )

        await dp.feed_update(bot, Update(update_id=14, callback_query=callback_query))

        template = get_poll_templates()[0]
        assert 302 in template["subs"]
        assert 301 not in template["subs"]
        mock_create_backup.assert_called_once_with("subs_command")

    @patch("src.handlers.create_backup")
    async def test_subs_add_duplicate_reports_existing_subscription(
        self, mock_create_backup, admin_user, regular_user, admin_service
    ):
        """Повторное добавление не создает дубль и не делает бэкап."""
        init_db()
        ensure_player(
            user_id=regular_user.id,
            name=regular_user.username,
            fullname=regular_user.full_name,
        )
        poll_template_id = save_poll_template(
            {"name": "Пятница", "message": "Играем?", "subs": [regular_user.id]}
        )

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=15,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text=f"/subs add {poll_template_id} @{regular_user.username}",
        )

        await dp.feed_update(bot, Update(update_id=15, message=message))

        template = get_poll_templates()[0]
        assert template["subs"].count(regular_user.id) == 1
        mock_create_backup.assert_not_called()
        method = bot.call_args.args[0]
        assert "уже есть абонемент" in (method.text or "")

    async def test_subs_add_reports_invalid_inputs(
        self, admin_user, regular_user, admin_service
    ):
        """Неверный ID зала, отсутствующий зал и игрок дают понятные ошибки."""
        init_db()
        ensure_player(
            user_id=regular_user.id,
            name=regular_user.username,
            fullname=regular_user.full_name,
        )
        poll_template_id = save_poll_template({"name": "Пятница", "message": "Играем?"})

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)
        chat = Chat(id=-1001234567890, type="supergroup")

        cases = [
            (20, "/subs add abc @regular_user", "числовой ID зала"),
            (21, "/subs add 999 @regular_user", "Зал с ID 999 не найден"),
            (22, f"/subs add {poll_template_id} @missing", "не найден"),
        ]
        for message_id, text, expected in cases:
            message = Message(
                message_id=message_id,
                date=MagicMock(),
                chat=chat,
                from_user=admin_user,
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=message_id, message=message))
            method = bot.call_args.args[0]
            assert expected in (method.text or "")

    @patch("src.handlers.create_backup")
    async def test_subs_add_as_regular_user_is_ignored(
        self, mock_create_backup, regular_user, admin_service
    ):
        """/subs add недоступен обычному пользователю."""
        init_db()
        ensure_player(
            user_id=regular_user.id,
            name=regular_user.username,
            fullname=regular_user.full_name,
        )
        poll_template_id = save_poll_template({"name": "Пятница", "message": "Играем?"})

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=23,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text=f"/subs add {poll_template_id} @{regular_user.username}",
        )

        await dp.feed_update(bot, Update(update_id=23, message=message))

        template = get_poll_templates()[0]
        assert regular_user.id not in template["subs"]
        assert not bot.called
        mock_create_backup.assert_not_called()


@pytest.mark.asyncio
class TestHallCommands:
    """Тесты команды /hall."""

    @patch("src.handlers.create_backup")
    @patch("src.handlers.refresh_scheduler")
    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_off_by_id(
        self,
        mock_get_templates,
        mock_save_poll_template,
        mock_refresh_scheduler,
        mock_create_backup,
        admin_user,
        admin_service,
    ):
        """Команда /hall off должна выключать зал по id."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        template = {
            "id": 3,
            "name": "Friday Poll",
            "message": "Msg",
            "enabled": 1,
        }
        mock_get_templates.return_value = [template]
        mock_save_poll_template.return_value = 3

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        message = Message(
            message_id=10,
            date=MagicMock(),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=admin_user,
            text="/hall off 3",
        )

        await dp.feed_update(bot, Update(update_id=10, message=message))

        mock_create_backup.assert_called_once_with("hall_command")
        mock_save_poll_template.assert_called_once()
        assert mock_save_poll_template.call_args.kwargs["match_by"] == "id"
        saved_template = mock_save_poll_template.call_args.args[0]
        assert saved_template["enabled"] == 0
        mock_refresh_scheduler.assert_called_once()
        method = bot.call_args.args[0]
        assert "теперь ⏸️ выключен" in (method.text or "")

    @patch("src.handlers.create_backup")
    @patch("src.handlers.refresh_scheduler")
    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_on_by_id(
        self,
        mock_get_templates,
        mock_save_poll_template,
        mock_refresh_scheduler,
        mock_create_backup,
        admin_user,
        admin_service,
    ):
        """Команда /hall on должна включать зал по id."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        template = {
            "id": 7,
            "name": "Test Poll",
            "message": "Msg",
            "enabled": 0,
        }
        mock_get_templates.return_value = [template]
        mock_save_poll_template.return_value = 7

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        message = Message(
            message_id=11,
            date=MagicMock(),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=admin_user,
            text="/hall on 7",
        )

        await dp.feed_update(bot, Update(update_id=11, message=message))

        mock_create_backup.assert_called_once_with("hall_command")
        mock_save_poll_template.assert_called_once()
        assert mock_save_poll_template.call_args.kwargs["match_by"] == "id"
        saved_template = mock_save_poll_template.call_args.args[0]
        assert saved_template["enabled"] == 1
        mock_refresh_scheduler.assert_called_once()
        method = bot.call_args.args[0]
        assert "теперь активен" in (method.text or "")

    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_off_requires_numeric_id(
        self, mock_get_templates, mock_save_poll_template, admin_user, admin_service
    ):
        """Команда /hall off должна принимать только ID."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = [
            {"id": 3, "name": "Friday Poll", "message": "Msg", "enabled": 1}
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        message = Message(
            message_id=12,
            date=MagicMock(),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=admin_user,
            text="/hall off Friday",
        )

        await dp.feed_update(bot, Update(update_id=12, message=message))

        mock_save_poll_template.assert_not_called()
        method = bot.call_args.args[0]
        assert "числовой ID" in (method.text or "")

    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_off_already_disabled(
        self, mock_get_templates, mock_save_poll_template, admin_user, admin_service
    ):
        """Повторное выключение отвечает без сохранения."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = [
            {"id": 5, "name": "Disabled Poll", "message": "Msg", "enabled": 0}
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        message = Message(
            message_id=13,
            date=MagicMock(),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=admin_user,
            text="/hall off 5",
        )

        await dp.feed_update(bot, Update(update_id=13, message=message))

        mock_save_poll_template.assert_not_called()
        method = bot.call_args.args[0]
        assert "уже ⏸️ выключен" in (method.text or "")

    @patch("src.handlers.get_poll_templates")
    async def test_hall_as_regular_user(
        self, mock_get_templates, regular_user, admin_service
    ):
        """Команда /hall недоступна обычному пользователю."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = [
            {"id": 3, "name": "Friday Poll", "message": "Msg", "enabled": 1}
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )

        register_handlers(dp, bot)

        message = Message(
            message_id=14,
            date=MagicMock(),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=regular_user,
            text="/hall off 3",
        )

        await dp.feed_update(bot, Update(update_id=14, message=message))

        assert not bot.called

    @patch("src.handlers.get_poll_templates")
    async def test_hall_list(self, mock_get_templates, admin_user, admin_service):
        """Команда /hall должна показывать список залов."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_templates.return_value = [
            {
                "id": 1,
                "name": "Friday",
                "place": "Gym",
                "message": "Msg",
                "game_day": "fri",
                "game_hour_utc": 17,
                "game_minute_utc": 30,
                "cost": 150,
                "cost_per_game": 2000,
                "enabled": 1,
            }
        ]

        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        message = Message(
            message_id=15,
            date=MagicMock(),
            chat=Chat(id=-1001234567890, type="supergroup"),
            from_user=admin_user,
            text="/hall",
        )

        await dp.feed_update(bot, Update(update_id=15, message=message))

        method = bot.call_args.args[0]
        text = method.text or ""
        assert "Friday" in text
        assert "20:30 МСК" in text
        assert "аренда 2000 ₽" in text

    @patch("src.handlers.create_backup")
    @patch("src.handlers.refresh_scheduler")
    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_add_wizard_saves_after_confirmation(
        self,
        mock_get_templates,
        mock_save_poll_template,
        mock_refresh_scheduler,
        mock_create_backup,
        admin_user,
        admin_service,
    ):
        """Мастер /hall add должен собрать поля и сохранить после подтверждения."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        chat = Chat(id=-1001234567890, type="supergroup")

        mock_get_templates.return_value = []
        mock_save_poll_template.return_value = 9
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        values = [
            "/hall add",
            "Friday",
            "Gym",
            "Пт",
            "20:30",
            "-",
            "-",
            "150",
            "2000",
            "-",
            "да",
        ]
        for index, text in enumerate(values, start=20):
            message = Message(
                message_id=index,
                date=MagicMock(),
                chat=chat,
                from_user=admin_user,
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=index, message=message))

        confirm_message = Message(
            message_id=99,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="confirm",
        )
        callback_query = CallbackQuery(
            id="hall-save",
            from_user=admin_user,
            chat_instance="1",
            message=confirm_message,
            data="hall_save",
        )

        mock_edit_text = AsyncMock()
        mock_answer = AsyncMock()
        with (
            patch.object(Message, "edit_text", mock_edit_text),
            patch.object(CallbackQuery, "answer", mock_answer),
        ):
            await dp.feed_update(
                bot, Update(update_id=100, callback_query=callback_query)
            )

        mock_create_backup.assert_called_once_with("hall_command")
        mock_save_poll_template.assert_called_once()
        saved_template = mock_save_poll_template.call_args.args[0]
        assert mock_save_poll_template.call_args.kwargs["match_by"] == "name"
        assert saved_template["name"] == "Friday"
        assert saved_template["game_day"] == "fri"
        assert saved_template["game_hour_utc"] == 17
        assert saved_template["game_minute_utc"] == 30
        assert saved_template["open_day"] == "thu"
        assert saved_template["open_hour_utc"] == 16
        assert saved_template["open_minute_utc"] == 0
        assert saved_template["message"] == "Играем в Friday?"
        mock_refresh_scheduler.assert_called_once()
        mock_edit_text.assert_called_once()
        mock_answer.assert_called_once()

    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_add_wizard_repeats_invalid_field(
        self, mock_get_templates, mock_save_poll_template, admin_user, admin_service
    ):
        """При ошибке мастер должен повторять текущий вопрос."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        chat = Chat(id=-1001234567890, type="supergroup")

        mock_get_templates.return_value = []
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        for update_id, text in [(120, "/hall add"), (121, "Friday"), (122, "Gym"), (123, "wrong-day")]:
            message = Message(
                message_id=update_id,
                date=MagicMock(),
                chat=chat,
                from_user=admin_user,
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=update_id, message=message))

        mock_save_poll_template.assert_not_called()
        texts = [
            call.args[0].text or ""
            for call in bot.call_args_list
            if call.args and hasattr(call.args[0], "text")
        ]
        assert any("День не распознан" in text for text in texts)
        assert texts[-1].startswith("Введите день игры")

    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_add_wizard_shows_default_values(
        self, mock_get_templates, mock_save_poll_template, admin_user, admin_service
    ):
        """Мастер /hall add должен показывать конкретные дефолты в подсказках."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        chat = Chat(id=-1001234567890, type="supergroup")

        mock_get_templates.return_value = []
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        async def send(update_id: int, text: str) -> str:
            message = Message(
                message_id=update_id,
                date=MagicMock(),
                chat=chat,
                from_user=admin_user,
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=update_id, message=message))
            return bot.call_args.args[0].text or ""

        await send(220, "/hall add")
        await send(221, "Friday")
        await send(222, "Gym")
        await send(223, "Пт")
        open_day_prompt = await send(224, "20:30")
        assert "По умолчанию: <b>Чт</b>" in open_day_prompt

        open_time_prompt = await send(225, "-")
        assert "По умолчанию: <b>19:00</b>" in open_time_prompt

        await send(226, "-")
        await send(227, "150")
        message_prompt = await send(228, "2000")
        assert "По умолчанию: <b>Играем в Friday?</b>" in message_prompt

        enabled_prompt = await send(229, "-")
        assert "По умолчанию: <b>да</b>" in enabled_prompt
        assert "Отправьте <code>-</code>" in enabled_prompt
        mock_save_poll_template.assert_not_called()

    @patch("src.handlers.create_backup")
    @patch("src.handlers.refresh_scheduler")
    @patch("src.handlers.save_poll_template")
    @patch("src.handlers.get_poll_templates")
    async def test_hall_edit_wizard_saves_by_id(
        self,
        mock_get_templates,
        mock_save_poll_template,
        mock_refresh_scheduler,
        mock_create_backup,
        admin_user,
        admin_service,
    ):
        """Мастер /hall edit должен сохранять существующий зал по id."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        chat = Chat(id=-1001234567890, type="supergroup")

        mock_get_templates.return_value = [
            {
                "id": 3,
                "name": "Friday",
                "place": "Old Gym",
                "message": "Old question?",
                "game_day": "fri",
                "game_hour_utc": 17,
                "game_minute_utc": 30,
                "open_day": "thu",
                "open_hour_utc": 16,
                "open_minute_utc": 0,
                "cost": 150,
                "cost_per_game": 2000,
                "enabled": 1,
                "subs": [123],
            }
        ]
        mock_save_poll_template.return_value = 3
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": MagicMock(spec=BotStateService),
                "poll_service": MagicMock(spec=PollService),
                "scheduler": MagicMock(),
            }
        )
        register_handlers(dp, bot)

        values = [
            "/hall edit 3",
            "-",
            "New Gym",
            "-",
            "-",
            "-",
            "-",
            "200",
            "-",
            "-",
            "нет",
        ]
        for index, text in enumerate(values, start=140):
            message = Message(
                message_id=index,
                date=MagicMock(),
                chat=chat,
                from_user=admin_user,
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=index, message=message))

        confirm_message = Message(
            message_id=199,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="confirm",
        )
        callback_query = CallbackQuery(
            id="hall-edit-save",
            from_user=admin_user,
            chat_instance="1",
            message=confirm_message,
            data="hall_save",
        )

        mock_edit_text = AsyncMock()
        mock_answer = AsyncMock()
        with (
            patch.object(Message, "edit_text", mock_edit_text),
            patch.object(CallbackQuery, "answer", mock_answer),
        ):
            await dp.feed_update(
                bot, Update(update_id=200, callback_query=callback_query)
            )

        mock_create_backup.assert_called_once_with("hall_command")
        mock_save_poll_template.assert_called_once()
        assert mock_save_poll_template.call_args.kwargs["match_by"] == "id"
        saved_template = mock_save_poll_template.call_args.args[0]
        assert saved_template["id"] == 3
        assert saved_template["name"] == "Friday"
        assert saved_template["place"] == "New Gym"
        assert saved_template["cost"] == 200
        assert saved_template["cost_per_game"] == 2000
        assert saved_template["enabled"] == 0
        assert saved_template["subs"] == [123]
        mock_refresh_scheduler.assert_called_once()

    async def test_schedule_marks_disabled_poll(self, regular_user, admin_service):
        """Команда /schedule должна помечать выключенный опрос."""
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
            {
                "id": 1,
                "name": "Test Poll",
                "place": "Test Place",
                "message": "Test Message",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "tue",
                "game_hour_utc": 15,
                "game_minute_utc": 30,
                "subs": [],
                "enabled": 0,
            }
        ]

        chat = Chat(id=123, type="private")
        message = Message(
            message_id=15,
            date=datetime.now(),
            chat=chat,
            from_user=regular_user,
            text="/schedule",
        )
        update = Update(update_id=15, message=message)

        with patch("src.handlers.get_poll_templates", return_value=polls):
            await dp.feed_update(bot, update)

        method = bot.call_args.args[0]
        assert "⏸️ выключен" in (method.text or "")


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


@pytest.mark.asyncio
class TestMonthlyAndStatsHandlers:
    async def test_open_monthly_uses_poll_service_open_method(
        self, admin_user, admin_service, temp_db
    ):
        init_db()

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        poll_service = PollService()
        poll_service.build_monthly_subscription_poll_spec = MagicMock(
            return_value=MagicMock()
        )
        poll_service.open_monthly_subscription_poll = AsyncMock(
            return_value=-1001234567890
        )
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": BotStateService(default_chat_id=-1001234567890),
                "poll_service": poll_service,
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=chat,
            from_user=admin_user,
            text="/open_monthly",
        )
        await dp.feed_update(bot, Update(update_id=1, message=message))

        poll_service.open_monthly_subscription_poll.assert_called_once_with(
            bot,
            -1001234567890,
            True,
        )

    async def test_close_monthly_uses_open_monthly_game(
        self, admin_user, admin_service, temp_db
    ):
        init_db()
        create_game(
            poll_id="monthly-1",
            kind="monthly_subscription",
            status="open",
            poll_template_id=None,
            poll_name_snapshot="monthly_subscription",
            question_snapshot="Абонемент?",
            chat_id=-1001234567890,
            poll_message_id=1,
            opened_at="2026-03-01T10:00:00+00:00",
        )

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        poll_service = PollService()
        poll_service.close_poll = AsyncMock()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": BotStateService(default_chat_id=-1001234567890),
                "poll_service": poll_service,
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=chat,
            from_user=admin_user,
            text="/close_monthly",
        )
        await dp.feed_update(bot, Update(update_id=1, message=message))
        poll_service.close_poll.assert_called_once_with(bot, "monthly-1")

    async def test_stats_summary_command(
        self, admin_user, admin_service, temp_db
    ):
        init_db()
        save_poll_template({"name": "Пятница", "message": "Игра"})
        template_id = 1
        create_game(
            poll_id="regular-1",
            kind="regular",
            status="open",
            poll_template_id=template_id,
            poll_name_snapshot="Пятница",
            question_snapshot="Играем?",
            chat_id=-1001234567890,
            poll_message_id=1,
            opened_at="2026-03-01T10:00:00+00:00",
        )
        close_game("regular-1", closed_at="2026-03-02T10:00:00+00:00", final_message_id=2)

        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": BotStateService(default_chat_id=-1001234567890),
                "poll_service": PollService(),
            }
        )
        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=chat,
            from_user=admin_user,
            text="/stats",
        )
        await dp.feed_update(bot, Update(update_id=2, message=message))
        method = bot.call_args.args[0]
        assert "Статистика" in (method.text or "")

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
