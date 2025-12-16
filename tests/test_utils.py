"""Тесты для модуля utils."""

import json
from pathlib import Path
from unittest.mock import patch

from aiogram.types import User

from src.utils import escape_html, get_player_name, is_admin, save_error_dump


class TestIsAdmin:
    """Тесты для функции is_admin."""

    def test_is_admin_with_correct_username(self):
        """Тест проверки администратора с правильным username."""
        user = User(id=123, is_bot=False, first_name="Test", username="TwinkleDev55")
        assert is_admin(user) is True

    def test_is_admin_with_username_with_at(self):
        """Тест проверки администратора с @ в username."""
        user = User(id=123, is_bot=False, first_name="Test", username="@TwinkleDev55")
        assert is_admin(user) is True

    def test_is_admin_with_wrong_username(self):
        """Тест проверки администратора с неправильным username."""
        user = User(id=123, is_bot=False, first_name="Test", username="wrong_user")
        assert is_admin(user) is False

    def test_is_admin_without_username(self):
        """Тест проверки администратора без username."""
        user = User(id=123, is_bot=False, first_name="Test", username=None)
        assert is_admin(user) is False

    def test_is_admin_case_sensitive(self):
        """Тест проверки администратора с учётом регистра."""
        user = User(
            id=123,
            is_bot=False,
            first_name="Test",
            username="twinkledev55",  # lowercase
        )
        assert is_admin(user) is False


class TestSaveErrorDump:
    """Тесты для функции save_error_dump."""

    def test_save_error_dump_creates_file(self, tmp_path: Path):
        """Тест создания файла дампа ошибки."""
        error_file = tmp_path / "error_dump.json"
        test_error = ValueError("Test error message")

        with patch("src.utils.os.path.dirname", return_value=str(tmp_path)):
            with patch("src.utils.os.path.join", return_value=str(error_file)):
                save_error_dump(
                    test_error, "test_poll", "Test question", -1001234567890
                )

        assert error_file.exists()

        with open(error_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["error_type"] == "ValueError"
        assert data[0]["error_message"] == "Test error message"
        assert data[0]["poll_name"] == "test_poll"
        assert data[0]["question"] == "Test question"
        assert data[0]["chat_id"] == -1001234567890
        assert "timestamp" in data[0]
        assert "traceback" in data[0]


class TestEscapeHtml:
    """Тесты для функции escape_html."""

    def test_escape_html_no_special_chars(self):
        """Текст без спецсимволов не должен изменяться."""
        assert escape_html("simple text @user") == "simple text @user"

    def test_escape_html_amp(self):
        """Символ & должен экранироваться первым."""
        assert escape_html("A & B") == "A &amp; B"

    def test_escape_html_angle_brackets(self):
        """Угловые скобки должны экранироваться."""
        assert escape_html("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"

    def test_escape_html_mixed(self):
        """Комбинация спецсимволов экранируется корректно."""
        assert escape_html("1 < 2 & 3 > 2") == "1 &lt; 2 &amp; 3 &gt; 2"

    def test_save_error_dump_appends_to_existing_file(self, tmp_path: Path):
        """Тест добавления ошибки в существующий файл."""
        error_file = tmp_path / "error_dump.json"

        # Создаём существующий файл с одной ошибкой
        existing_data = [
            {
                "timestamp": "2024-01-01T00:00:00",
                "error_type": "OldError",
                "error_message": "Old error",
            }
        ]
        with open(error_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f)

        test_error = ValueError("New error")

        with patch("src.utils.os.path.dirname", return_value=str(tmp_path)):
            with patch("src.utils.os.path.join", return_value=str(error_file)):
                save_error_dump(test_error, "new_poll", "New question", -1001234567890)

        with open(error_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 2
        assert data[0]["error_type"] == "OldError"
        assert data[1]["error_type"] == "ValueError"

    def test_save_error_dump_limits_to_50_entries(self, tmp_path: Path):
        """Тест ограничения количества записей до 50."""
        error_file = tmp_path / "error_dump.json"

        # Создаём файл с 60 ошибками
        existing_data = [{"error_type": f"Error{i}"} for i in range(60)]
        with open(error_file, "w", encoding="utf-8") as f:
            json.dump(existing_data, f)

        test_error = ValueError("New error")

        with patch("src.utils.os.path.dirname", return_value=str(tmp_path)):
            with patch("src.utils.os.path.join", return_value=str(error_file)):
                save_error_dump(
                    test_error, "test_poll", "Test question", -1001234567890
                )

        with open(error_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Должно остаться только последние 50 записей
        assert len(data) == 50
        assert data[-1]["error_type"] == "ValueError"

    def test_save_error_dump_handles_corrupted_file(self, tmp_path: Path):
        """Тест обработки повреждённого файла."""
        error_file = tmp_path / "error_dump.json"

        # Создаём повреждённый JSON файл
        with open(error_file, "w", encoding="utf-8") as f:
            f.write("invalid json content {")

        test_error = ValueError("Test error")

        with patch("src.utils.os.path.dirname", return_value=str(tmp_path)):
            with patch("src.utils.os.path.join", return_value=str(error_file)):
                save_error_dump(
                    test_error, "test_poll", "Test question", -1001234567890
                )

        # Файл должен быть перезаписан с новой ошибкой
        with open(error_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        assert data[0]["error_type"] == "ValueError"


class TestGetPlayerName:
    """Тесты для функции get_player_name."""

    def test_get_player_name_with_fullname_in_players(self):
        """Пользователь с fullname в players.json показывает имя и @username."""
        user = User(
            id=185633965,  # ID из players.json с fullname "Кисик"
            is_bot=False,
            first_name="Test",
            username="kkiiissik",
        )

        # Мокаем PLAYERS
        with patch(
            "src.utils.PLAYERS",
            [{"id": 185633965, "name": "kkiiissik", "fullname": "Кисик"}],
        ):
            result = get_player_name(user)

        assert result == "Кисик (@kkiiissik)"

    def test_get_player_name_without_fullname_no_duplicate(self):
        """Пользователь без fullname - @username НЕ дублируется."""
        user = User(
            id=454205863, is_bot=False, first_name="Test", username="what_goes_around"
        )

        # Мокаем PLAYERS без fullname
        with patch(
            "src.utils.PLAYERS", [{"id": 454205863, "name": "what_goes_around"}]
        ):
            result = get_player_name(user)

        # Не должно быть дубликата: "@username (@username)"
        assert result == "@what_goes_around"

    def test_get_player_name_not_in_players_with_username(self):
        """Пользователь не в players.json, но с username."""
        user = User(id=999999999, is_bot=False, first_name="Test", username="new_user")

        with patch("src.utils.PLAYERS", []):
            result = get_player_name(user)

        # display_name = @new_user, username_mention = @new_user -> не дублируем
        assert result == "@new_user"

    def test_get_player_name_without_username(self):
        """Пользователь без username использует full_name из Telegram."""
        user = User(
            id=999999999,
            is_bot=False,
            first_name="Иван",
            last_name="Петров",
            username=None,
        )

        with patch("src.utils.PLAYERS", []):
            result = get_player_name(user)

        assert result == "Иван Петров"

    def test_get_player_name_without_username_no_fullname(self):
        """Пользователь без username и без last_name."""
        user = User(id=999999999, is_bot=False, first_name="Иван", username=None)

        with patch("src.utils.PLAYERS", []):
            result = get_player_name(user)

        assert result == "Иван"

    def test_get_player_name_escapes_html_in_fullname(self):
        """HTML-символы в fullname экранируются."""
        user = User(id=123, is_bot=False, first_name="Test", username="testuser")

        with patch("src.utils.PLAYERS", [{"id": 123, "fullname": "Имя <script>"}]):
            result = get_player_name(user)

        assert result == "Имя &lt;script&gt; (@testuser)"

    def test_get_player_name_escapes_html_without_username(self):
        """HTML-символы экранируются для пользователя без username."""
        user = User(id=999999999, is_bot=False, first_name="Test<>", username=None)

        with patch("src.utils.PLAYERS", []):
            result = get_player_name(user)

        assert result == "Test&lt;&gt;"

    def test_get_player_name_empty_fullname_uses_telegram_name(self):
        """Пустой fullname в players.json - используется имя из Telegram."""
        user = User(
            id=123, is_bot=False, first_name="TelegramName", username="testuser"
        )

        with patch(
            "src.utils.PLAYERS",
            [
                {"id": 123, "fullname": "   "}  # Пустой fullname (только пробелы)
            ],
        ):
            result = get_player_name(user)

        # fullname пустой -> display_name = @testuser -> не дублируем
        assert result == "@testuser"

    def test_get_player_name_special_chars_no_escape_needed(self):
        """Точки и подчёркивания не требуют экранирования."""
        user = User(id=123, is_bot=False, first_name=". .", username=None)

        with patch("src.utils.PLAYERS", []):
            result = get_player_name(user)

        # Точки не экранируются в HTML
        assert result == ". ."

    def test_get_player_name_with_ball_donate(self):
        """Игрок с донатом мячей получает эмодзи волейбольного мяча."""
        user = User(id=123, is_bot=False, first_name="Test", username="donor")

        with patch(
            "src.utils.PLAYERS",
            [{"id": 123, "name": "donor", "fullname": "Donor", "ball_donate": True}],
        ):
            result = get_player_name(user)

        assert result == "🏐 Donor (@donor)"

    def test_get_player_name_with_subscription(self):
        """Подписчик получает эмодзи звёздочки."""
        user = User(id=123, is_bot=False, first_name="Test", username="sub")

        with patch(
            "src.utils.PLAYERS", [{"id": 123, "name": "sub", "fullname": "Subscriber"}]
        ):
            result = get_player_name(user, subs=[123, 456])

        assert result == "⭐️ Subscriber (@sub)"

    def test_get_player_name_with_subscription_and_ball_donate(self):
        """Подписчик и донор получает оба эмодзи в правильном порядке."""
        user = User(id=123, is_bot=False, first_name="Test", username="super")

        with patch(
            "src.utils.PLAYERS",
            [
                {
                    "id": 123,
                    "name": "super",
                    "fullname": "SuperUser",
                    "ball_donate": True,
                }
            ],
        ):
            result = get_player_name(user, subs=[123])

        assert result == "⭐️🏐 SuperUser (@super)"

    def test_get_player_name_sub_not_in_list(self):
        """Игрок не в списке подписчиков не получает звезду."""
        user = User(id=123, is_bot=False, first_name="Test", username="user")

        with patch(
            "src.utils.PLAYERS", [{"id": 123, "name": "user", "fullname": "User"}]
        ):
            result = get_player_name(user, subs=[456, 789])

        assert result == "User (@user)"
