"""Тесты команды /player rename."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update

from src.db import ensure_player, get_player_info, init_db
from src.handlers import register_handlers
from src.services import BotStateService, PollService


@pytest.fixture
def player_dispatcher(admin_service):
    init_db()
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
    return bot, dp


async def send_command(player_dispatcher, user, text, reply_user=None):
    bot, dp = player_dispatcher
    chat = Chat(id=-1001234567890, type="supergroup")
    reply = (
        Message(message_id=1, date=datetime.now(UTC), chat=chat, from_user=reply_user)
        if reply_user
        else None
    )
    message = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
        reply_to_message=reply,
    )
    await dp.feed_update(bot, Update(update_id=2, message=message))
    return bot.call_args.args[0].text if bot.called else ""


@pytest.mark.asyncio
class TestPlayerRename:
    @pytest.mark.parametrize("target", ["111", "@player_user", "Иван"])
    async def test_rename_by_id_username_or_name(
        self, player_dispatcher, admin_user, target
    ):
        ensure_player(111, "player_user", "Иван Старый")

        def check_backup_before_rename(reason):
            assert reason == "player_rename_command"
            assert get_player_info(111)["fullname"] == "Иван Старый"

        with patch(
            "src.handlers.create_backup", side_effect=check_backup_before_rename
        ) as backup:
            text = await send_command(
                player_dispatcher, admin_user, f"/player rename {target} Иван Петров"
            )

        assert get_player_info(111)["fullname"] == "Иван Петров"
        assert get_player_info(111)["name"] == "player_user"
        assert "Игрок переименован" in text
        assert "Иван Петров" in text
        backup.assert_called_once_with("player_rename_command")

    @pytest.mark.parametrize("existing", [True, False])
    async def test_rename_by_reply(
        self, player_dispatcher, admin_user, regular_user, existing
    ):
        if existing:
            ensure_player(regular_user.id, regular_user.username, "Old Name")

        with patch("src.handlers.create_backup") as backup:
            text = await send_command(
                player_dispatcher,
                admin_user,
                "/player rename Новое полное имя",
                reply_user=regular_user,
            )

        player = get_player_info(regular_user.id)
        assert player["fullname"] == "Новое полное имя"
        assert player["name"] == regular_user.username
        assert "Игрок переименован" in text
        backup.assert_called_once_with("player_rename_command")

    @pytest.mark.parametrize("suffix", ["", "   ", " 111", " @player_user   "])
    async def test_missing_arguments_show_usage_without_changes(
        self, player_dispatcher, admin_user, suffix
    ):
        ensure_player(111, "player_user", "Old Name")

        with patch("src.handlers.create_backup") as backup:
            text = await send_command(
                player_dispatcher, admin_user, f"/player rename{suffix}"
            )

        assert "Переименование игрока" in text
        assert get_player_info(111)["fullname"] == "Old Name"
        backup.assert_not_called()

    async def test_empty_name_in_reply_does_not_register_player(
        self, player_dispatcher, admin_user, regular_user
    ):
        with patch("src.handlers.create_backup") as backup:
            text = await send_command(
                player_dispatcher,
                admin_user,
                "/player rename  \n",
                reply_user=regular_user,
            )

        assert "Переименование игрока" in text
        assert get_player_info(regular_user.id) is None
        backup.assert_not_called()

    @pytest.mark.parametrize("target", ["99999", "@missing_user", "@"])
    async def test_missing_player_does_not_change_existing_players(
        self, player_dispatcher, admin_user, target
    ):
        ensure_player(111, "player_user", "Old Name")

        with patch("src.handlers.create_backup") as backup:
            text = await send_command(
                player_dispatcher, admin_user, f"/player rename {target} New Name"
            )

        assert "не найден" in text
        assert get_player_info(111)["fullname"] == "Old Name"
        backup.assert_not_called()

    async def test_ambiguous_name_requires_id(self, player_dispatcher, admin_user):
        ensure_player(111, "ivan_one", "Иван Один")
        ensure_player(112, "ivan_two", "Иван Два")

        with patch("src.handlers.create_backup") as backup:
            text = await send_command(
                player_dispatcher, admin_user, "/player rename Иван Новое имя"
            )

        assert "несколько игроков" in text
        assert "111" in text and "112" in text
        assert get_player_info(111)["fullname"] == "Иван Один"
        assert get_player_info(112)["fullname"] == "Иван Два"
        backup.assert_not_called()

    async def test_regular_user_cannot_rename(self, player_dispatcher, regular_user):
        ensure_player(111, "player_user", "Old Name")

        with patch("src.handlers.create_backup") as backup:
            text = await send_command(
                player_dispatcher, regular_user, "/player rename 111 New Name"
            )

        assert not text
        assert get_player_info(111)["fullname"] == "Old Name"
        backup.assert_not_called()

    async def test_name_is_preserved_and_html_escaped(
        self, player_dispatcher, admin_user
    ):
        ensure_player(111, "player_user", "Old Name")

        text = await send_command(
            player_dispatcher,
            admin_user,
            "/player rename 111   O'Connor <Иван> & друзья  ",
        )

        assert get_player_info(111)["fullname"] == "O'Connor <Иван> & друзья"
        assert "O'Connor &lt;Иван&gt; &amp; друзья" in text
        bot, _ = player_dispatcher
        assert bot.call_args.args[0].parse_mode == "HTML"

    async def test_database_failure_is_reported(self, player_dispatcher, admin_user):
        ensure_player(111, "player_user", "Old Name")

        with patch("src.handlers.rename_player", return_value=False):
            text = await send_command(
                player_dispatcher, admin_user, "/player rename 111 New Name"
            )

        assert "Не удалось переименовать" in text
        assert get_player_info(111)["fullname"] == "Old Name"
