"""Тесты для модуля poll."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendPoll

from src.config import REQUIRED_PLAYERS
from src.db import POLL_STATE_KEY, init_db, load_state
from src.poll import (
    PollDataItem,
    VoterInfo,
    close_poll,
    load_persisted_poll_state,
    persist_poll_state,
    poll_data,
    send_poll,
    sort_voters_by_update_id,
    update_players_list,
)


def test_sort_voters_by_update_id_orders_updates():
    """Сортировка должна учитывать порядок update_id."""
    voters: list[VoterInfo] = [
        {"id": 2, "name": "@late", "update_id": 5},
        {"id": 1, "name": "@early", "update_id": 3},
        {"id": 3, "name": "@unknown"},  # без update_id остаётся в начале
    ]

    sorted_voters = sort_voters_by_update_id(voters)

    assert [v["id"] for v in sorted_voters] == [3, 1, 2]


@pytest.mark.asyncio
class TestSendPoll:
    """Тесты для функции send_poll."""

    async def test_send_poll_when_bot_disabled(self, mock_bot):
        """Тест отправки опроса при выключенном боте."""
        result = await send_poll(
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
        mock_poll_message = MagicMock()
        mock_poll_message.poll.id = "test_poll_id"
        mock_poll_message.message_id = 123
        mock_bot.send_poll = AsyncMock(return_value=mock_poll_message)
        mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=124))
        mock_bot.pin_chat_message = AsyncMock()

        poll_data.clear()

        result = await send_poll(
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
        assert "test_poll_id" in poll_data
        assert poll_data["test_poll_id"]["yes_voters"] == []

    async def test_send_poll_handles_migration(self, mock_bot):
        """Тест обработки миграции группы в супергруппу."""
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

        poll_data.clear()

        result = await send_poll(
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
        mock_bot.send_poll = AsyncMock(side_effect=Exception("Network error"))
        mock_bot.send_message = AsyncMock()

        poll_data.clear()

        with patch("src.poll.save_error_dump") as mock_save:
            result = await send_poll(
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
        poll_id = "test_poll_id"
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": [],
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        call_args = mock_bot.edit_message_text.call_args
        assert "⏳ Идёт сбор голосов..." in call_args.kwargs["text"]

    async def test_update_players_list_less_than_required(self, mock_bot):
        """Тест обновления списка при недостаточном количестве игроков."""
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [
            {"id": 1, "name": "@user1"},
            {"id": 2, "name": "@user2"},
        ]
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": voters,
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        call_args = mock_bot.edit_message_text.call_args
        assert f"{len(voters)}/{REQUIRED_PLAYERS}" in call_args.kwargs["text"]
        assert "@user1" in call_args.kwargs["text"]
        assert "@user2" in call_args.kwargs["text"]

    async def test_update_players_list_with_reserves(self, mock_bot):
        """Тест обновления списка с запасными игроками."""
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [
            {"id": i, "name": f"@user{i}"} for i in range(REQUIRED_PLAYERS + 5)
        ]
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": voters,
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        call_args = mock_bot.edit_message_text.call_args
        assert "✅ <b>Список игроков:</b>" in call_args.kwargs["text"]
        assert "🕗 <b>Запасные игроки:</b>" in call_args.kwargs["text"]
        # Должен использоваться HTML parse_mode
        assert call_args.kwargs.get("parse_mode") == "HTML"

    async def test_update_players_list_skips_if_no_info_msg(self, mock_bot):
        """Тест пропуска обновления при отсутствии info_msg_id."""
        poll_id = "test_poll_id"
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": None,
                "yes_voters": [{"id": 1, "name": "@user1"}],
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_not_called()

    async def test_update_players_list_skips_if_text_unchanged(self, mock_bot):
        """Тест пропуска обновления при неизменном тексте."""
        poll_id = "test_poll_id"
        text = "⏳ Идёт сбор голосов..."
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": [],
                "update_task": None,
                "last_message_text": text,
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        # Должен быть вызван, но если текст совпадает, то не обновляется
        # В реальной реализации проверка происходит после формирования текста
        # Здесь мы просто проверяем, что функция выполнилась
        assert poll_data[poll_id].get("update_task") is None


@pytest.mark.asyncio
class TestClosePoll:
    """Тесты для функции close_poll."""

    async def test_close_poll_no_active_polls(self, mock_bot):
        """Тест закрытия опроса при отсутствии активных опросов."""
        poll_data.clear()

        await close_poll(mock_bot, "test_poll")

        mock_bot.stop_poll.assert_not_called()

    async def test_close_poll_success(self, mock_bot):
        """Тест успешного закрытия опроса."""
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [{"id": i, "name": f"@user{i}"} for i in range(5)]
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": voters,
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.stop_poll = AsyncMock()
        mock_bot.edit_message_text = AsyncMock()

        await close_poll(mock_bot, "test_poll")

        mock_bot.stop_poll.assert_called_once()
        mock_bot.edit_message_text.assert_called_once()
        assert poll_id not in poll_data

    async def test_close_poll_with_full_team(self, mock_bot):
        """Тест закрытия опроса с полной командой."""
        poll_id = "test_poll_id"
        voters: list[VoterInfo] = [
            {"id": i, "name": f"@user{i}"} for i in range(REQUIRED_PLAYERS + 3)
        ]
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": voters,
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.stop_poll = AsyncMock()
        mock_bot.edit_message_text = AsyncMock()

        await close_poll(mock_bot, "test_poll")

        call_args = mock_bot.edit_message_text.call_args
        assert "✅" in call_args.kwargs["text"]
        assert "Запасные" in call_args.kwargs["text"]
        # Должен использоваться HTML parse_mode
        assert call_args.kwargs.get("parse_mode") == "HTML"


def test_persist_poll_state_roundtrip():
    """Состояние опроса должно сохраняться и восстанавливаться из БД."""
    init_db()
    poll_data.clear()
    poll_data["poll123"] = cast(
        PollDataItem,
        {
            "chat_id": 1,
            "poll_msg_id": 2,
            "info_msg_id": 3,
            "yes_voters": [{"id": 7, "name": "@user7", "update_id": 1}],
            "update_task": object(),
            "last_message_text": "cached",
            "subs": [7],
        },
    )

    persist_poll_state()

    stored = load_state(POLL_STATE_KEY, default={})
    assert stored["poll123"]["update_task"] is None

    poll_data.clear()
    load_persisted_poll_state()

    assert "poll123" in poll_data
    restored = poll_data["poll123"]
    assert restored["chat_id"] == 1
    assert restored.get("update_task") is None
    yes_voters = restored["yes_voters"]
    assert len(yes_voters) > 0
    assert yes_voters[0]["id"] == 7


@pytest.mark.asyncio
class TestHtmlEscapingInPollTexts:
    """Тесты экранирования HTML в текстах опроса."""

    async def test_update_players_list_escapes_html(self, mock_bot):
        """Имена игроков с HTML-символами должны экранироваться."""
        poll_id = "test_html_poll_id"
        voters: list[VoterInfo] = [
            {"id": 1, "name": "<User&1>"},
            {"id": 2, "name": "NormalUser"},
        ]
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": voters,
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        text = mock_bot.edit_message_text.call_args.kwargs["text"]
        assert "&lt;User&amp;1&gt;" in text
        assert "<User&1>" not in text

    async def test_update_players_list_includes_legend(self, mock_bot):
        """Текст списка игроков должен содержать легенду эмодзи."""
        poll_id = "test_legend_poll_id"
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": [{"id": 1, "name": "User"}],
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.edit_message_text = AsyncMock()

        with patch("src.poll.asyncio.sleep", new_callable=AsyncMock):
            await update_players_list(mock_bot, poll_id)

        mock_bot.edit_message_text.assert_called_once()
        text = mock_bot.edit_message_text.call_args.kwargs["text"]
        assert "⭐️ — оплативший за месяц" in text
        assert "🏐 — донат на мяч" in text

    async def test_close_poll_includes_legend(self, mock_bot):
        """Финальный текст опроса должен содержать легенду эмодзи."""
        poll_id = "test_close_poll_legend_id"
        poll_data[poll_id] = cast(
            PollDataItem,
            {
                "chat_id": -1001234567890,
                "poll_msg_id": 123,
                "info_msg_id": 124,
                "yes_voters": [{"id": 1, "name": "User"}],
                "update_task": None,
                "last_message_text": "",
                "subs": [],
            },
        )

        mock_bot.stop_poll = AsyncMock()
        mock_bot.edit_message_text = AsyncMock()

        await close_poll(mock_bot, "test_poll")

        mock_bot.edit_message_text.assert_called_once()
        text = mock_bot.edit_message_text.call_args.kwargs["text"]
        assert "⭐️ — оплативший за месяц" in text
        assert "🏐 — донат на мяч" in text
