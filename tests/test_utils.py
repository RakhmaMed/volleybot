"""Тесты для модуля utils."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import ChatMemberOwner, User

from src.services import AdminService
from src.utils import (
    _RATE_LIMIT_CACHE,
    RATE_LIMIT_MAX_REQUESTS,
    escape_html,
    generate_webhook_secret_path,
    get_player_name,
    is_rate_limited,
    is_telegram_ip,
    rate_limit_check,
    save_error_dump,
)


class TestAdminService:
    """Тесты для AdminService."""

    @pytest.fixture
    def admin_service(self):
        """Создаёт экземпляр AdminService."""
        return AdminService(default_chat_id=-1001234567890)

    @pytest.fixture
    def mock_bot(self):
        """Создаёт мок бота."""
        bot = MagicMock()
        return bot

    @pytest.fixture
    def admin_user(self):
        """Создаёт пользователя-администратора."""
        return User(id=123456789, is_bot=False, first_name="Admin", username="admin")

    @pytest.fixture
    def regular_user(self):
        """Создаёт обычного пользователя."""
        return User(id=987654321, is_bot=False, first_name="User", username="user")

    @pytest.mark.asyncio
    async def test_is_admin_returns_true_for_admin(
        self, admin_service, mock_bot, admin_user
    ):
        """Тест что администратор группы распознаётся."""
        # Мокаем ответ Telegram API
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        result = await admin_service.is_admin(mock_bot, admin_user)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_admin_returns_false_for_non_admin(
        self, admin_service, mock_bot, admin_user, regular_user
    ):
        """Тест что обычный пользователь не распознаётся как админ."""
        # Мокаем ответ - только admin_user в списке
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        result = await admin_service.is_admin(mock_bot, regular_user)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_admin_uses_cache(self, admin_service, mock_bot, admin_user):
        """Тест что кэш используется и API не вызывается повторно."""
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        # Первый вызов - заполняет кэш
        await admin_service.is_admin(mock_bot, admin_user)
        assert mock_bot.get_chat_administrators.call_count == 1

        # Второй вызов - должен использовать кэш
        await admin_service.is_admin(mock_bot, admin_user)
        assert mock_bot.get_chat_administrators.call_count == 1  # Не увеличился

    @pytest.mark.asyncio
    async def test_refresh_cache_updates_admins(
        self, admin_service, mock_bot, admin_user
    ):
        """Тест принудительного обновления кэша."""
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        await admin_service.refresh_cache(mock_bot)

        cached = admin_service.get_cached_admins()
        assert admin_user.id in cached

    def test_invalidate_cache_clears_cache(self, admin_service):
        """Тест инвалидации кэша."""
        # Вручную заполняем кэш
        admin_service._admin_cache[-1001234567890] = {123, 456}
        admin_service._cache_updated_at[-1001234567890] = time.time()

        admin_service.invalidate_cache()

        assert admin_service.get_cached_admins() == set()

    @pytest.mark.asyncio
    async def test_is_admin_handles_api_error(
        self, admin_service, mock_bot, admin_user
    ):
        """Тест обработки ошибки API."""
        mock_bot.get_chat_administrators = AsyncMock(side_effect=Exception("API Error"))

        result = await admin_service.is_admin(mock_bot, admin_user)
        # При ошибке возвращается False
        assert result is False

    @pytest.mark.asyncio
    async def test_is_admin_uses_default_chat_for_private_chat(
        self, admin_service, mock_bot, admin_user
    ):
        """Тест что для приватных чатов (положительный ID) используется default_chat_id."""
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        private_chat_id = 5013132836  # Положительный ID = приватный чат

        await admin_service.is_admin(mock_bot, admin_user, chat_id=private_chat_id)

        # Должен вызываться с default_chat_id (-1001234567890), а не с private_chat_id
        mock_bot.get_chat_administrators.assert_called_once_with(-1001234567890)

    @pytest.mark.asyncio
    async def test_refresh_cache_uses_default_chat_for_private_chat(
        self, admin_service, mock_bot, admin_user
    ):
        """Тест что refresh_cache использует default_chat_id для приватных чатов."""
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        private_chat_id = 5013132836  # Положительный ID = приватный чат

        await admin_service.refresh_cache(mock_bot, chat_id=private_chat_id)

        # Должен вызываться с default_chat_id (-1001234567890), а не с private_chat_id
        mock_bot.get_chat_administrators.assert_called_once_with(-1001234567890)

    @pytest.mark.asyncio
    async def test_is_admin_uses_group_chat_id_for_groups(
        self, admin_service, mock_bot, admin_user
    ):
        """Тест что для групповых чатов (отрицательный ID) используется переданный chat_id."""
        admin_member = MagicMock()
        admin_member.user = admin_user
        mock_bot.get_chat_administrators = AsyncMock(return_value=[admin_member])

        group_chat_id = -1009999999999  # Отрицательный ID = групповой чат

        await admin_service.is_admin(mock_bot, admin_user, chat_id=group_chat_id)

        # Должен вызываться с переданным group_chat_id
        mock_bot.get_chat_administrators.assert_called_once_with(group_chat_id)


class TestRateLimiting:
    """Тесты для rate limiting."""

    @pytest.fixture(autouse=True)
    def clear_rate_limit_cache(self):
        """Очищает кэш rate limit перед каждым тестом."""
        _RATE_LIMIT_CACHE.clear()
        yield
        _RATE_LIMIT_CACHE.clear()

    def test_is_rate_limited_allows_requests_under_limit(self):
        """Тест что запросы в пределах лимита разрешены."""
        user_id = 12345
        # Первые несколько запросов должны проходить
        for _ in range(RATE_LIMIT_MAX_REQUESTS - 1):
            assert is_rate_limited(user_id) is False

    def test_is_rate_limited_blocks_after_limit(self):
        """Тест что запросы сверх лимита блокируются."""
        user_id = 12346
        # Делаем максимальное количество запросов
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            is_rate_limited(user_id)
        # Следующий запрос должен быть заблокирован
        assert is_rate_limited(user_id) is True

    def test_rate_limit_check_returns_none_for_admin(self):
        """Тест что администраторы не ограничены rate limit."""
        admin_user = User(id=777, is_bot=False, first_name="Admin", username="admin")
        # Администраторы не ограничены (передаём is_admin=True)
        result = rate_limit_check(admin_user, is_admin=True)
        assert result is None

    def test_rate_limit_check_returns_error_when_limited(self):
        """Тест что rate_limit_check возвращает ошибку при превышении лимита."""
        regular_user = User(
            id=888, is_bot=False, first_name="Regular", username="regular_user"
        )
        # Исчерпываем лимит
        for _ in range(RATE_LIMIT_MAX_REQUESTS):
            is_rate_limited(regular_user.id)
        # Проверяем что возвращается сообщение об ошибке (is_admin=False)
        result = rate_limit_check(regular_user, is_admin=False)
        assert result is not None
        assert "Слишком много запросов" in result


class TestTelegramIPValidation:
    """Тесты для валидации IP-адресов Telegram."""

    def test_is_telegram_ip_valid_ip(self):
        """Тест валидного IP Telegram."""
        # IP из диапазона 149.154.160.0/20
        assert is_telegram_ip("149.154.167.50") is True

    def test_is_telegram_ip_valid_ip_second_range(self):
        """Тест валидного IP Telegram из второго диапазона."""
        # IP из диапазона 91.108.4.0/22
        assert is_telegram_ip("91.108.4.100") is True

    def test_is_telegram_ip_invalid_ip(self):
        """Тест невалидного IP (не Telegram)."""
        assert is_telegram_ip("8.8.8.8") is False
        assert is_telegram_ip("192.168.1.1") is False

    def test_is_telegram_ip_malformed_ip(self):
        """Тест некорректного формата IP."""
        assert is_telegram_ip("not-an-ip") is False
        assert is_telegram_ip("") is False


class TestWebhookSecretPath:
    """Тесты для генерации секретного пути webhook."""

    def test_generate_webhook_secret_path_format(self):
        """Тест формата секретного пути."""
        token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
        path = generate_webhook_secret_path(token)
        assert path.startswith("/webhook_")
        assert len(path) > 10  # /webhook_ + hash

    def test_generate_webhook_secret_path_deterministic(self):
        """Тест что одинаковые токены дают одинаковый путь."""
        token = "test_token_12345"
        path1 = generate_webhook_secret_path(token)
        path2 = generate_webhook_secret_path(token)
        assert path1 == path2

    def test_generate_webhook_secret_path_different_tokens(self):
        """Тест что разные токены дают разные пути."""
        path1 = generate_webhook_secret_path("token1")
        path2 = generate_webhook_secret_path("token2")
        assert path1 != path2

    def test_generate_webhook_secret_path_length(self):
        """Тест длины секретного пути."""
        token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
        path = generate_webhook_secret_path(token)
        # /webhook_ (9 символов) + 32 символа хеша = 41
        assert len(path) == 41


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
