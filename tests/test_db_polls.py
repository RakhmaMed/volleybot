import sqlite3

import pytest

from src.db import (
    _connect,
    close_game,
    create_game,
    get_open_game_by_template_id,
    get_open_monthly_game,
    get_poll_templates,
    get_player_stats,
    get_poll_stats,
    get_stats_summary,
    init_db,
    save_game_participants,
    save_monthly_vote,
    save_poll_template,
)


class TestDBPolls:
    """Тесты для функций БД, связанных с шаблонами опросов."""

    def test_init_db_creates_poll_tables(self, temp_db):
        """Проверка инициализации таблиц для шаблонов опросов и подписок."""
        init_db()
        with _connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('poll_templates', 'poll_subscriptions', 'games', 'game_participants', 'monthly_poll_votes')"
            )
            tables = [row[0] for row in cursor.fetchall()]
            assert "poll_templates" in tables
            assert "poll_subscriptions" in tables
            assert "games" in tables
            assert "game_participants" in tables
            assert "monthly_poll_votes" in tables
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(poll_templates)")
            }
            assert "id" in columns
            assert "enabled" in columns
            sub_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(poll_subscriptions)")
            }
            assert "poll_template_id" in sub_columns

    def test_create_and_close_game(self, temp_db):
        init_db()
        save_poll_template({"name": "Пятница", "message": "Игра"})
        template = get_poll_templates()[0]

        create_game(
            poll_id="poll-1",
            kind="regular",
            status="open",
            poll_template_id=int(template["id"]),
            poll_name_snapshot="Пятница",
            question_snapshot="Играем?",
            chat_id=1,
            poll_message_id=10,
            info_message_id=11,
            opened_at="2026-03-01T10:00:00+00:00",
        )
        game = get_open_game_by_template_id(int(template["id"]))
        assert game is not None
        assert game["poll_id"] == "poll-1"
        assert game["info_message_id"] == 11

        close_game(
            "poll-1",
            closed_at="2026-03-01T12:00:00+00:00",
            final_message_id=12,
        )
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, final_message_id FROM games WHERE poll_id = 'poll-1'"
            ).fetchone()
            assert row["status"] == "closed"
            assert row["final_message_id"] == 12

    def test_monthly_votes_and_stats(self, temp_db):
        init_db()
        save_poll_template({"name": "Пятница", "message": "Игра"})
        template = get_poll_templates()[0]
        with _connect() as conn:
            conn.execute(
                "INSERT INTO players (id, name, fullname, balance) VALUES (1, 'u1', 'User 1', -150)"
            )
            conn.commit()

        create_game(
            poll_id="monthly-1",
            kind="monthly_subscription",
            status="open",
            poll_template_id=None,
            poll_name_snapshot="monthly_subscription",
            question_snapshot="Абонемент?",
            chat_id=1,
            poll_message_id=20,
            opened_at="2026-03-01T10:00:00+00:00",
        )
        save_monthly_vote("monthly-1", 1, [0, 1])
        assert get_open_monthly_game() is not None

        create_game(
            poll_id="regular-1",
            kind="regular",
            status="closed",
            poll_template_id=int(template["id"]),
            poll_name_snapshot="Пятница",
            question_snapshot="Играем?",
            chat_id=1,
            poll_message_id=30,
            opened_at="2026-03-01T10:00:00+00:00",
        )
        close_game(
            "regular-1",
            closed_at="2026-03-02T10:00:00+00:00",
            final_message_id=31,
        )
        save_game_participants(
            "regular-1",
            [
                {
                    "player_id": 1,
                    "roster_bucket": "main",
                    "sort_order": 1,
                    "is_subscriber": False,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                    "balance_before": 0,
                    "balance_after": -150,
                }
            ],
        )

        summary = get_stats_summary("2026-03")
        poll_stats = get_poll_stats(int(template["id"]), "2026-03")
        player_stats = get_player_stats(1, "2026-03")
        assert summary["games_count"] == 1
        assert poll_stats["games_count"] == 1
        assert player_stats["games_total"] == 1

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
        assert templates[0]["enabled"] == 1
        assert "subs" in templates[0]
        assert set(templates[0]["subs"]) == {123, 456}

    def test_save_and_get_poll_templates_with_enabled_flag(self, temp_db):
        """Проверка сохранения признака enabled у шаблона."""
        init_db()
        template = {
            "name": "Disabled Poll",
            "message": "Disabled Message",
            "enabled": 0,
        }
        save_poll_template(template)

        templates = get_poll_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "Disabled Poll"
        assert templates[0]["enabled"] == 0

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
            assert template["cost_per_game"] == 0
            assert template["enabled"] == 1
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

    def test_migrates_v3_schema_to_v4_with_enabled_default(self, temp_db):
        """Схема v3 обновляется до v4 с enabled=1 для существующих записей."""
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    place TEXT,
                    message TEXT NOT NULL,
                    open_day TEXT NOT NULL DEFAULT '*',
                    open_hour_utc INTEGER NOT NULL DEFAULT 0,
                    open_minute_utc INTEGER NOT NULL DEFAULT 0,
                    game_day TEXT NOT NULL DEFAULT '*',
                    game_hour_utc INTEGER NOT NULL DEFAULT 0,
                    game_minute_utc INTEGER NOT NULL DEFAULT 0,
                    cost INTEGER NOT NULL DEFAULT 0,
                    monthly_cost INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE poll_subscriptions (
                    poll_template_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (poll_template_id, user_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO poll_templates (
                    name, message, open_day, open_hour_utc, open_minute_utc,
                    game_day, game_hour_utc, game_minute_utc, cost, monthly_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("Existing Poll", "Msg", "mon", 10, 0, "tue", 18, 0, 100, 4000),
            )
            conn.execute("PRAGMA user_version = 3")
            conn.commit()

        init_db()

        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(poll_templates)")
            }
            assert "enabled" in columns
            assert "cost_per_game" in columns
            assert "monthly_cost" not in columns

            row = conn.execute(
                "SELECT enabled, cost_per_game FROM poll_templates WHERE name = ?",
                ("Existing Poll",),
            ).fetchone()
            assert row is not None
            assert row["enabled"] == 1
            assert row["cost_per_game"] == 1500

    def test_migrates_v5_monthly_cost_to_cost_per_game(self, temp_db):
        """Схема v5 переносит monthly_cost в cost_per_game по правилу 1500/0."""
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    place TEXT,
                    message TEXT NOT NULL,
                    open_day TEXT NOT NULL DEFAULT '*',
                    open_hour_utc INTEGER NOT NULL DEFAULT 0,
                    open_minute_utc INTEGER NOT NULL DEFAULT 0,
                    game_day TEXT NOT NULL DEFAULT '*',
                    game_hour_utc INTEGER NOT NULL DEFAULT 0,
                    game_minute_utc INTEGER NOT NULL DEFAULT 0,
                    cost INTEGER NOT NULL DEFAULT 0,
                    monthly_cost INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                INSERT INTO poll_templates (
                    name, message, game_day, cost, monthly_cost
                ) VALUES
                    ('Paid Hall', 'Msg', 'mon', 150, 6000),
                    ('Free Hall', 'Msg', 'wed', 150, 0)
                """
            )
            conn.execute("PRAGMA user_version = 5")
            conn.commit()

        init_db()

        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(poll_templates)")
            }
            assert "cost_per_game" in columns
            assert "monthly_cost" not in columns

            rows = conn.execute(
                "SELECT name, cost_per_game FROM poll_templates ORDER BY id"
            ).fetchall()
            assert rows[0]["name"] == "Paid Hall"
            assert rows[0]["cost_per_game"] == 1500
            assert rows[1]["name"] == "Free Hall"
            assert rows[1]["cost_per_game"] == 0
