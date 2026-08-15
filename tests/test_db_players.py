from aiogram.types import User
from returns.result import Failure

from src.db2 import (
    DB,
    ensure_player,
    get_all_players,
    get_player_info,
    toggle_player_ball_donate,
)
from src.utils import get_player_name


class TestDBPlayers:
    """Тесты для функций БД, связанных с игроками."""

    def test_init_db_creates_players_table(self, test_db: DB):
        """Проверка инициализации таблицы игроков."""
        cursor = test_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='players'"
        )
        assert cursor.fetchone() is not None

    def test_ensure_player_creates_new(self, test_db: DB):
        """Проверка создания нового игрока."""
        ensure_player(test_db, user_id=123, name="test_user", fullname="Test User").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["id"] == 123
        assert players[0]["fullname"] == "Test User"

    def test_ensure_player_updates_username_preserves_fullname(self, test_db: DB):
        """
        Проверка что Telegram username актуализируется, а fullname сохраняется.

        Важно: это защищает от случайной перезаписи вручную установленных имён,
        но не держит устаревший username после смены в Telegram.
        """
        # Первый вызов: создаём игрока с начальными данными
        ensure_player(test_db, user_id=123, name="original_name", fullname="Original Fullname").unwrap()

        # Второй вызов: пытаемся изменить (например, из Telegram пришли новые данные)
        ensure_player(test_db, user_id=123, name="new_name", fullname="New Fullname").unwrap()

        # Проверяем: username обновился, а отображаемое имя осталось прежним
        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] == "new_name", "Name должен обновиться"
        assert players[0]["fullname"] == "Original Fullname", (
            "Fullname должен остаться оригинальным"
        )

    def test_ensure_player_fills_null_name(self, test_db: DB):
        """
        Проверка что NULL поля заполняются новыми данными.

        Если в БД name=NULL, а приходит новое значение - оно должно записаться.
        """
        # Создаём игрока без name (NULL)
        ensure_player(test_db, user_id=200, name=None, fullname="User With No Name").unwrap()

        # Пытаемся обновить: добавляем name
        ensure_player(test_db, user_id=200, name="added_name", fullname="New Fullname").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] == "added_name", "NULL name должен заполниться"
        assert players[0]["fullname"] == "User With No Name", (
            "Существующий fullname должен сохраниться"
        )

    def test_ensure_player_fills_null_fullname(self, test_db: DB):
        """
        Проверка что NULL fullname заполняется новыми данными.
        """
        # Создаём игрока без fullname (NULL)
        ensure_player(test_db, user_id=201, name="user_name", fullname=None).unwrap()

        # Пытаемся обновить: добавляем fullname
        ensure_player(test_db, user_id=201, name="new_name", fullname="Added Fullname").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] == "new_name", "Существующий name должен обновиться"
        assert players[0]["fullname"] == "Added Fullname", (
            "NULL fullname должен заполниться"
        )

    def test_ensure_player_null_update_preserves_data(self, test_db: DB):
        """
        Проверка что передача NULL не затирает существующие данные.

        Если в БД есть данные, а мы передаём NULL - данные должны сохраниться.
        """
        # Создаём игрока с полными данными
        ensure_player(test_db, user_id=202, name="existing_name", fullname="Existing Fullname").unwrap()

        # Пытаемся обновить с NULL значениями
        ensure_player(test_db, user_id=202, name=None, fullname=None).unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] == "existing_name", "Name не должен затереться NULL"
        assert players[0]["fullname"] == "Existing Fullname", (
            "Fullname не должен затереться NULL"
        )

    def test_ensure_player_partial_update(self, test_db: DB):
        """
        Проверка частичного обновления: одно поле NULL, другое нет.
        """
        # Создаём игрока: name есть, fullname=NULL
        ensure_player(test_db, user_id=203, name="user123", fullname=None).unwrap()

        # Обновляем: пытаемся изменить name и добавить fullname
        ensure_player(test_db, user_id=203, name="new_user123", fullname="New Name").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] == "new_user123", "Существующий name должен обновиться"
        assert players[0]["fullname"] == "New Name", "NULL fullname должен заполниться"

    def test_ensure_player_invalid_username_preserves_existing_name(self, test_db: DB):
        """Невалидный username не должен затирать сохранённый username."""
        ensure_player(test_db, user_id=204, name="valid_user", fullname="Valid User").unwrap()

        ensure_player(test_db, user_id=204, name="Invalid Username", fullname=None).unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] == "valid_user"

    def test_ensure_player_real_world_scenario(self, test_db: DB):
        """
        Реальный сценарий: вручную установленное имя не должно затираться.

        1. Пользователь впервые взаимодействует с ботом → данные из Telegram
        2. Админ вручную меняет fullname на красивое имя
        3. Пользователь снова голосует → данные из Telegram НЕ должны перезаписать
        """

        # 1. Первое взаимодействие: сохраняем данные из Telegram
        ensure_player(
            test_db,
            user_id=5013132836, name="TwinkleDev55", fullname="Что-то хорошее есть"
        ).unwrap()

        # 2. Админ вручную меняет fullname (через UPDATE)
        test_db.conn.execute(
            "UPDATE players SET fullname = ? WHERE id = ?", ("Рахма", 5013132836)
        )
        test_db.conn.commit()

        # 3. Пользователь снова голосует: Telegram передаёт старое имя
        ensure_player(
            test_db,
            user_id=5013132836, name="TwinkleDev55", fullname="Что-то хорошее есть"
        ).unwrap()

        # Проверяем: должно остаться вручную установленное имя "Рахма"
        players = get_all_players(test_db).unwrap()
        player = next((p for p in players if p["id"] == 5013132836), None)
        assert player is not None
        assert player["fullname"] == "Рахма", (
            "Вручную установленное имя должно сохраниться!"
        )

    def test_get_all_players_converts_ball_donate_to_bool(self, test_db: DB):
        """Проверка конвертации ball_donate из int (DB) в bool (Logic)."""
        test_db.conn.execute(
            "INSERT INTO players (id, name, fullname, ball_donate) VALUES (?, ?, ?, ?)",
            (1, "user", "User", 1),
        )
        test_db.conn.commit()

        players = get_all_players(test_db).unwrap()
        assert players[0]["ball_donate"] is True

    def test_ensure_player_normalizes_username_with_spaces(self, test_db: DB):
        """Проверка нормализации username с пробелами (очищается)."""
        ensure_player(test_db, user_id=999, name="Invalid Username", fullname="Test User").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["id"] == 999
        assert players[0]["name"] is None  # должен быть очищен
        assert players[0]["fullname"] == "Test User"

    def test_ensure_player_keeps_valid_username(self, test_db: DB):
        """Проверка что валидный username сохраняется."""
        ensure_player(test_db, user_id=888, name="valid_username", fullname="Test User").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["id"] == 888
        assert players[0]["name"] == "valid_username"
        assert players[0]["fullname"] == "Test User"

    def test_ensure_player_normalizes_username_with_tabs(self, test_db: DB):
        """Проверка нормализации username с табуляцией (очищается)."""
        ensure_player(test_db, user_id=777, name="name\twith\ttabs", fullname="Tab User").unwrap()

        players = get_all_players(test_db).unwrap()
        assert len(players) == 1
        assert players[0]["name"] is None  # должен быть очищен


class TestGetPlayerInfo:
    """Тесты для get_player_info."""

    def test_get_player_info_returns_full_data(self, test_db: DB):
        """get_player_info возвращает id, name, fullname, ball_donate (bool), balance."""
        test_db.conn.execute(
            "INSERT INTO players (id, name, fullname, ball_donate, balance) VALUES (?, ?, ?, ?, ?)",
            (42, "player42", "Full Name", 1, 300),
        )
        test_db.conn.commit()

        info = get_player_info(test_db, 42).unwrap()
        assert info["id"] == 42
        assert info["name"] == "player42"
        assert info["fullname"] == "Full Name"
        assert info["ball_donate"] is True
        assert info["balance"] == 300

    def test_get_player_info_ball_donate_false(self, test_db: DB):
        """ball_donate=0 в БД преобразуется в False."""
        ensure_player(test_db, user_id=42, name="player42", fullname="Full Name").unwrap()
        test_db.conn.execute(
            "INSERT INTO players (id, name, fullname, ball_donate, balance) VALUES (?, ?, ?, ?, ?)",
            (43, "u", "U", 0, 0),
        )
        test_db.conn.commit()

        info = get_player_info(test_db, 43).unwrap()
        assert info["ball_donate"] is False

    def test_get_player_info_returns_none_for_missing(self, test_db: DB):
        """get_player_info возвращает None для несуществующего игрока."""
        assert get_player_info(test_db, 99999) == Failure("Player not found")


class TestTogglePlayerBallDonate:
    """Тесты для toggle_player_ball_donate."""

    def test_toggle_player_ball_donate_switches_false_to_true(self, test_db: DB):
        test_db.conn.execute(
            "INSERT INTO players (id, name, fullname, ball_donate) VALUES (?, ?, ?, ?)",
            (55, "toggle_user", "Toggle User", 0),
        )
        test_db.conn.commit()

        result = toggle_player_ball_donate(test_db, 55).unwrap()

        assert result is True
        info = get_player_info(test_db, 55).unwrap()
        assert info is not None
        assert info["ball_donate"] is True

    def test_toggle_player_ball_donate_switches_true_to_false(self, test_db: DB):
        test_db.conn.execute(
            "INSERT INTO players (id, name, fullname, ball_donate) VALUES (?, ?, ?, ?)",
            (56, "toggle_user_2", "Toggle User 2", 1),
        )
        test_db.conn.commit()

        result = toggle_player_ball_donate(test_db, 56).unwrap()

        assert result is False
        info = get_player_info(test_db, 56).unwrap()
        assert info is not None
        assert info["ball_donate"] is False

    def test_toggle_player_ball_donate_returns_none_for_missing_player(self, test_db: DB):
        result = toggle_player_ball_donate(test_db, 99999)
        assert result == Failure("Player not found")


class TestGetPlayerNameFromDB:
    """Интеграционные тесты: get_player_name получает актуальные данные из БД."""

    def test_get_player_name_uses_db_data(self, test_db: DB):
        """Интеграционный тест: get_player_name использует данные из БД."""
        user_id = 789
        ensure_player(test_db, user_id=user_id, name="bot_name", fullname="Real Name").unwrap()

        user = User(id=user_id, is_bot=False, first_name="TG_Name", username="tg_user")
        result = get_player_name(user)

        # Должно использовать fullname из БД ("Real Name") и username из объекта User
        assert "Real Name" in result
        assert "@tg_user" in result

    def test_ball_donate_emoji_from_db(self, test_db: DB):
        """Проверка появления эмодзи мяча на основе данных из БД."""
        user_id = 111
        test_db.conn.execute(
            "INSERT INTO players (id, name, fullname, ball_donate) VALUES (?, ?, ?, ?)",
            (user_id, "donor", "Donor User", 1),
        )
        test_db.conn.commit()

        user = User(id=user_id, is_bot=False, first_name="Donor", username="donor_user")
        result = get_player_name(user)

        assert "🏐" in result
        assert "Donor User" in result

    def test_subscription_emoji_from_subs_list(self, test_db: DB):
        """Проверка появления эмодзи звезды для подписчиков."""
        user_id = 222
        ensure_player(test_db, user_id=user_id, name="subscribed", fullname="Sub User").unwrap()

        user = User(id=user_id, is_bot=False, first_name="Sub", username="sub_user")
        result = get_player_name(user, subs=[user_id])

        assert "⭐" in result
        assert "Sub User" in result
