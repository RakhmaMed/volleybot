"""Тесты для модуля poll и PollService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendPoll

from src.config import MAX_PLAYERS, MIN_PLAYERS
from src.db import POLL_STATE_KEY, init_db, load_state
from src.poll import PollData, VoterInfo, sort_voters_by_update_id
from src.services import PollService


def test_sort_voters_by_update_id_orders_updates():
    """Сортировка должна учитывать порядок update_id."""
    voters: list[VoterInfo] = [
        VoterInfo(id=2, name="@late", update_id=5),
        VoterInfo(id=1, name="@early", update_id=3),
        VoterInfo(id=3, name="@unknown", update_id=0),  # default update_id=0
    ]

    sorted_voters = sort_voters_by_update_id(voters)

    assert [v.id for v in sorted_voters] == [3, 1, 2]


def test_sort_voters_by_subscription():
    """Сортировка должна ставить подписчиков в начало списка."""
    voters: list[VoterInfo] = [
        VoterInfo(id=1, name="User1", update_id=10),
        VoterInfo(id=2, name="Sub1", update_id=20),
        VoterInfo(id=3, name="User2", update_id=5),
    ]
    subs = [2]  # Sub1 имеет подписку

    sorted_voters = sort_voters_by_update_id(voters, subs)

    # Sub1 (id=2) должен быть первым, несмотря на update_id=20
    # Затем User2 (id=3, update_id=5) и User1 (id=1, update_id=10)
    assert [v.id for v in sorted_voters] == [2, 3, 1]


class TestPollService:
    """Тесты для PollService."""

    def test_poll_service_initialization(self):
        """Тест инициализации сервиса."""
        service = PollService()
        assert not service.has_active_polls()
        assert service.get_all_polls() == {}

    def test_poll_service_has_poll(self):
        """Тест проверки существования опроса."""
        service = PollService()
        service._poll_data["test_id"] = PollData(
            chat_id=123, poll_msg_id=456, yes_voters=[], subs=[]
        )
        assert service.has_poll("test_id")
        assert not service.has_poll("nonexistent")

    def test_poll_service_get_poll_data(self):
        """Тест получения данных опроса."""
        service = PollService()
        poll_data = PollData(chat_id=123, poll_msg_id=456, yes_voters=[], subs=[])
        service._poll_data["test_id"] = poll_data

        assert service.get_poll_data("test_id") == poll_data
        assert service.get_poll_data("nonexistent") is None

    def test_poll_service_delete_poll(self):
        """Тест удаления опроса."""
        service = PollService()
        service._poll_data["test_id"] = PollData(
            chat_id=123, poll_msg_id=456, yes_voters=[], subs=[]
        )
        service._update_tasks["test_id"] = None

        service.delete_poll("test_id")

        assert not service.has_poll("test_id")
        assert "test_id" not in service._update_tasks

    def test_poll_service_clear_all_polls(self):
        """Тест очистки всех опросов."""
        service = PollService()
        service._poll_data["poll1"] = PollData(
            chat_id=123, poll_msg_id=456, yes_voters=[], subs=[]
        )
        service._poll_data["poll2"] = PollData(
            chat_id=789, poll_msg_id=101, yes_voters=[], subs=[]
        )

        service.clear_all_polls()

        assert not service.has_active_polls()
        assert service._update_tasks == {}

    def test_poll_service_update_voters(self):
        """Тест обновления списка голосующих."""
        service = PollService()
        service._poll_data["test_id"] = PollData(
            chat_id=123,
            poll_msg_id=456,
            yes_voters=[VoterInfo(id=1, name="User1", update_id=1)],
            subs=[2],  # User2 будет подписчиком
        )

        # Добавляем нового голосующего (подписчика)
        result = service.update_voters("test_id", 2, "User2", 2, True)
        assert len(result) == 2
        # User2 (подписчик) должен быть первым, хотя проголосовал позже (update_id=2)
        assert result[0].id == 2
        assert result[1].id == 1

        # Убираем голосующего
        result = service.update_voters("test_id", 2, "User2", 3, False)
        assert len(result) == 1
        assert result[0].id == 1


@pytest.mark.asyncio
class TestSendPoll:
    """Тесты для функции send_poll."""

    async def test_send_poll_when_bot_disabled(self, mock_bot):
        """Тест отправки опроса при выключенном боте."""
        service = PollService()
        result = await service.send_poll(
            mock_bot,
            chat_id=-1001234567890,
            question="Test question",
            poll_name="test_poll",
            bot_enabled=False,
        )

        assert result == -1001234567890
        mock_bot.send_poll.assert_not_called()

    async def test_send_poll_success(self, mock_bot):
        """Тест успешной отправки опроса."""
        service = PollService()

        mock_poll_message = MagicMock()
        mock_poll_message.poll.id = "test_poll_id"
        mock_poll_message.message_id = 123
        mock_bot.send_poll = AsyncMock(return_value=mock_poll_message)
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=124))
        mock_bot.pin_chat_message = AsyncMock()

        result = await service.send_poll(
            mock_bot,
            chat_id=-1001234567890,
            question="Test question",
            poll_name="test_poll",
            bot_enabled=True,
        )

        assert result == -1001234567890
        mock_bot.send_poll.assert_called_once()
        mock_bot.send_message.assert_called_once()
        mock_bot.pin_chat_message.assert_called_once()
        assert service.has_poll("test_poll_id")
        poll_data = service.get_poll_data("test_poll_id")
        assert poll_data is not None
        assert poll_data.yes_voters == []

    async def test_send_poll_handles_migration(self, mock_bot):
        """Тест обработки миграции группы в супергруппу."""
        service = PollService()

        new_chat_id = -1009876543210
        # Создаём мок исключения с нужным атрибутом
        migration_error = TelegramMigrateToChat(
            method=SendPoll(
                chat_id=-1001234567890,
                question="Test question",
                options=["Option 1", "Option 2"],
            ),
            message="Migration",
            migrate_to_chat_id=new_chat_id,
        )

        mock_bot.send_poll = AsyncMock(side_effect=migration_error)
        mock_bot.send_message = AsyncMock()

        result = await service.send_poll(
            mock_bot,
            chat_id=-1001234567890,
            question="Test question",
            poll_name="test_poll",
            bot_enabled=True,
        )

        assert result == new_chat_id
        mock_bot.send_message.assert_called_once()

    async def test_send_poll_handles_general_error(self, mock_bot):
        """Тест обработки общей ошибки при отправке опроса."""
        from aiogram.exceptions import TelegramAPIError

        service = PollService()

        mock_bot.send_poll = AsyncMock(
            side_effect=TelegramAPIError(method=MagicMock(), message="Network error")
        )
        mock_bot.send_message = AsyncMock()

        with patch("src.services.poll_service.save_error_dump") as mock_save:
            result = await service.send_poll(
                mock_bot,
                chat_id=-1001234567890,
                question="Test question",
                poll_name="test_poll",
                bot_enabled=True,
            )

            assert result == -1001234567890
            mock_save.assert_called_once()
            mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
class TestUpdatePlayersList:
    """Тесты для функции update_players_list."""

    async def test_update_players_list_empty(self, mock_bot):
        """Тест обновления списка при отсутствии голосов."""
        service = PollService()
        poll_id = "test_poll_id"
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=[],
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        call_args = mock_bot.edit_message_text.call_args
        assert "⏳ Идёт сбор голосов..." in call_args.kwargs["text"]

    async def test_update_players_list_less_than_required(self, mock_bot):
        """Тест обновления списка при недостаточном количестве игроков."""
        service = PollService()
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [
            VoterInfo(id=1, name="@user1"),
            VoterInfo(id=2, name="@user2"),
        ]
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=voters,
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        call_args = mock_bot.edit_message_text.call_args
        assert f"{len(voters)}/{MIN_PLAYERS}" in call_args.kwargs["text"]
        assert "@user1" in call_args.kwargs["text"]
        assert "@user2" in call_args.kwargs["text"]

    async def test_update_players_list_with_reserves(self, mock_bot):
        """Тест обновления списка с запасными игроками."""
        service = PollService()
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [
            VoterInfo(id=i, name=f"@user{i}") for i in range(MAX_PLAYERS + 5)
        ]
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=voters,
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        call_args = mock_bot.edit_message_text.call_args
        assert "✅ <b>Список игроков:</b>" in call_args.kwargs["text"]
        assert "🕗 <b>Запасные игроки:</b>" in call_args.kwargs["text"]
        # Должен использоваться HTML parse_mode
        assert call_args.kwargs.get("parse_mode") == "HTML"

    async def test_update_players_list_skips_if_no_info_msg(self, mock_bot):
        """Тест пропуска обновления при отсутствии info_msg_id."""
        service = PollService()
        poll_id = "test_poll_id"
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=None,  # Нет info_msg_id
            yes_voters=[],
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        # Без info_msg_id обновление должно быть пропущено
        mock_bot.edit_message_text.assert_not_called()

    async def test_update_players_list_skips_if_text_unchanged(self, mock_bot):
        """Тест пропуска обновления при неизменном тексте."""
        service = PollService()
        poll_id = "test_poll_id"
        text = "⏳ Идёт сбор голосов...\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=[],
            last_message_text=text,
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_not_called()
        assert service._update_tasks[poll_id] is None


@pytest.mark.asyncio
class TestClosePoll:
    """Тесты для функции close_poll."""

    async def test_close_poll_no_active_polls(self, mock_bot):
        """Тест закрытия опроса при отсутствии активных опросов."""
        service = PollService()

        await service.close_poll(mock_bot, "test_poll")

        mock_bot.stop_poll.assert_not_called()

    async def test_close_poll_success(self, mock_bot):
        """Тест успешного закрытия опроса."""
        service = PollService()
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [VoterInfo(id=1, name="@user1")]
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=voters,
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.stop_poll = AsyncMock()
        mock_bot.send_message = AsyncMock()
        mock_bot.delete_message = AsyncMock()

        await service.close_poll(mock_bot, "test_poll")

        mock_bot.stop_poll.assert_called_once()
        mock_bot.send_message.assert_called_once()
        mock_bot.delete_message.assert_called_once()
        # Проверяем, что send_message вызван с reply_to_message_id
        call_args = mock_bot.send_message.call_args
        assert call_args.kwargs["reply_to_message_id"] == 123
        assert not service.has_poll(poll_id)

    async def test_close_poll_with_full_team(self, mock_bot):
        """Тест закрытия опроса с полным составом."""
        service = PollService()
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [
            VoterInfo(id=i, name=f"@user{i}") for i in range(MAX_PLAYERS + 5)
        ]
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=voters,
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.stop_poll = AsyncMock()
        mock_bot.send_message = AsyncMock()
        mock_bot.delete_message = AsyncMock()

        await service.close_poll(mock_bot, "test_poll")

        call_args = mock_bot.send_message.call_args
        assert "✅" in call_args.kwargs["text"]
        assert "Запасные" in call_args.kwargs["text"]
        # Должен использоваться HTML parse_mode
        assert call_args.kwargs.get("parse_mode") == "HTML"
        # Должен быть reply_to_message_id
        assert call_args.kwargs["reply_to_message_id"] == 123


def test_persist_poll_state_roundtrip():
    """Состояние опроса должно сохраняться и восстанавливаться из БД."""
    init_db()
    service = PollService()

    service._poll_data["poll123"] = PollData(
        chat_id=1,
        poll_msg_id=2,
        info_msg_id=3,
        yes_voters=[VoterInfo(id=7, name="@user7", update_id=1)],
        last_message_text="cached",
        subs=[7],
    )
    service._update_tasks["poll123"] = None

    service.persist_state()

    stored = load_state(POLL_STATE_KEY, default={})
    assert "poll123" in stored
    # update_task не должен сериализоваться
    assert "update_task" not in stored["poll123"]

    service2 = PollService()
    service2.load_persisted_state()

    assert service2.has_poll("poll123")
    restored = service2.get_poll_data("poll123")
    assert restored is not None
    assert restored.chat_id == 1
    assert restored.poll_msg_id == 2
    assert restored.info_msg_id == 3
    yes_voters = restored.yes_voters
    assert len(yes_voters) > 0
    assert yes_voters[0].id == 7
    assert yes_voters[0].name == "@user7"


@pytest.mark.asyncio
class TestHtmlEscapingInPollTexts:
    """Тесты экранирования HTML в текстах опроса."""

    async def test_update_players_list_escapes_html(self, mock_bot):
        """Имена игроков с HTML-символами должны экранироваться."""
        service = PollService()
        poll_id = "test_html_poll_id"
        voters: list[VoterInfo] = [
            VoterInfo(id=1, name="<User&1>"),
            VoterInfo(id=2, name="NormalUser"),
        ]
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=voters,
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        text = mock_bot.edit_message_text.call_args.kwargs["text"]
        assert "&lt;User&amp;1&gt;" in text
        assert "<User&1>" not in text

    async def test_update_players_list_includes_legend(self, mock_bot):
        """Текст списка игроков должен содержать легенду эмодзи."""
        service = PollService()
        poll_id = "test_legend_poll_id"
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=[VoterInfo(id=1, name="User")],
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.services.poll_service.asyncio.sleep", new_callable=AsyncMock):
            await service._update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        text = mock_bot.edit_message_text.call_args.kwargs["text"]
        assert "⭐️ — оплативший за месяц" in text
        assert "🏐 — донат на мяч" in text

    async def test_close_poll_includes_legend(self, mock_bot):
        """Финальный текст опроса должен содержать легенду эмодзи."""
        service = PollService()
        poll_id = "test_close_poll_legend_id"
        service._poll_data[poll_id] = PollData(
            chat_id=-1001234567890,
            poll_msg_id=123,
            info_msg_id=124,
            yes_voters=[VoterInfo(id=1, name="User")],
            last_message_text="",
            subs=[],
        )
        service._update_tasks[poll_id] = None

        mock_bot.stop_poll = AsyncMock()
        mock_bot.send_message = AsyncMock()
        mock_bot.delete_message = AsyncMock()

        await service.close_poll(mock_bot, "test_poll")

        mock_bot.send_message.assert_called_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "⭐️ — оплативший за месяц" in text
        assert "🏐 — донат на мяч" in text
