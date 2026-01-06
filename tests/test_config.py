"""Тесты для модуля config."""

# Импортируем config для проверки его структуры
from src import config
from src.config import PollSchedule


class TestConfigLoading:
    """Тесты для загрузки конфигурации."""

    def test_config_has_required_attributes(self):
        """Тест наличия обязательных атрибутов в конфигурации."""
        assert hasattr(config, "TOKEN")
        assert hasattr(config, "CHAT_ID")
        assert hasattr(config, "POLLS_SCHEDULE")
        assert hasattr(config, "WEBHOOK_PATH")
        assert hasattr(config, "WEBHOOK_PORT")
        assert hasattr(config, "REQUIRED_PLAYERS")
        assert hasattr(config, "POLL_OPTIONS")

    def test_config_token_is_string(self):
        """Тест типа токена."""
        assert isinstance(config.TOKEN, str)
        assert len(config.TOKEN) > 0

    def test_config_chat_id_is_int(self):
        """Тест типа chat_id."""
        assert isinstance(config.CHAT_ID, int)

    def test_config_webhook_secret_is_string(self):
        """Тест типа webhook_secret."""
        assert isinstance(config.WEBHOOK_SECRET, str)

    def test_config_polls_schedule_is_list(self):
        """Тест типа polls_schedule."""
        assert isinstance(config.POLLS_SCHEDULE, list)

    def test_config_required_players_is_int(self):
        """Тест типа required_players."""
        assert isinstance(config.REQUIRED_PLAYERS, int)
        assert config.REQUIRED_PLAYERS > 0

    def test_config_poll_options_is_tuple(self):
        """Тест типа poll_options."""
        assert isinstance(config.POLL_OPTIONS, tuple)
        assert len(config.POLL_OPTIONS) > 0

    def test_config_webhook_url_format(self):
        """Тест формата webhook URL."""
        if config.WEBHOOK_HOST:
            assert config.WEBHOOK_URL.startswith("http")
            assert config.WEBHOOK_PATH in config.WEBHOOK_URL
        else:
            assert config.WEBHOOK_URL == ""

    def test_config_polls_have_required_fields(self):
        """Тест наличия обязательных полей в опросах."""
        for poll in config.POLLS_SCHEDULE:
            assert isinstance(poll, PollSchedule)
            assert hasattr(poll, "name")
            assert hasattr(poll, "message")
            assert hasattr(poll, "open_day")
            assert hasattr(poll, "open_hour_utc")
            assert hasattr(poll, "open_minute_utc")
            assert hasattr(poll, "game_day")
            assert hasattr(poll, "game_hour_utc")
            assert hasattr(poll, "game_minute_utc")
            assert hasattr(poll, "subs")
