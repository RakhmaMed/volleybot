"""Тесты для модуля scheduler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.scheduler import create_close_poll_job, create_poll_job, setup_scheduler


@pytest.mark.asyncio
class TestCreatePollJob:
    """Тесты для функции create_poll_job."""

    async def test_create_poll_job_calls_send_poll(self):
        """Тест создания задачи отправки опроса."""
        bot = MagicMock()
        chat_id = [-1001234567890]  # Используем список для изменения

        def get_chat_id():
            return chat_id[0]

        def set_chat_id(value: int):
            chat_id[0] = value

        def get_bot_enabled():
            return True

        with patch("src.scheduler.send_poll", new_callable=AsyncMock) as mock_send_poll:
            mock_send_poll.return_value = -1001234567890

            job = create_poll_job(
                bot,
                "Test message",
                "test_poll",
                get_chat_id,
                set_chat_id,
                get_bot_enabled,
            )

            await job()

            mock_send_poll.assert_called_once()
            assert mock_send_poll.call_args[0][1] == -1001234567890
            assert mock_send_poll.call_args[0][2] == "Test message"
            assert mock_send_poll.call_args[0][3] == "test_poll"

    async def test_create_poll_job_updates_chat_id_on_migration(self):
        """Тест обновления chat_id при миграции группы."""
        bot = MagicMock()
        chat_id = [-1001234567890]
        new_chat_id = -1009876543210

        def get_chat_id():
            return chat_id[0]

        def set_chat_id(value: int):
            chat_id[0] = value

        def get_bot_enabled():
            return True

        with patch("src.scheduler.send_poll", new_callable=AsyncMock) as mock_send_poll:
            mock_send_poll.return_value = new_chat_id

            job = create_poll_job(
                bot,
                "Test message",
                "test_poll",
                get_chat_id,
                set_chat_id,
                get_bot_enabled,
            )

            await job()

            assert chat_id[0] == new_chat_id


@pytest.mark.asyncio
class TestCreateClosePollJob:
    """Тесты для функции create_close_poll_job."""

    async def test_create_close_poll_job_calls_close_poll(self):
        """Тест создания задачи закрытия опроса."""
        bot = MagicMock()

        with patch(
            "src.scheduler.close_poll", new_callable=AsyncMock
        ) as mock_close_poll:
            job = create_close_poll_job(bot, "test_poll")

            await job()

            mock_close_poll.assert_called_once()
            assert mock_close_poll.call_args[0][1] == "test_poll"


class TestSetupScheduler:
    """Тесты для функции setup_scheduler."""

    def test_setup_scheduler_with_valid_config(self):
        """Тест настройки планировщика с валидной конфигурацией."""
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()

        def get_chat_id():
            return -1001234567890

        def set_chat_id(value: int):
            pass

        def get_bot_enabled():
            return True

        # Мокаем конфигурацию
        test_polls = [
            {
                "name": "test_poll",
                "message": "Test message",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "close_day": "tue",
                "close_hour_utc": 10,
                "close_minute_utc": 0,
            }
        ]

        with patch("src.scheduler.POLLS_SCHEDULE", test_polls):
            setup_scheduler(scheduler, bot, get_chat_id, set_chat_id, get_bot_enabled)

            # Проверяем, что задачи добавлены
            jobs = scheduler.get_jobs()
            assert len(jobs) == 2  # Одна задача открытия, одна закрытия

    def test_setup_scheduler_with_empty_config(self):
        """Тест настройки планировщика с пустой конфигурацией."""
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()

        def get_chat_id():
            return -1001234567890

        def set_chat_id(value: int):
            pass

        def get_bot_enabled():
            return True

        with patch("src.scheduler.POLLS_SCHEDULE", []):
            setup_scheduler(scheduler, bot, get_chat_id, set_chat_id, get_bot_enabled)

            jobs = scheduler.get_jobs()
            assert len(jobs) == 0

    def test_setup_scheduler_skips_poll_without_message(self):
        """Тест пропуска опроса без сообщения."""
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()

        def get_chat_id():
            return -1001234567890

        def set_chat_id(value: int):
            pass

        def get_bot_enabled():
            return True

        test_polls = [
            {
                "name": "test_poll",
                "message": "",  # Пустое сообщение
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "close_day": "tue",
                "close_hour_utc": 10,
                "close_minute_utc": 0,
            }
        ]

        with patch("src.scheduler.POLLS_SCHEDULE", test_polls):
            setup_scheduler(scheduler, bot, get_chat_id, set_chat_id, get_bot_enabled)

            jobs = scheduler.get_jobs()
            assert len(jobs) == 0  # Опрос должен быть пропущен
