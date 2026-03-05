import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update

from src.db import cleanup_old_backups, create_backup, create_game, init_db, save_state
from src.handlers import register_handlers
from src.poll import PollData
from src.services import BotStateService, PollService


class TestDatabaseBackups:
    def test_create_backup_creates_sqlite_snapshot(self, temp_db):
        init_db()
        save_state("test_key", {"value": 1})

        backup_path = create_backup("startup")

        assert backup_path is not None
        assert backup_path.exists()
        assert backup_path.suffix == ".sqlite3"
        assert "startup" in backup_path.name

    def test_cleanup_old_backups_removes_only_expired_files(self, temp_db):
        init_db()
        backup_path = create_backup("startup")
        assert backup_path is not None
        backup_dir = backup_path.parent

        old_backup = backup_dir / "old_backup.sqlite3"
        old_backup.write_text("old", encoding="utf-8")
        fresh_backup = backup_dir / "fresh_backup.sqlite3"
        fresh_backup.write_text("fresh", encoding="utf-8")

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=11)
        fresh_time = now - timedelta(days=5)
        os.utime(old_backup, (old_time.timestamp(), old_time.timestamp()))
        os.utime(fresh_backup, (fresh_time.timestamp(), fresh_time.timestamp()))

        deleted = cleanup_old_backups(now=now)

        assert deleted == 1
        assert not old_backup.exists()
        assert fresh_backup.exists()


@pytest.mark.asyncio
class TestBackupHooks:
    async def test_start_command_creates_backup_before_enabling(
        self, admin_user, admin_service, temp_db
    ):
        init_db()
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        bot_state_service.set_enabled(False)
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": bot_state_service,
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
            text="/start",
        )

        with patch("src.handlers.create_backup") as create_backup_mock:
            await dp.feed_update(bot, Update(update_id=1, message=message))

        create_backup_mock.assert_called_once_with("bot_start_command")
        assert bot_state_service.is_enabled() is True

    async def test_stop_command_creates_backup_before_disabling(
        self, admin_user, admin_service, temp_db
    ):
        init_db()
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        bot_state_service.set_enabled(True)
        dp.workflow_data.update(
            {
                "admin_service": admin_service,
                "bot_state_service": bot_state_service,
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
            text="/stop",
        )

        with patch("src.handlers.create_backup") as create_backup_mock:
            await dp.feed_update(bot, Update(update_id=1, message=message))

        create_backup_mock.assert_called_once_with("bot_stop_command")
        assert bot_state_service.is_enabled() is False

    async def test_close_poll_creates_daily_backup(self, temp_db):
        init_db()
        bot = AsyncMock(spec=Bot)
        bot.stop_poll = AsyncMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=10))
        bot.delete_message = AsyncMock()

        poll_service = PollService()
        poll_service._poll_data["daily-1"] = PollData(
            kind="regular",
            poll_name_snapshot="Пятница",
            chat_id=-1001234567890,
            poll_msg_id=1,
            info_msg_id=2,
            yes_voters=[],
            subs=[],
        )

        with patch("src.services.poll_service.create_backup") as create_backup_mock:
            await poll_service.close_poll(bot, "daily-1")

        create_backup_mock.assert_called_once_with("daily_poll_finalize")

    async def test_close_poll_creates_monthly_backup(self, temp_db):
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
        bot.stop_poll = AsyncMock()
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=10))

        poll_service = PollService()
        poll_service._poll_data["monthly-1"] = PollData(
            kind="monthly_subscription",
            poll_name_snapshot="monthly_subscription",
            chat_id=-1001234567890,
            poll_msg_id=1,
            options=["Hall A", "смотреть результат"],
            option_poll_names=["Hall A", None],
            monthly_votes={},
        )

        with patch("src.services.poll_service.create_backup") as create_backup_mock:
            await poll_service.close_poll(bot, "monthly-1")

        create_backup_mock.assert_called_once_with("monthly_poll_finalize")
