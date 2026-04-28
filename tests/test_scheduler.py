"""Тесты для модуля scheduler."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.db import create_game, init_db
from src.scheduler import (
    create_close_poll_job,
    create_poll_job,
    refresh_scheduler,
    setup_scheduler,
)
from src.services import BotStateService, PollService


class FixedDatetime(datetime):
    fixed_now_utc = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.fixed_now_utc.replace(tzinfo=None)
        return cls.fixed_now_utc.astimezone(tz)


def _create_open_monthly_game(opened_at: str = "2026-04-27T19:00:00+00:00") -> None:
    create_game(
        poll_id="monthly-test",
        kind="monthly_subscription",
        status="open",
        poll_template_id=None,
        poll_name_snapshot="monthly_subscription",
        question_snapshot="Абонемент?",
        chat_id=-1001234567890,
        poll_message_id=20,
        opened_at=opened_at,
        options=["Пятница", "Смотреть результат"],
        option_poll_names=["Пятница", None],
        target_month_snapshot="2026-05",
    )


@pytest.mark.asyncio
class TestCreatePollJob:
    """Тесты для функции create_poll_job."""

    async def test_create_poll_job_calls_open_regular_poll(self):
        """Тест создания задачи отправки опроса."""
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        poll_service.open_regular_poll = AsyncMock(return_value=-1001234567890)

        job = create_poll_job(
            bot,
            bot_state_service,
            poll_service,
            poll_template_id=7,
        )

        await job()

        poll_service.open_regular_poll.assert_called_once_with(
            bot,
            -1001234567890,
            7,
            True,
        )

    async def test_create_poll_job_updates_chat_id_on_migration(self):
        """Тест обновления chat_id при миграции группы."""
        bot = MagicMock()
        new_chat_id = -1009876543210

        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        poll_service.open_regular_poll = AsyncMock(return_value=new_chat_id)

        job = create_poll_job(
            bot,
            bot_state_service,
            poll_service,
            poll_template_id=7,
        )

        await job()

        assert bot_state_service.get_chat_id() == new_chat_id

    async def test_create_monthly_poll_job_calls_service(self):
        """Месячный poll job должен использовать тот же сервисный путь открытия."""
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()
        poll_service.open_monthly_subscription_poll = AsyncMock(
            return_value=-1001234567890
        )

        job = create_poll_job(
            bot,
            bot_state_service,
            poll_service,
            monthly=True,
        )

        await job()

        poll_service.open_monthly_subscription_poll.assert_called_once_with(
            bot,
            -1001234567890,
            True,
        )


@pytest.mark.asyncio
class TestCreateClosePollJob:
    """Тесты для функции create_close_poll_job."""

    async def test_create_close_poll_job_calls_close_poll(self):
        """Тест создания задачи закрытия опроса."""
        bot = MagicMock()
        poll_service = PollService()

        # Мокаем метод close_poll
        poll_service.close_poll = AsyncMock()

        with patch(
            "src.scheduler.get_open_game_by_template_id",
            return_value={"poll_id": "test_poll_id"},
        ):
            job = create_close_poll_job(
                bot, poll_service, poll_template_id=1
            )

            await job()

        poll_service.close_poll.assert_called_once()
        assert poll_service.close_poll.call_args[0][1] == "test_poll_id"


class TestSetupScheduler:
    """Тесты для функции setup_scheduler."""

    def test_setup_scheduler_with_valid_templates(self, temp_db):
        """Тест настройки планировщика с шаблонами из БД."""
        init_db()
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        # Мокаем данные из БД
        test_polls = [
            {
                "name": "test_poll",
                "message": "Test message",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "tue",
                "game_hour_utc": 10,
                "game_minute_utc": 0,
                "subs": [],
                "enabled": 1,
            }
        ]

        with patch("src.scheduler.get_poll_templates", return_value=test_polls):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

            # Проверяем, что задачи добавлены
            jobs = scheduler.get_jobs()
            assert len(jobs) == 3  # Открытие, закрытие и очистка старых бэкапов

    def test_setup_scheduler_skips_disabled_templates(self, temp_db):
        """Планировщик не должен создавать jobs для выключенных шаблонов."""
        init_db()
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        test_polls = [
            {
                "name": "enabled_poll",
                "message": "Enabled",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "mon",
                "game_hour_utc": 12,
                "game_minute_utc": 0,
                "enabled": 1,
            },
            {
                "name": "disabled_poll",
                "message": "Disabled",
                "open_day": "tue",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "tue",
                "game_hour_utc": 12,
                "game_minute_utc": 0,
                "enabled": 0,
            },
        ]

        with patch("src.scheduler.get_poll_templates", return_value=test_polls):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

        jobs = scheduler.get_jobs()
        assert len(jobs) == 3
        job_names = {job.name for job in jobs}
        assert "enabled_poll (открытие)" in job_names
        assert "enabled_poll (закрытие)" in job_names
        assert "disabled_poll (открытие)" not in job_names
        assert "disabled_poll (закрытие)" not in job_names
        assert "Бэкапы (очистка)" in job_names

    def test_setup_scheduler_with_empty_db(self, temp_db):
        """Тест настройки планировщика при отсутствии опросов в БД."""
        init_db()
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        with patch("src.scheduler.get_poll_templates", return_value=[]):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

            jobs = scheduler.get_jobs()
            assert len(jobs) == 1
            assert jobs[0].name == "Бэкапы (очистка)"

    def test_setup_scheduler_restores_active_monthly_jobs_before_reminder(
        self, monkeypatch: pytest.MonkeyPatch, temp_db
    ):
        """После рестарта открытый месячный опрос получает reminder и close jobs."""
        init_db()
        _create_open_monthly_game()
        FixedDatetime.fixed_now_utc = datetime(
            2026, 4, 28, 12, 0, tzinfo=timezone.utc
        )
        monkeypatch.setattr("src.scheduler.datetime", FixedDatetime)

        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        with patch("src.scheduler.get_poll_templates", return_value=[]):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert "monthly_subs_reminder" in jobs
        assert "monthly_subs_close" in jobs
        assert "monthly_subs_open" not in jobs
        assert "monthly_subs_clear" not in jobs
        assert jobs["monthly_subs_reminder"].trigger.run_date == datetime(
            2026, 4, 28, 15, 0, tzinfo=ZoneInfo("UTC")
        )
        assert jobs["monthly_subs_close"].trigger.run_date == datetime(
            2026, 4, 28, 19, 0, tzinfo=ZoneInfo("UTC")
        )

    def test_setup_scheduler_restores_active_monthly_close_after_reminder(
        self, monkeypatch: pytest.MonkeyPatch, temp_db
    ):
        """Если 18:00 уже прошло, рестарт не дублирует reminder, но закрытие остаётся."""
        init_db()
        _create_open_monthly_game()
        FixedDatetime.fixed_now_utc = datetime(
            2026, 4, 28, 16, 0, tzinfo=timezone.utc
        )
        monkeypatch.setattr("src.scheduler.datetime", FixedDatetime)

        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        with patch("src.scheduler.get_poll_templates", return_value=[]):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert "monthly_subs_reminder" not in jobs
        assert jobs["monthly_subs_close"].trigger.run_date == datetime(
            2026, 4, 28, 19, 0, tzinfo=ZoneInfo("UTC")
        )

    def test_setup_scheduler_restores_overdue_active_monthly_close_immediately(
        self, monkeypatch: pytest.MonkeyPatch, temp_db
    ):
        """Если дедлайн прошёл во время простоя, закрытие ставится сразу после старта."""
        init_db()
        _create_open_monthly_game()
        FixedDatetime.fixed_now_utc = datetime(
            2026, 4, 28, 20, 0, tzinfo=timezone.utc
        )
        monkeypatch.setattr("src.scheduler.datetime", FixedDatetime)

        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        with patch("src.scheduler.get_poll_templates", return_value=[]):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

        jobs = {job.id: job for job in scheduler.get_jobs()}
        assert "monthly_subs_reminder" not in jobs
        assert jobs["monthly_subs_close"].trigger.run_date == datetime(
            2026, 4, 28, 20, 0, 1, tzinfo=ZoneInfo("UTC")
        )

    def test_setup_scheduler_poll_closure_timing(
        self, caplog: pytest.LogCaptureFixture, temp_db
    ):
        """Тест логики закрытия опроса за 30 минут до игры с учетом переходов через полночь/дни."""
        import logging

        init_db()
        caplog.set_level(logging.INFO)
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = BotStateService(default_chat_id=-1001234567890)
        poll_service = PollService()

        test_polls = [
            # 1. Обычное время: 18:00 -> 17:30
            {
                "name": "normal",
                "message": "msg",
                "open_day": "wed",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "wed",
                "game_hour_utc": 18,
                "game_minute_utc": 0,
                "enabled": 1,
            },
            # 2. Переход через полночь: 00:15 -> 23:45 (предыдущий день)
            {
                "name": "midnight_crossover",
                "message": "msg",
                "open_day": "mon",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "tue",
                "game_hour_utc": 0,
                "game_minute_utc": 15,
                "enabled": 1,
            },
            # 3. Переход через начало недели: Mon 00:20 -> Sun 23:50
            {
                "name": "week_crossover",
                "message": "msg",
                "open_day": "sat",
                "open_hour_utc": 10,
                "open_minute_utc": 0,
                "game_day": "mon",
                "game_hour_utc": 0,
                "game_minute_utc": 20,
                "enabled": 1,
            },
        ]

        with patch("src.scheduler.get_poll_templates", return_value=test_polls):
            setup_scheduler(scheduler, bot, bot_state_service, poll_service)

            # Проверяем через логи, так как они содержат вычисленное время закрытия
            log_text = caplog.text
            assert "ЗАКРЫТИЕ: WED 17:30 UTC (игра WED в 18:00) - normal" in log_text
            assert (
                "ЗАКРЫТИЕ: MON 23:45 UTC (игра TUE в 00:15) - midnight_crossover"
                in log_text
            )
            assert (
                "ЗАКРЫТИЕ: SUN 23:50 UTC (игра MON в 00:20) - week_crossover"
                in log_text
            )

            # Также проверим, что задачи действительно добавлены в планировщик
            jobs = {job.name: job for job in scheduler.get_jobs()}
            assert "normal (закрытие)" in jobs
            assert "midnight_crossover (закрытие)" in jobs
            assert "week_crossover (закрытие)" in jobs


class TestRefreshScheduler:
    """Регрессионные тесты для refresh_scheduler."""

    @patch("src.scheduler.get_poll_templates")
    async def test_refresh_removes_monthly_subs_jobs(
        self, mock_get_templates
    ):
        """Регрессия #2: refresh_scheduler должен удалять monthly_subs_* задачи.

        При отключении последнего платного опроса задачи monthly_subs_*
        не должны оставаться в планировщике.
        """
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = MagicMock(spec=BotStateService)
        poll_service = MagicMock(spec=PollService)

        # Добавляем фейковые monthly_subs_* задачи
        scheduler.add_job(
            lambda: None,
            "date",
            id="monthly_subs_open",
            name="Абонемент (открытие)",
            run_date="2030-01-01",
        )
        scheduler.add_job(
            lambda: None,
            "date",
            id="monthly_subs_close",
            name="Абонемент (закрытие)",
            run_date="2030-01-02",
        )

        # Ни одного платного опроса — monthly не должен быть запланирован
        mock_get_templates.return_value = [
            {"id": 1, "name": "Test", "cost": 0, "enabled": 1},
        ]
        scheduler.start()

        refresh_scheduler(scheduler, bot, bot_state_service, poll_service)

        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "monthly_subs_open" not in job_ids
        assert "monthly_subs_close" not in job_ids
        # backup_cleanup должен остаться
        assert "backup_cleanup" not in job_ids  # не добавлялся в этот scheduler

        scheduler.shutdown()

    @patch("src.scheduler.get_poll_templates")
    async def test_refresh_removes_poll_open_close_jobs(
        self, mock_get_templates
    ):
        """refresh_scheduler должен удалять poll_open_* и poll_close_* задачи."""
        scheduler = AsyncIOScheduler(timezone="UTC")
        bot = MagicMock()
        bot_state_service = MagicMock(spec=BotStateService)
        poll_service = MagicMock(spec=PollService)

        # Добавляем фейковые задачи
        scheduler.add_job(
            lambda: None,
            "date",
            id="poll_open_1",
            run_date="2030-01-01",
        )
        scheduler.add_job(
            lambda: None,
            "date",
            id="poll_close_1",
            run_date="2030-01-01",
        )

        mock_get_templates.return_value = []
        scheduler.start()

        refresh_scheduler(scheduler, bot, bot_state_service, poll_service)

        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "poll_open_1" not in job_ids
        assert "poll_close_1" not in job_ids

        scheduler.shutdown()
