import sqlite3

import pytest

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
        with _connect() as conn:
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (?, ?, ?)",
                (123, "user123", "User 123"),
            )
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (?, ?, ?)",
                (456, "user456", "User 456"),
            )
            conn.commit()
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
        assert "subs" in templates[0]
        assert set(templates[0]["subs"]) == {123, 456}

    def test_update_poll_template(self, temp_db):
        """Проверка обновления существующего шаблона опроса."""
        init_db()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (?, ?, ?)",
                (1, "user1", "User 1"),
            )
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (?, ?, ?)",
                (2, "user2", "User 2"),
            )
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (?, ?, ?)",
                (3, "user3", "User 3"),
            )
            conn.commit()
        template1 = {"name": "Test", "message": "Msg 1", "subs": [1]}
        template2 = {"name": "Test", "message": "Msg 2", "subs": [2, 3]}

        save_poll_template(template1)
        save_poll_template(template2)

        templates = get_poll_templates()
        assert len(templates) == 1
        assert templates[0]["message"] == "Msg 2"
        assert "subs" in templates[0]
        assert set(templates[0]["subs"]) == {2, 3}

    def test_foreign_keys_enforced_for_subscriptions(self, temp_db):
        """Проверка, что FK реально enforced на runtime."""
        init_db()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (?, ?, ?)",
                (1, "user", "User"),
            )
            conn.commit()

            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO poll_subscriptions (poll_name, user_id) VALUES (?, ?)",
                    ("missing_poll", 1),
                )
                conn.commit()
