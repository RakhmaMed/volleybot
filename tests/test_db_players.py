import sqlite3
from unittest.mock import patch

import pytest
from aiogram.types import User

import src.utils
from src.db import (
    _connect,
    ensure_player,
    get_all_players,
    init_db,
    set_player_balance,
)
from src.utils import get_player_name, load_players


class TestDBPlayers:
    """Тесты для функций БД, связанных с игроками."""

    def test_init_db_creates_players_table(self, temp_db):
        """Проверка инициализации таблицы игроков."""
        init_db()
        with _connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='players'"
            )
            assert cursor.fetchone() is not None

    def test_ensure_player_creates_new(self, temp_db):
        """Проверка создания нового игрока."""
        init_db()
        ensure_player(user_id=123, name="test_user", fullname="Test User")

        players = get_all_players()
        assert len(players) == 1
        assert players[0]["id"] == 123
        assert players[0]["fullname"] == "Test User"

    def test_ensure_player_updates_existing(self, temp_db):
        """Проверка обновления существующего игрока."""
        init_db()
        ensure_player(user_id=123, name="old_name", fullname="Old Fullname")
        ensure_player(user_id=123, name="new_name", fullname="New Fullname")

        players = get_all_players()
        assert len(players) == 1
        assert players[0]["name"] == "new_name"
        assert players[0]["fullname"] == "New Fullname"

    def test_get_all_players_converts_ball_donate_to_bool(self, temp_db):
        """Проверка конвертации ball_donate из int (DB) в bool (Logic)."""
        init_db()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO players (id, name, fullname, ball_donate) VALUES (?, ?, ?, ?)",
                (1, "user", "User", 1),
            )
            conn.commit()

        players = get_all_players()
        assert players[0]["ball_donate"] is True

    def test_ensure_player_normalizes_username_with_spaces(self, temp_db):
        """Проверка нормализации username с пробелами (очищается)."""
        init_db()
        ensure_player(user_id=999, name="Invalid Username", fullname="Test User")

        players = get_all_players()
        assert len(players) == 1
        assert players[0]["id"] == 999
        assert players[0]["name"] is None  # должен быть очищен
        assert players[0]["fullname"] == "Test User"

    def test_ensure_player_keeps_valid_username(self, temp_db):
        """Проверка что валидный username сохраняется."""
        init_db()
        ensure_player(user_id=888, name="valid_username", fullname="Test User")

        players = get_all_players()
        assert len(players) == 1
        assert players[0]["id"] == 888
        assert players[0]["name"] == "valid_username"
        assert players[0]["fullname"] == "Test User"

    def test_ensure_player_normalizes_username_with_tabs(self, temp_db):
        """Проверка нормализации username с табуляцией (очищается)."""
        init_db()
        ensure_player(user_id=777, name="name\twith\ttabs", fullname="Tab User")

        players = get_all_players()
        assert len(players) == 1
        assert players[0]["name"] is None  # должен быть очищен


class TestLoadPlayersDB:
    """Тесты для функции load_players, теперь использующей БД."""

    def test_load_players_updates_global_cache(self, temp_db):
        """Проверка, что load_players загружает данные из БД в PLAYERS."""
        init_db()
        ensure_player(user_id=456, name="db_user", fullname="Database User")

        # Сбрасываем глобальную переменную перед тестом
        with patch("src.utils.PLAYERS", []):
            load_players()
            # Так как мы патчим в src.utils, проверяем там же
            assert any(p["id"] == 456 for p in src.utils.PLAYERS)

    def test_get_player_name_uses_db_data(self, temp_db):
        """Интеграционный тест: get_player_name использует данные, загруженные из БД."""
        init_db()
        user_id = 789
        ensure_player(user_id=user_id, name="bot_name", fullname="Real Name")

        # Загружаем из БД в кэш
        load_players()

        user = User(id=user_id, is_bot=False, first_name="TG_Name", username="tg_user")
        result = get_player_name(user)

        # Должно использовать fullname из БД ("Real Name") и username из объекта User
        assert "Real Name" in result
        assert "@tg_user" in result

    def test_load_players_handles_db_error(self, temp_db):
        """Проверка обработки ошибок БД при загрузке."""
        with patch("src.db.get_all_players", side_effect=sqlite3.Error("DB Error")):
            with patch("src.utils.PLAYERS", [{"id": 1}]):  # Старое значение
                load_players()
                # При ошибке список должен стать пустым
                assert src.utils.PLAYERS == []

    def test_ball_donate_emoji_from_db(self, temp_db):
        """Проверка появления эмодзи мяча на основе данных из БД."""
        init_db()
        user_id = 111
        with _connect() as conn:
            conn.execute(
                "INSERT INTO players (id, name, fullname, ball_donate) VALUES (?, ?, ?, ?)",
                (user_id, "donor", "Donor User", 1),
            )
            conn.commit()

        load_players()
        user = User(id=user_id, is_bot=False, first_name="Donor", username="donor_user")
        result = get_player_name(user)

        assert "🏐" in result
        assert "Donor User" in result
