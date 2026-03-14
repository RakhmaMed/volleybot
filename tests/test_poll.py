"""Тесты для модуля poll и PollService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendPoll

from src.config import MAX_PLAYERS, MIN_PLAYERS
from src.db import (
    POLL_STATE_KEY,
    ensure_player,
    get_game,
    get_player_balance,
    init_db,
    load_state,
    save_poll_template,
    update_player_balance,
)
from src.poll import PollData, VoterInfo, sort_voters_by_update_id
from src.services import PollService
from src.types import SubscriptionResult


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

    def test_resolve_target_month_prefers_saved_value(self):
        """Проверяет, что фиксированный target_month имеет приоритет над fallback-логикой."""
        service = PollService()
        data = PollData(
            kind="monthly_subscription",
            chat_id=1,
            poll_msg_id=2,
            yes_voters=[],
            subs=[],
            target_month="2026-04",
        )
        assert service._resolve_target_month(data) == "2026-04"

    def test_resolve_target_month_uses_opened_at_next_month(self):
        """Проверяет fallback: target_month вычисляется как следующий месяц от opened_at."""
        service = PollService()
        data = PollData(
            kind="monthly_subscription",
            chat_id=1,
            poll_msg_id=2,
            yes_voters=[],
            subs=[],
            opened_at="2026-01-31T23:55:00+00:00",
        )
        assert service._resolve_target_month(data) == "2026-02"


@pytest.mark.asyncio
async def test_close_monthly_subscription_uses_fixed_target_month(temp_db):
    """
    Проверяет, что при закрытии monthly poll используется зафиксированный target_month,
    а не текущая дата на момент закрытия.
    """
    init_db()
    service = PollService()
    data = PollData(
        kind="monthly_subscription",
        chat_id=-1001234567890,
        poll_msg_id=123,
        info_msg_id=124,
        yes_voters=[],
        subs=[],
        option_poll_names=["Пятница", None],
        monthly_votes={101: [0]},
        target_month="2026-02",
    )

    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))

    with (
        patch("src.services.poll_service.load_monthly_votes", return_value={}),
        patch(
            "src.services.poll_service.get_poll_templates",
            return_value=[
                {"id": 1, "name": "Пятница", "cost": 150, "cost_per_game": 1500}
            ],
        ),
        patch("src.services.poll_service.save_poll_template"),
        patch("src.services.poll_service.get_fund_balance", return_value=0),
        patch(
            "src.services.poll_service.calculate_subscription",
            return_value=SubscriptionResult(hall_breakdown=[], subscriber_charges=[]),
        ) as mock_calculate,
        patch.object(service, "_apply_subscription_charges", return_value=[]),
        patch("src.services.poll_service.close_game"),
    ):
        await service._close_monthly_subscription_poll(
            mock_bot, "monthly-test", "monthly_subscription", data
        )

    args = mock_calculate.call_args.args
    # 3-й позиционный аргумент calculate_subscription — это target_month.
    assert args[2] == "2026-02"


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

    async def test_send_poll_success(self, mock_bot, temp_db):
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
        game = get_game("test_poll_id")
        assert game is not None
        assert game["poll_message_id"] == 123
        assert game["info_message_id"] == 124

    async def test_send_poll_handles_migration(self, mock_bot, temp_db):
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

    async def test_send_poll_handles_general_error(self, mock_bot, temp_db):
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

        await service.close_poll(mock_bot, "test_poll_id")

        mock_bot.stop_poll.assert_not_called()

    async def test_close_poll_success(self, mock_bot, temp_db):
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

        await service.close_poll(mock_bot, poll_id)

        mock_bot.stop_poll.assert_called_once()
        mock_bot.send_message.assert_called_once()
        mock_bot.delete_message.assert_called_once()
        # Проверяем, что send_message вызван с reply_to_message_id
        call_args = mock_bot.send_message.call_args
        assert call_args.kwargs["reply_to_message_id"] == 123
        assert not service.has_poll(poll_id)

    async def test_close_poll_with_full_team(self, mock_bot, temp_db):
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

        await service.close_poll(mock_bot, poll_id)

        call_args = mock_bot.send_message.call_args
        assert "✅" in call_args.kwargs["text"]
        assert "Запасные" in call_args.kwargs["text"]
        # Должен использоваться HTML parse_mode
        assert call_args.kwargs.get("parse_mode") == "HTML"
        # Должен быть reply_to_message_id
        assert call_args.kwargs["reply_to_message_id"] == 123


@pytest.mark.asyncio
class TestProcessPaymentDeduction:
    """Тесты для _process_payment_deduction (списание за платный зал)."""

    async def test_poll_not_in_config_skips_deduction(self, mock_bot, temp_db):
        """Если опрос не в конфиге шаблонов — списание не выполняется."""
        init_db()
        service = PollService()
        yes_voters = [VoterInfo(id=1, name="@user1")]
        with patch.object(
            service, "_send_admin_report", new_callable=AsyncMock
        ) as mock_report:
            await service._process_payment_deduction(
                mock_bot, "Несуществующий опрос", yes_voters, []
            )
        mock_report.assert_not_called()
        # Баланс не должен меняться (игрок мог не быть в БД)
        data = get_player_balance(1)
        if data:
            assert data.get("balance", 0) == 0

    async def test_free_poll_skips_deduction(self, mock_bot, temp_db):
        """Если cost=0 — списание не выполняется."""
        init_db()
        save_poll_template(
            {
                "name": "Бесплатный зал",
                "message": "Текст",
                "cost": 0,
            }
        )
        service = PollService()
        yes_voters = [VoterInfo(id=2, name="@user2")]
        with patch.object(
            service, "_send_admin_report", new_callable=AsyncMock
        ) as mock_report:
            await service._process_payment_deduction(
                mock_bot, "Бесплатный зал", yes_voters, []
            )
        mock_report.assert_not_called()
        data = get_player_balance(2)
        if data:
            assert data.get("balance", 0) == 0

    async def test_deducts_balance_using_dict(self, mock_bot, temp_db):
        """Списывает сумму с игрока; get_player_balance возвращает dict — используется balance."""
        init_db()
        save_poll_template(
            {
                "name": "Платный зал",
                "message": "Текст",
                "cost": 150,
            }
        )
        ensure_player(2, "user2")
        update_player_balance(2, 500)
        service = PollService()
        yes_voters = [VoterInfo(id=2, name="@user2")]
        with patch.object(
            service, "_send_admin_report", new_callable=AsyncMock
        ) as mock_report:
            await service._process_payment_deduction(
                mock_bot, "Платный зал", yes_voters, []
            )
        data = get_player_balance(2)
        assert data is not None
        assert data["balance"] == 350
        mock_report.assert_called_once()
        # _send_admin_report(bot, poll_name, cost, charged_players, subscribed_players)
        _, _, _, charged, _ = mock_report.call_args[0]
        assert len(charged) == 1
        assert charged[0]["old_balance"] == 500
        assert charged[0]["new_balance"] == 350

    async def test_skips_subscribers(self, mock_bot, temp_db):
        """Игроки из списка подписчиков не списываются."""
        init_db()
        save_poll_template(
            {
                "name": "Зал с подпиской",
                "message": "Текст",
                "cost": 100,
            }
        )
        ensure_player(10, "sub_user")
        update_player_balance(10, 200)
        service = PollService()
        yes_voters = [VoterInfo(id=10, name="@sub_user")]
        subs = [10]
        with patch.object(
            service, "_send_admin_report", new_callable=AsyncMock
        ) as mock_report:
            await service._process_payment_deduction(
                mock_bot, "Зал с подпиской", yes_voters, subs
            )
        data = get_player_balance(10)
        assert data is not None
        assert data["balance"] == 200
        _, _, _, charged, subscribed_names = mock_report.call_args[0]
        assert charged == []
        assert "@sub_user" in subscribed_names

    async def test_handles_none_from_get_player_balance(self, mock_bot, temp_db):
        """Если get_player_balance возвращает None — используется 0, исключения нет."""
        init_db()
        save_poll_template(
            {
                "name": "Зал для теста None",
                "message": "Текст",
                "cost": 50,
            }
        )
        service = PollService()
        yes_voters = [VoterInfo(id=99, name="@new_user")]
        with patch("src.services.poll_service.get_player_balance", return_value=None):
            with patch.object(
                service, "_send_admin_report", new_callable=AsyncMock
            ) as mock_report:
                await service._process_payment_deduction(
                    mock_bot, "Зал для теста None", yes_voters, []
                )
        mock_report.assert_called_once()
        _, _, _, charged, _ = mock_report.call_args[0]
        assert len(charged) == 1
        assert charged[0]["old_balance"] == 0
        assert charged[0]["new_balance"] == -50


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

    async def test_close_poll_includes_legend(self, mock_bot, temp_db):
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

        await service.close_poll(mock_bot, poll_id)

        mock_bot.send_message.assert_called_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "⭐️ — оплативший за месяц" in text
        assert "🏐 — донат на мяч" in text

    async def test_close_poll_includes_payment_details(self, mock_bot, temp_db):
        """Финальный текст опроса должен содержать реквизиты для перевода."""
        service = PollService()
        poll_id = "test_close_poll_payment_details_id"
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

        with (
            patch("src.services.poll_service.PAYMENT_NAME", "Rakhma"),
            patch("src.services.poll_service.PAYMENT_BANK", "TСберk"),
            patch("src.services.poll_service.PAYMENT_PHONE", "+79990000000"),
        ):
            await service.close_poll(mock_bot, poll_id)

        mock_bot.send_message.assert_called_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "Реквизиты для перевода" in text
        assert "Rakhma" in text
        assert "TСберk" in text
        assert "+79990000000" in text

    async def test_close_poll_escapes_payment_details(self, mock_bot, temp_db):
        """Реквизиты в финальном тексте должны экранироваться."""
        service = PollService()
        poll_id = "test_close_poll_escape_payment_details_id"
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

        with (
            patch("src.services.poll_service.PAYMENT_NAME", "<Rakhma&Co>"),
            patch("src.services.poll_service.PAYMENT_BANK", "\"Best<Bank>\""),
            patch("src.services.poll_service.PAYMENT_PHONE", "+7<999>&000"),
        ):
            await service.close_poll(mock_bot, poll_id)

        mock_bot.send_message.assert_called_once()
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "&lt;Rakhma&amp;Co&gt;" in text
        assert "\"Best&lt;Bank&gt;\"" in text
        assert "+7&lt;999&gt;&amp;000" in text
        assert "<Rakhma&Co>" not in text
        assert "\"Best<Bank>\"" not in text
        assert "+7<999>&000" not in text


def test_format_subscription_report_sorts_by_name():
    """Итоговый отчёт должен сортировать список оплат по имени."""
    service = PollService()

    report = service._format_subscription_report(
        total_voters=3,
        summary_text="Сводка",
        charged_subscribers=[
            {"name": "Марат", "fullname": "Марат", "amount": 400, "old_balance": 0},
            {"name": "Алим", "fullname": "Алим", "amount": 300, "old_balance": 0},
            {"name": "Борис", "fullname": "Борис", "amount": 200, "old_balance": 0},
        ],
        fund_balance=1000,
    )

    assert report.index("Алим</a> - 300 ₽") < report.index("Борис</a> - 200 ₽")
    assert report.index("Борис</a> - 200 ₽") < report.index("Марат</a> - 400 ₽")


def test_format_subscription_report_adds_payment_details():
    """Итоговый отчёт должен включать реквизиты для перевода."""
    service = PollService()

    with (
        patch("src.services.poll_service.PAYMENT_NAME", "Rakhma"),
        patch("src.services.poll_service.PAYMENT_BANK", "TСберk"),
        patch("src.services.poll_service.PAYMENT_PHONE", "+79990000000"),
    ):
        report = service._format_subscription_report(
            total_voters=1,
            summary_text="Сводка",
            charged_subscribers=[
                {"name": "Алим", "fullname": "Алим", "amount": 300, "old_balance": 50},
            ],
            fund_balance=1000,
        )

    assert "Реквизиты для перевода" in report
    assert "Rakhma" in report
    assert "TСберk" in report
    assert "+79990000000" in report


def test_format_subscription_report_adds_profile_links():
    """Итоговый отчёт должен включать ссылки на профили игроков."""
    service = PollService()

    report = service._format_subscription_report(
        total_voters=1,
        summary_text="Сводка",
        charged_subscribers=[
            {
                "user_id": 12345,
                "name": "petya",
                "username": "petya",
                "fullname": "Петя",
                "amount": 300,
                "old_balance": 0,
            },
        ],
        fund_balance=1000,
    )

    assert '<a href="https://t.me/petya">Петя</a> - 300 ₽' in report
