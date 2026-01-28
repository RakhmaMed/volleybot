import sqlite3

from src.db import (
    _connect,
    get_poll_templates,
    init_db,
    save_poll_template,
)


class TestDBPolls:
    """Тесты для функций БД, связанных с шаблонами опросов."""

    def test_init_db_creates_poll_tables(self, temp_db):
        """Проверка инициализации таблиц для шаблонов опросов и подписок."""
        init_db()
        with _connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('poll_templates', 'poll_subscriptions')"
            )
            tables = [row[0] for row in cursor.fetchall()]
            assert "poll_templates" in tables
            assert "poll_subscriptions" in tables

    def test_save_and_get_poll_templates(self, temp_db):
        """Проверка сохранения и получения шаблона опроса с подписчиками."""
        init_db()
        template = {
            "name": "Test Poll",
            "message": "Test Message",
            "open_day": "mon",
            "open_hour_utc": 10,
            "open_minute_utc": 0,
            "game_day": "tue",
            "game_hour_utc": 18,
            "game_minute_utc": 30,
            "subs": [123, 456],
        }
        save_poll_template(template)

        templates = get_poll_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "Test Poll"
        assert templates[0]["message"] == "Test Message"
        assert templates[0]["open_day"] == "mon"
        assert set(templates[0]["subs"]) == {123, 456}

    def test_update_poll_template(self, temp_db):
        """Проверка обновления существующего шаблона опроса."""
        init_db()
        template1 = {"name": "Test", "message": "Msg 1", "subs": [1]}
        template2 = {"name": "Test", "message": "Msg 2", "subs": [2, 3]}

        save_poll_template(template1)
        save_poll_template(template2)

        templates = get_poll_templates()
        assert len(templates) == 1
        assert templates[0]["message"] == "Msg 2"
        assert set(templates[0]["subs"]) == {2, 3}
