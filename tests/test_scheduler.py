"""Тесты для модуля scheduler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import ValidationError

from src.config import PollSchedule
from src.scheduler import create_close_poll_job, create_poll_job, setup_scheduler
from src.services import BotStateService, PollService


@pytest.mark.asyncio
class TestCreatePollJob:
    """Тесты для функции create_poll_job."""

    async def test_create_poll_job_calls_send_poll(self):
        """Тест создания задачи отправки опроса."""
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        # Мокаем метод send_poll
        poll_service.send_poll = AsyncMock(return_value=-1001234567890)

        job = create_poll_job(
            bot,
            "Test message",
            "test_poll",
            bot_state_service,
            poll_service,
        )

        await job()

        poll_service.send_poll.assert_called_once()
        assert poll_service.send_poll.call_args[0][1] == -1001234567890
        assert poll_service.send_poll.call_args[0][2] == "Test message"
        assert poll_service.send_poll.call_args[0][3] == "test_poll"

    async def test_create_poll_job_updates_chat_id_on_migration(self):
        """Тест обновления chat_id при миграции группы."""
        bot = MagicMock()
        new_chat_id = -1009876543210

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        # Мокаем метод send_poll для возврата нового chat_id
        poll_service.send_poll = AsyncMock(return_value=new_chat_id)

        job = create_poll_job(
            bot,
            "Test message",
            "test_poll",
            bot_state_service,
            poll_service,
        )

        await job()

        assert bot_state_service.get_chat_id() == new_chat_id


@pytest.mark.asyncio
class TestCreateClosePollJob:
    """Тесты для функции create_close_poll_job."""

    async def test_create_close_poll_job_calls_close_poll(self):
        """Тест создания задачи закрытия опроса."""
        bot = MagicMock()
        poll_service = PollService()

        # Мокаем метод close_poll
        poll_service.close_poll = AsyncMock()

        job = create_close_poll_job(bot, "test_poll", poll_service)

        await job()

        poll_service.close_poll.assert_called_once()
        assert poll_service.close_poll.call_args[0][1] == "test_poll"


class TestSetupScheduler:
    """Тесты для функции setup_scheduler."""

    def test_setup_scheduler_with_valid_config(self):
        """Тест настройки планировщика с валидной конфигурацией."""
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        # Мокаем конфигурацию
        test_polls = [
            PollSchedule(
                name="test_poll",
                message="Test message",
                open_day="mon",
                open_hour_utc=10,
                open_minute_utc=0,
                close_day="tue",
                close_hour_utc=10,
                close_minute_utc=0,
            )
        ]

        with patch("src.scheduler.POLLS_SCHEDULE", test_polls):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

            # Проверяем, что задачи добавлены
            jobs = scheduler.get_jobs()
            assert len(jobs) == 2  # Одна задача открытия, одна закрытия

    def test_setup_scheduler_with_empty_config(self):
        """Тест настройки планировщика с пустой конфигурацией."""
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        with patch("src.scheduler.POLLS_SCHEDULE", []):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

            jobs = scheduler.get_jobs()
            assert len(jobs) == 0

    def test_setup_scheduler_skips_poll_without_message(self):
        """Тест пропуска опроса без сообщения."""
        # С Pydantic validation, пустое message не пройдет валидацию
        # Поэтому этот тест проверяет что валидация работает корректно
        with pytest.raises(ValidationError):
            PollSchedule(
                name="test_poll",
                message="",  # Пустое сообщение не пройдет валидацию (min_length=1)
                open_day="mon",
                open_hour_utc=10,
                open_minute_utc=0,
                close_day="tue",
                close_hour_utc=10,
                close_minute_utc=0,
            )
