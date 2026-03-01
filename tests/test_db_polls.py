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
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(poll_templates)")
            }
            assert "id" in columns
            sub_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(poll_subscriptions)")
            }
            assert "poll_template_id" in sub_columns

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
        assert isinstance(templates[0]["id"], int)
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
                    "INSERT INTO poll_subscriptions (poll_template_id, user_id) VALUES (?, ?)",
                    (999, 1),
                )
                conn.commit()

    def test_migrates_legacy_poll_schema(self, temp_db):
        """Legacy-схема мигрируется на v3 без потери данных."""
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    fullname TEXT,
                    ball_donate INTEGER DEFAULT 0,
                    balance INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE poll_templates (
                    name TEXT PRIMARY KEY,
                    place TEXT,
                    message TEXT NOT NULL,
                    open_day TEXT,
                    open_hour_utc INTEGER,
                    open_minute_utc INTEGER,
                    game_day TEXT,
                    game_hour_utc INTEGER,
                    game_minute_utc INTEGER,
                    cost INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE poll_subscriptions (
                    poll_name TEXT,
                    user_id INTEGER,
                    PRIMARY KEY (poll_name, user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    description TEXT,
                    poll_name TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE hall_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    poll_name TEXT NOT NULL,
                    month TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(poll_name, month)
                )
                """
            )
            conn.execute(
                "INSERT INTO players (id, name, fullname) VALUES (1, 'user1', 'User 1')"
            )
            conn.execute(
                """
                INSERT INTO poll_templates (
                    name, place, message, open_day, open_hour_utc, open_minute_utc,
                    game_day, game_hour_utc, game_minute_utc, cost, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("Legacy Poll", "Hall", "Msg", "mon", 10, 5, "tue", 18, 30, 150, "2026-02-01 12:00:00"),
            )
            conn.execute(
                "INSERT INTO poll_subscriptions (poll_name, user_id) VALUES (?, ?)",
                ("Legacy Poll", 1),
            )
            conn.execute(
                """
                INSERT INTO transactions (player_id, amount, description, poll_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, -150, "Legacy charge", "Legacy Poll", "2026-02-10 10:00:00"),
            )
            conn.execute(
                """
                INSERT INTO hall_payments (poll_name, month, amount, paid_at)
                VALUES (?, ?, ?, ?)
                """,
                ("Legacy Poll", "2026-02", 6000, "2026-02-11 11:00:00"),
            )
            conn.commit()

        init_db()

        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            template = conn.execute(
                "SELECT * FROM poll_templates WHERE name = ?", ("Legacy Poll",)
            ).fetchone()
            assert template is not None
            assert template["id"] is not None
            assert template["monthly_cost"] == 0
            assert template["created_at"] == "2026-02-01 12:00:00"

            subscription = conn.execute(
                "SELECT * FROM poll_subscriptions WHERE user_id = 1"
            ).fetchone()
            assert subscription is not None
            assert subscription["poll_template_id"] == template["id"]

            payment = conn.execute("SELECT * FROM hall_payments").fetchone()
            assert payment is not None
            assert payment["poll_template_id"] == template["id"]

            tx = conn.execute("SELECT * FROM transactions").fetchone()
            assert tx is not None
            assert tx["poll_template_id"] == template["id"]
            assert tx["poll_name_snapshot"] == "Legacy Poll"
