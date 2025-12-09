"""Тесты для модуля config."""

# Импортируем config для проверки его структуры
from src import config


class TestConfigLoading:
    """Тесты для загрузки конфигурации."""
    
    def test_config_has_required_attributes(self):
        """Тест наличия обязательных атрибутов в конфигурации."""
        assert hasattr(config, 'TOKEN')
        assert hasattr(config, 'CHAT_ID')
        assert hasattr(config, 'ADMIN_USERNAME')
        assert hasattr(config, 'POLLS_SCHEDULE')
        assert hasattr(config, 'WEBHOOK_PATH')
        assert hasattr(config, 'WEBHOOK_PORT')
        assert hasattr(config, 'REQUIRED_PLAYERS')
        assert hasattr(config, 'POLL_OPTIONS')
    
    def test_config_token_is_string(self):
        """Тест типа токена."""
        assert isinstance(config.TOKEN, str)
        assert len(config.TOKEN) > 0
    
    def test_config_chat_id_is_int(self):
        """Тест типа chat_id."""
        assert isinstance(config.CHAT_ID, int)
    
    def test_config_admin_username_is_string(self):
        """Тест типа admin_username."""
        assert isinstance(config.ADMIN_USERNAME, str)
        assert len(config.ADMIN_USERNAME) > 0
    
    def test_config_polls_schedule_is_list(self):
        """Тест типа polls_schedule."""
        assert isinstance(config.POLLS_SCHEDULE, list)
    
    def test_config_required_players_is_int(self):
        """Тест типа required_players."""
        assert isinstance(config.REQUIRED_PLAYERS, int)
        assert config.REQUIRED_PLAYERS > 0
    
    def test_config_poll_options_is_list(self):
        """Тест типа poll_options."""
        assert isinstance(config.POLL_OPTIONS, list)
        assert len(config.POLL_OPTIONS) > 0
    
    def test_config_webhook_url_format(self):
        """Тест формата webhook URL."""
        if config.WEBHOOK_HOST:
            assert config.WEBHOOK_URL.startswith('http')
            assert config.WEBHOOK_PATH in config.WEBHOOK_URL
        else:
            assert config.WEBHOOK_URL == ""
    
    def test_config_polls_have_required_fields(self):
        """Тест наличия обязательных полей в опросах."""
        for poll in config.POLLS_SCHEDULE:
            assert 'name' in poll
            assert 'message' in poll
            assert 'open_day' in poll
            assert 'open_hour_utc' in poll
            assert 'open_minute_utc' in poll
            assert 'close_day' in poll
            assert 'close_hour_utc' in poll
            assert 'close_minute_utc' in poll

