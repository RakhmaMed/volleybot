import sqlite3

import pytest

from src.db import (
    _connect,
    _create_current_schema,
    close_game,
    create_game,
    get_open_game_by_template_id,
    get_open_monthly_game,
    get_player_stats,
    get_poll_stats,
    get_poll_templates,
    get_single_game_income_stats,
    get_stats_summary,
    init_db,
    save_game_participants,
    save_monthly_vote,
    save_poll_template,
)


def _insert_player(player_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO players (id, name, fullname) VALUES (?, ?, ?)",
            (player_id, f"user{player_id}", f"User {player_id}"),
        )
        conn.commit()


def _create_closed_game_with_participants(
    *,
    poll_id: str,
    template_id: int,
    closed_at: str,
    cost_per_game_snapshot: int,
    participants: list[dict],
) -> None:
    for participant in participants:
        _insert_player(int(participant["player_id"]))
    create_game(
        poll_id=poll_id,
        kind="regular",
        status="closed",
        poll_template_id=template_id,
        poll_name_snapshot=f"Зал {template_id}",
        question_snapshot="Играем?",
        chat_id=1,
        poll_message_id=100,
        opened_at=closed_at,
        cost_per_game_snapshot=cost_per_game_snapshot,
    )
    close_game(poll_id, closed_at=closed_at, final_message_id=101)
    save_game_participants(poll_id, participants)


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
            game_columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
            assert "cost_per_game_snapshot" in game_columns

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

    def test_single_game_income_stats_aggregates_paid_games(self, temp_db):
        init_db()
        save_poll_template(
            {
                "name": "Понедельник",
                "message": "Игра",
                "cost": 150,
                "cost_per_game": 1500,
            }
        )
        save_poll_template(
            {
                "name": "Пятница",
                "message": "Игра",
                "cost": 150,
                "cost_per_game": 1500,
            }
        )
        monday, friday = get_poll_templates()

        _create_closed_game_with_participants(
            poll_id="mon-1",
            template_id=int(monday["id"]),
            closed_at="2026-03-02T10:00:00+00:00",
            cost_per_game_snapshot=1500,
            participants=[
                {
                    "player_id": 1,
                    "roster_bucket": "main",
                    "sort_order": 1,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                },
                {
                    "player_id": 2,
                    "roster_bucket": "main",
                    "sort_order": 2,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                },
                {
                    "player_id": 3,
                    "roster_bucket": "main",
                    "sort_order": 3,
                    "is_subscriber": True,
                    "charged_amount": 0,
                    "charge_source": "subscription",
                },
            ],
        )
        _create_closed_game_with_participants(
            poll_id="mon-2",
            template_id=int(monday["id"]),
            closed_at="2026-03-09T10:00:00+00:00",
            cost_per_game_snapshot=1500,
            participants=[
                {
                    "player_id": 4,
                    "roster_bucket": "reserve",
                    "sort_order": 1,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                },
                {
                    "player_id": 5,
                    "roster_bucket": "booked",
                    "sort_order": 2,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                },
            ],
        )
        _create_closed_game_with_participants(
            poll_id="fri-1",
            template_id=int(friday["id"]),
            closed_at="2026-03-06T10:00:00+00:00",
            cost_per_game_snapshot=1500,
            participants=[
                {
                    "player_id": 6,
                    "roster_bucket": "main",
                    "sort_order": 1,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                }
            ],
        )
        _create_closed_game_with_participants(
            poll_id="free-1",
            template_id=int(friday["id"]),
            closed_at="2026-03-13T10:00:00+00:00",
            cost_per_game_snapshot=0,
            participants=[
                {
                    "player_id": 7,
                    "roster_bucket": "main",
                    "sort_order": 1,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                }
            ],
        )

        stats = get_single_game_income_stats(months_back=3, before_month="2026-04")

        assert stats["global"]["games_count"] == 3
        assert stats["global"]["single_game_charges"] == 4
        assert stats["global"]["single_game_sum"] == 600
        assert stats["global"]["avg_income_per_game"] == 200
        monday_stats = stats["by_poll_template_id"][int(monday["id"])]
        assert monday_stats["games_count"] == 2
        assert monday_stats["single_game_charges"] == 3
        assert monday_stats["single_game_sum"] == 450
        assert monday_stats["avg_income_per_game"] == 225

    def test_single_game_income_stats_respects_before_month(self, temp_db):
        init_db()
        save_poll_template(
            {
                "name": "Пятница",
                "message": "Игра",
                "cost": 150,
                "cost_per_game": 1500,
            }
        )
        template = get_poll_templates()[0]

        _create_closed_game_with_participants(
            poll_id="old-game",
            template_id=int(template["id"]),
            closed_at="2026-03-06T10:00:00+00:00",
            cost_per_game_snapshot=1500,
            participants=[
                {
                    "player_id": 1,
                    "roster_bucket": "main",
                    "sort_order": 1,
                    "charged_amount": 150,
                    "charge_source": "single_game",
                }
            ],
        )
        _create_closed_game_with_participants(
            poll_id="target-month-game",
            template_id=int(template["id"]),
            closed_at="2026-04-03T10:00:00+00:00",
            cost_per_game_snapshot=1500,
            participants=[
                {
                    "player_id": 2,
                    "roster_bucket": "main",
                    "sort_order": 1,
                    "charged_amount": 300,
                    "charge_source": "single_game",
                }
            ],
        )

        stats = get_single_game_income_stats(months_back=3, before_month="2026-04")

        assert stats["global"]["games_count"] == 1
        assert stats["global"]["single_game_sum"] == 150

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
        assert templates[0].get("enabled", 1) == 1
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
        assert templates[0].get("enabled", 0) == 0

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

    def test_update_poll_template_by_id_allows_rename(self, temp_db):
        """Обновление по id должно переименовывать шаблон без создания дубля."""
        init_db()
        template_id = save_poll_template({"name": "Old", "message": "Msg 1"})

        updated_id = save_poll_template(
            {
                "id": template_id,
                "name": "New",
                "message": "Msg 2",
                "cost": 200,
                "cost_per_game": 2500,
                "enabled": 0,
            },
            match_by="id",
        )

        templates = get_poll_templates()
        assert updated_id == template_id
        assert len(templates) == 1
        assert templates[0]["name"] == "New"
        assert templates[0]["message"] == "Msg 2"
        assert templates[0]["cost"] == 200
        assert templates[0]["cost_per_game"] == 2500
        assert templates[0]["enabled"] == 0

    def test_update_poll_template_by_id_does_not_insert_missing_id(self, temp_db):
        """Обновление по неизвестному id не должно создавать новый шаблон."""
        init_db()

        result = save_poll_template(
            {"id": 999, "name": "Missing", "message": "Msg"},
            match_by="id",
        )

        assert result is None
        assert get_poll_templates() == []

    def test_update_poll_template_by_id_rejects_duplicate_name(self, temp_db):
        """Переименование по id в занятое имя должно быть отклонено."""
        init_db()
        first_id = save_poll_template({"name": "First", "message": "Msg 1"})
        second_id = save_poll_template({"name": "Second", "message": "Msg 2"})

        result = save_poll_template(
            {"id": second_id, "name": "First", "message": "Conflict"},
            match_by="id",
        )

        templates = get_poll_templates()
        assert result is None
        assert len(templates) == 2
        assert {template["name"] for template in templates} == {"First", "Second"}
        assert first_id != second_id

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

    def test_init_db_fails_on_legacy_poll_templates_schema(self, temp_db):
        """init_db падает на legacy-схеме poll_templates с monthly_cost."""
        with _connect() as conn:
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
                CREATE TABLE poll_subscriptions (
                    poll_template_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (poll_template_id, user_id)
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
                    poll_template_id INTEGER,
                    poll_name_snapshot TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE hall_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    poll_template_id INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(poll_template_id, month)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE games (
                    poll_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    poll_template_id INTEGER,
                    poll_name_snapshot TEXT NOT NULL,
                    question_snapshot TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    poll_message_id INTEGER NOT NULL,
                    info_message_id INTEGER,
                    final_message_id INTEGER,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    game_date TEXT,
                    place_snapshot TEXT,
                    cost_snapshot INTEGER NOT NULL DEFAULT 0,
                    cost_per_game_snapshot INTEGER NOT NULL DEFAULT 0,
                    options_json TEXT NOT NULL DEFAULT '[]',
                    option_poll_names_json TEXT NOT NULL DEFAULT '[]',
                    target_month_snapshot TEXT,
                    last_info_text TEXT NOT NULL DEFAULT '⏳ Идёт сбор голосов...',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE game_participants (
                    game_poll_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    roster_bucket TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    is_subscriber INTEGER NOT NULL DEFAULT 0,
                    charged_amount INTEGER NOT NULL DEFAULT 0,
                    charge_source TEXT NOT NULL DEFAULT 'none',
                    balance_before INTEGER,
                    balance_after INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (game_poll_id, player_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE monthly_poll_votes (
                    game_poll_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    option_ids_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (game_poll_id, player_id)
                )
                """
            )
            conn.commit()

        with pytest.raises(sqlite3.DatabaseError, match="unexpected columns"):
            init_db()

    def test_init_db_fails_on_legacy_games_snapshot_column(self, temp_db):
        """init_db падает, если games содержит monthly_cost_snapshot вместо актуального имени."""
        with _connect() as conn:
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
                    cost_per_game INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
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
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    description TEXT,
                    poll_template_id INTEGER,
                    poll_name_snapshot TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE hall_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    poll_template_id INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    paid_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(poll_template_id, month)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE games (
                    poll_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    poll_template_id INTEGER,
                    poll_name_snapshot TEXT NOT NULL,
                    question_snapshot TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    poll_message_id INTEGER NOT NULL,
                    info_message_id INTEGER,
                    final_message_id INTEGER,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    game_date TEXT,
                    place_snapshot TEXT,
                    cost_snapshot INTEGER NOT NULL DEFAULT 0,
                    monthly_cost_snapshot INTEGER NOT NULL DEFAULT 0,
                    options_json TEXT NOT NULL DEFAULT '[]',
                    option_poll_names_json TEXT NOT NULL DEFAULT '[]',
                    target_month_snapshot TEXT,
                    last_info_text TEXT NOT NULL DEFAULT '⏳ Идёт сбор голосов...',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE game_participants (
                    game_poll_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    roster_bucket TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    is_subscriber INTEGER NOT NULL DEFAULT 0,
                    charged_amount INTEGER NOT NULL DEFAULT 0,
                    charge_source TEXT NOT NULL DEFAULT 'none',
                    balance_before INTEGER,
                    balance_after INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (game_poll_id, player_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE monthly_poll_votes (
                    game_poll_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    option_ids_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (game_poll_id, player_id)
                )
                """
            )
            conn.commit()

        with pytest.raises(sqlite3.DatabaseError, match="games:"):
            init_db()

    def test_init_db_fails_when_poll_template_id_is_not_primary_key(self, temp_db):
        """init_db падает, если poll_templates.id не является primary key."""
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE poll_templates (
                    id INTEGER,
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
                    cost_per_game INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            _create_current_schema(conn)
            conn.commit()

        with pytest.raises(sqlite3.DatabaseError, match="poll_templates: primary key"):
            init_db()
