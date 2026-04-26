from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot import on_shutdown, on_startup
from src.services import BotStateService, PollService


@pytest.mark.asyncio
async def test_on_startup_notifies_admin_about_invalid_polls_and_startup(temp_db):
    bot = AsyncMock()
    scheduler = MagicMock()
    scheduler.start = MagicMock()
    bot_state_service = BotStateService(default_chat_id=-1001234567890)
    poll_service = PollService()

    with (
        patch("src.bot.cleanup_old_backups"),
        patch("src.bot.create_backup"),
        patch("src.bot.setup_bot_commands", new=AsyncMock()),
        patch("src.bot.setup_scheduler"),
        patch("src.bot.ADMIN_USER_ID", 777),
        patch(
            "src.bot.get_poll_templates",
            return_value=[
                {
                    "name": "Среда",
                    "cost": 150,
                    "cost_per_game": 0,
                }
            ],
        ),
    ):
        await on_startup(bot, scheduler, bot_state_service, poll_service)

    assert bot.send_message.await_count == 2
    invalid_call = bot.send_message.await_args_list[0]
    startup_call = bot.send_message.await_args_list[1]

    assert invalid_call.kwargs["chat_id"] == 777
    assert "Если cost_per_game = 0, то cost тоже должен быть 0." in invalid_call.kwargs["text"]
    assert "Среда: cost=150, cost_per_game=0" in invalid_call.kwargs["text"]

    assert startup_call.kwargs == {
        "chat_id": 777,
        "text": "🟢 Бот запущен (polling).",
    }


@pytest.mark.asyncio
async def test_on_shutdown_notifies_admin_about_stop(temp_db):
    bot = AsyncMock()
    bot.session = SimpleNamespace(close=AsyncMock())
    scheduler = MagicMock()
    scheduler.running = True
    scheduler.shutdown = MagicMock()
    bot_state_service = BotStateService(default_chat_id=-1001234567890)
    poll_service = PollService()

    with (
        patch("src.bot.create_backup"),
        patch("src.bot.ADMIN_USER_ID", 777),
    ):
        await on_shutdown(bot, scheduler, bot_state_service, poll_service)

    bot.send_message.assert_awaited_once_with(chat_id=777, text="🔴 Бот остановлен.")
    scheduler.shutdown.assert_called_once()
    bot.session.close.assert_awaited_once()
