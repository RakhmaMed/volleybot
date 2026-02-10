"""Тесты для кассы, оплаты залов, /restore и расчёта абонементов."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
)

from src.db import (
    _connect,
    add_transaction,
    ensure_player,
    get_fund_balance,
    get_player_balance,
    get_unpaid_halls,
    init_db,
    record_hall_payment,
    save_poll_template,
    update_fund_balance,
    update_player_balance,
)
from src.handlers import register_handlers
from src.services import BotStateService, PollService


# ── DB-level fund tests ─────────────────────────────────────────────────────


class TestFundBalance:
    """Тесты для функций кассы в БД."""

    def test_fund_balance_default_zero(self, temp_db):
        """По умолчанию баланс кассы равен 0."""
        init_db()
        assert get_fund_balance() == 0

    def test_update_fund_balance_increment(self, temp_db):
        """Пополнение кассы увеличивает баланс."""
        init_db()
        update_fund_balance(500)
        assert get_fund_balance() == 500

    def test_update_fund_balance_decrement(self, temp_db):
        """Списание с кассы уменьшает баланс."""
        init_db()
        update_fund_balance(1000)
        update_fund_balance(-300)
        assert get_fund_balance() == 700

    def test_update_fund_balance_multiple(self, temp_db):
        """Несколько последовательных изменений кассы корректны."""
        init_db()
        update_fund_balance(100)
        update_fund_balance(200)
        update_fund_balance(-50)
        assert get_fund_balance() == 250

    def test_fund_can_go_negative(self, temp_db):
        """Касса может уйти в минус."""
        init_db()
        update_fund_balance(-500)
        assert get_fund_balance() == -500


# ── DB-level hall payment tests ──────────────────────────────────────────────


class TestHallPayments:
    """Тесты для оплаты залов."""

    def _create_paid_template(self, name: str = "Пятница", monthly_cost: int = 6000):
        save_poll_template({
            "name": name,
            "message": f"Игра {name}",
            "cost": 150,
            "monthly_cost": monthly_cost,
        })

    def test_get_unpaid_halls_returns_paid_only(self, temp_db):
        """get_unpaid_halls возвращает только залы с monthly_cost > 0."""
        init_db()
        self._create_paid_template("Пятница", monthly_cost=6000)
        save_poll_template({
            "name": "Среда",
            "message": "Бесплатная игра",
            "cost": 0,
            "monthly_cost": 0,
        })

        unpaid = get_unpaid_halls("2026-02")
        assert len(unpaid) == 1
        assert unpaid[0]["name"] == "Пятница"

    def test_get_unpaid_halls_excludes_paid(self, temp_db):
        """Оплаченные залы не возвращаются."""
        init_db()
        self._create_paid_template("Пятница", monthly_cost=6000)
        self._create_paid_template("Понедельник", monthly_cost=4000)

        record_hall_payment("Пятница", "2026-02", 6000)

        unpaid = get_unpaid_halls("2026-02")
        assert len(unpaid) == 1
        assert unpaid[0]["name"] == "Понедельник"

    def test_record_hall_payment_success(self, temp_db):
        """Запись оплаты зала успешна."""
        init_db()
        self._create_paid_template("Пятница")

        result = record_hall_payment("Пятница", "2026-02", 6000)
        assert result is True

    def test_record_hall_payment_duplicate_fails(self, temp_db):
        """Повторная оплата того же зала за тот же месяц не проходит."""
        init_db()
        self._create_paid_template("Пятница")

        assert record_hall_payment("Пятница", "2026-02", 6000) is True
        assert record_hall_payment("Пятница", "2026-02", 6000) is False

    def test_different_months_allowed(self, temp_db):
        """Оплата одного зала за разные месяцы допустима."""
        init_db()
        self._create_paid_template("Пятница")

        assert record_hall_payment("Пятница", "2026-02", 6000) is True
        assert record_hall_payment("Пятница", "2026-03", 6000) is True

    def test_all_halls_paid(self, temp_db):
        """Если все залы оплачены, get_unpaid_halls возвращает пустой список."""
        init_db()
        self._create_paid_template("Пятница", monthly_cost=6000)
        record_hall_payment("Пятница", "2026-02", 6000)

        unpaid = get_unpaid_halls("2026-02")
        assert len(unpaid) == 0


# ── DB-level monthly_cost tests ─────────────────────────────────────────────


class TestMonthlyCost:
    """Тесты для поля monthly_cost в poll_templates."""

    def test_monthly_cost_default_zero(self, temp_db):
        """monthly_cost по умолчанию равен 0."""
        init_db()
        save_poll_template({
            "name": "Test",
            "message": "Test",
        })
        with _connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT monthly_cost FROM poll_templates WHERE name = 'Test'"
            ).fetchone()
            assert row["monthly_cost"] == 0

    def test_monthly_cost_saved(self, temp_db):
        """monthly_cost сохраняется корректно."""
        init_db()
        save_poll_template({
            "name": "Пятница",
            "message": "Игра",
            "monthly_cost": 6000,
        })
        with _connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT monthly_cost FROM poll_templates WHERE name = 'Пятница'"
            ).fetchone()
            assert row["monthly_cost"] == 6000

    def test_monthly_cost_migration(self, temp_db):
        """Проверка, что init_db добавляет monthly_cost в существующую таблицу."""
        init_db()
        # Столбец должен уже быть после init_db
        with _connect() as conn:
            cursor = conn.execute("PRAGMA table_info(poll_templates)")
            columns = [row[1] for row in cursor.fetchall()]
            assert "monthly_cost" in columns


# ── DB-level transaction tests ───────────────────────────────────────────────


class TestTransactionLogging:
    """Тесты для логирования транзакций."""

    def test_transaction_created(self, temp_db):
        """Транзакция создаётся с правильными данными."""
        init_db()
        ensure_player(user_id=100, name="test", fullname="Test Player")
        add_transaction(100, 500, "Оплата (admin: @admin)")

        with _connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT * FROM transactions WHERE player_id = 100"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["amount"] == 500
            assert rows[0]["description"] == "Оплата (admin: @admin)"

    def test_transaction_with_poll_name(self, temp_db):
        """Транзакция с привязкой к опросу."""
        init_db()
        ensure_player(user_id=101, name="test2", fullname="Test 2")
        add_transaction(101, -150, "Зал: Пятница (08.02.2026)", "Пятница")

        with _connect() as conn:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT * FROM transactions WHERE player_id = 101"
            ).fetchone()
            assert row["poll_name"] == "Пятница"
            assert row["amount"] == -150


# ── Handler-level /pay tests (fund tracking) ────────────────────────────────


@pytest.mark.asyncio
class TestPayFundTracking:
    """/pay должен обновлять кассу и создавать транзакцию."""

    @patch("src.handlers.update_fund_balance")
    @patch("src.handlers.add_transaction")
    @patch("src.handlers.get_fund_balance", return_value=500)
    @patch("src.handlers.update_player_balance", return_value=True)
    @patch("src.handlers.get_player_balance")
    @patch("src.handlers.ensure_player")
    async def test_pay_updates_fund(
        self,
        mock_ensure,
        mock_get_balance,
        mock_update_balance,
        mock_get_fund,
        mock_add_tx,
        mock_update_fund,
        admin_user,
        regular_user,
        admin_service,
    ):
        """При /pay касса увеличивается на сумму оплаты."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_balance.return_value = {
            "id": regular_user.id,
            "name": "regular_user",
            "fullname": "Regular User",
            "balance": 500,
        }

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        reply_message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text="hello",
        )
        message = Message(
            message_id=2,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay 500",
            reply_to_message=reply_message,
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        mock_update_balance.assert_called_with(regular_user.id, 500)
        mock_update_fund.assert_called_with(500)
        mock_add_tx.assert_called_once()
        # Проверяем описание транзакции
        tx_args = mock_add_tx.call_args
        assert tx_args[0][0] == regular_user.id
        assert tx_args[0][1] == 500
        assert "Оплата" in tx_args[0][2]

    @patch("src.handlers.update_fund_balance")
    @patch("src.handlers.add_transaction")
    @patch("src.handlers.get_fund_balance", return_value=500)
    @patch("src.handlers.update_player_balance", return_value=True)
    @patch("src.handlers.get_player_balance")
    async def test_pay_shows_fund_in_reply(
        self,
        mock_get_balance,
        mock_update_balance,
        mock_get_fund,
        mock_add_tx,
        mock_update_fund,
        admin_user,
        regular_user,
        admin_service,
    ):
        """Ответ на /pay содержит баланс кассы."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_balance.return_value = {
            "id": regular_user.id,
            "name": "regular_user",
            "fullname": "Regular User",
            "balance": 500,
        }

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        reply_message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text="hello",
        )
        message = Message(
            message_id=2,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay 500",
            reply_to_message=reply_message,
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Касса" in method.text


# ── Handler-level /restore tests ────────────────────────────────────────────


@pytest.mark.asyncio
class TestRestoreCommand:
    """/restore должен менять баланс, но НЕ кассу."""

    @patch("src.handlers.update_fund_balance")
    @patch("src.handlers.add_transaction")
    @patch("src.handlers.update_player_balance", return_value=True)
    @patch("src.handlers.get_player_balance")
    @patch("src.handlers.ensure_player")
    async def test_restore_does_not_update_fund(
        self,
        mock_ensure,
        mock_get_balance,
        mock_update_balance,
        mock_add_tx,
        mock_update_fund,
        admin_user,
        regular_user,
        admin_service,
    ):
        """/restore не вызывает update_fund_balance."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_balance.return_value = {
            "id": regular_user.id,
            "name": "regular_user",
            "fullname": "Regular User",
            "balance": 0,
        }

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        reply_message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text="hello",
        )
        message = Message(
            message_id=2,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/restore 150",
            reply_to_message=reply_message,
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        mock_update_balance.assert_called_with(regular_user.id, 150)
        mock_update_fund.assert_not_called()
        mock_add_tx.assert_called_once()
        tx_args = mock_add_tx.call_args
        assert "Восстановление" in tx_args[0][2]

    @patch("src.handlers.update_fund_balance")
    @patch("src.handlers.add_transaction")
    @patch("src.handlers.update_player_balance", return_value=True)
    @patch("src.handlers.get_player_balance")
    @patch("src.handlers.ensure_player")
    async def test_restore_shows_no_fund_change(
        self,
        mock_ensure,
        mock_get_balance,
        mock_update_balance,
        mock_add_tx,
        mock_update_fund,
        admin_user,
        regular_user,
        admin_service,
    ):
        """Ответ /restore содержит 'Касса не изменена'."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_balance.return_value = {
            "id": regular_user.id,
            "name": "regular_user",
            "fullname": "Regular User",
            "balance": 150,
        }

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        reply_message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=regular_user,
            text="hello",
        )
        message = Message(
            message_id=2,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/restore 150",
            reply_to_message=reply_message,
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Касса не изменена" in method.text
        assert "восстановлен" in method.text


# ── Handler-level /pay Оплата зала tests ─────────────────────────────────────


@pytest.mark.asyncio
class TestHallPaymentHandler:
    """Тесты для /pay Оплата зала."""

    @patch("src.handlers.get_unpaid_halls")
    @patch("src.handlers.get_fund_balance", return_value=5000)
    async def test_hall_payment_shows_unpaid(
        self,
        mock_get_fund,
        mock_get_unpaid,
        admin_user,
        admin_service,
    ):
        """При /pay Оплата зала показываются неоплаченные залы."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_unpaid.return_value = [
            {"name": "Пятница", "place": "Зал №1", "monthly_cost": 6000},
            {"name": "Понедельник", "place": "Зал №2", "monthly_cost": 4000},
        ]

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay Оплата зала",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Неоплаченные залы" in method.text
        assert isinstance(method.reply_markup, InlineKeyboardMarkup)
        buttons = method.reply_markup.inline_keyboard
        assert len(buttons) == 2
        assert "6000" in buttons[0][0].text
        assert "hall_pay:" in buttons[0][0].callback_data

    @patch("src.handlers.get_unpaid_halls", return_value=[])
    async def test_hall_payment_all_paid(
        self,
        mock_get_unpaid,
        admin_user,
        admin_service,
    ):
        """Если все залы оплачены — сообщение об этом."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay Оплата зала",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Все залы" in method.text and "оплачены" in method.text

    @patch("src.handlers.get_unpaid_halls")
    @patch("src.handlers.get_fund_balance", return_value=5000)
    async def test_hall_payment_case_insensitive(
        self,
        mock_get_fund,
        mock_get_unpaid,
        admin_user,
        admin_service,
    ):
        """/pay ОПЛАТА ЗАЛА (в верхнем регистре) тоже работает."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        mock_get_unpaid.return_value = [
            {"name": "Пятница", "place": "Зал №1", "monthly_cost": 6000},
        ]

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/pay ОПЛАТА ЗАЛА",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Неоплаченные залы" in method.text


# ── Handler-level /balance fund display ──────────────────────────────────────


@pytest.mark.asyncio
class TestBalanceFundDisplay:
    """/balance для админа показывает кассу."""

    @patch("src.handlers.get_fund_balance", return_value=3500)
    @patch("src.handlers.get_players_with_balance", return_value=[])
    async def test_balance_shows_fund(
        self,
        mock_get_players,
        mock_get_fund,
        admin_user,
        admin_service,
    ):
        """Администратор видит баланс кассы в ответе /balance."""
        bot = AsyncMock(spec=Bot)
        dp = Dispatcher()

        dp.workflow_data.update({
            "admin_service": admin_service,
            "bot_state_service": MagicMock(spec=BotStateService),
            "poll_service": MagicMock(spec=PollService),
        })

        register_handlers(dp, bot)

        chat = Chat(id=-1001234567890, type="supergroup")
        message = Message(
            message_id=1,
            date=MagicMock(),
            chat=chat,
            from_user=admin_user,
            text="/balance",
        )

        await dp.feed_update(bot, Update(update_id=1, message=message))

        assert bot.called
        method = bot.call_args.args[0]
        assert "Касса" in method.text
        assert "3500" in method.text


# ── Subscription calculation tests (unit) ────────────────────────────────────


class TestSubscriptionCalculation:
    """Тесты для расчёта абонемента (DB-уровень, без Telegram API)."""

    def test_subscription_deducts_from_balance(self, temp_db):
        """Списание абонемента уменьшает баланс подписчика."""
        init_db()
        user_id = 100
        ensure_player(user_id=user_id, name="test", fullname="Test Player")

        # Списываем цену абонемента (в новой модели — 400-500₽ за зал)
        per_person = 450
        update_player_balance(user_id, -per_person)

        player = get_player_balance(user_id)
        assert player is not None
        assert player["balance"] == -450

    def test_subscription_fund_not_changed(self, temp_db):
        """При списании абонемента касса НЕ меняется."""
        init_db()
        update_fund_balance(2000)  # начальная касса

        user_id = 100
        ensure_player(user_id=user_id, name="test", fullname="Test Player")

        # Имитируем списание абонемента (баланс минусуется, касса нет)
        update_player_balance(user_id, -450)

        assert get_fund_balance() == 2000  # касса не изменилась

    def test_subscription_combo_deducts_from_balance(self, temp_db):
        """Подписчик на 2 зала (комбо) — списание комбо-цены."""
        init_db()
        # Комбо-цена = price_per_hall * 1.7 ≈ 770₽
        combo_price = 770

        user_id = 200
        ensure_player(user_id=user_id, name="sub_user", fullname="Sub User")
        update_player_balance(user_id, -combo_price)

        player = get_player_balance(user_id)
        assert player is not None
        assert player["balance"] == -770


# ── Integration test: full pay → fund → hall payment flow ────────────────────


class TestFullPaymentFlow:
    """Интеграционный тест: полный цикл оплаты."""

    def test_casual_payment_flow(self, temp_db):
        """Полный цикл: списание за игру → оплата → оплата зала."""
        init_db()

        # Настройка
        save_poll_template({
            "name": "Пятница",
            "message": "Игра",
            "cost": 150,
            "monthly_cost": 6000,
        })
        user_id = 100
        ensure_player(user_id=user_id, name="casual", fullname="Casual Player")

        # 1. Автоматическое списание за игру (бот делает при закрытии голосования)
        update_player_balance(user_id, -150)
        add_transaction(user_id, -150, "Зал: Пятница (07.02.2026)", "Пятница")

        player = get_player_balance(user_id)
        assert player["balance"] == -150
        assert get_fund_balance() == 0  # Касса не меняется

        # 2. Игрок оплачивает через /pay
        update_player_balance(user_id, 150)
        update_fund_balance(150)
        add_transaction(user_id, 150, "Оплата (admin: @admin)")

        player = get_player_balance(user_id)
        assert player["balance"] == 0
        assert get_fund_balance() == 150  # Касса увеличилась

        # 3. Оплата зала из кассы
        record_hall_payment("Пятница", "2026-02", 6000)
        update_fund_balance(-6000)

        assert get_fund_balance() == -5850  # Касса ушла в минус
        unpaid = get_unpaid_halls("2026-02")
        assert len(unpaid) == 0  # Зал оплачен

    def test_restore_does_not_affect_fund(self, temp_db):
        """Восстановление баланса не влияет на кассу."""
        init_db()
        user_id = 200
        ensure_player(user_id=user_id, name="noshow", fullname="No Show")

        # Списание за игру
        update_player_balance(user_id, -150)
        add_transaction(user_id, -150, "Зал: Пятница (07.02.2026)")

        # Восстановление (/restore)
        update_player_balance(user_id, 150)
        add_transaction(user_id, 150, "Восстановление (admin: @admin)")
        # НЕ вызываем update_fund_balance!

        player = get_player_balance(user_id)
        assert player["balance"] == 0
        assert get_fund_balance() == 0  # Касса не изменилась

    def test_subscription_then_hall_payment(self, temp_db):
        """Абонемент списывается с баланса, затем зал оплачивается из кассы."""
        init_db()
        save_poll_template({
            "name": "Пятница",
            "message": "Игра",
            "cost": 150,
            "monthly_cost": 6000,
        })

        # Касса накопилась от предыдущих оплат
        update_fund_balance(2000)

        # Подписчики: 10 человек
        num_subs = 10
        for i in range(num_subs):
            uid = 1000 + i
            ensure_player(user_id=uid, name=f"sub{i}", fullname=f"Sub {i}")

        # В новой модели цена абонемента — 400-500₽ за зал (единая)
        per_person = 450  # примерная цена

        # Списание с подписчиков (касса НЕ меняется)
        for i in range(num_subs):
            uid = 1000 + i
            update_player_balance(uid, -per_person)
            add_transaction(uid, -per_person, "Абонемент: Пятница (2026-03)")

        assert get_fund_balance() == 2000  # Касса не изменилась

        # Оплата зала из кассы
        record_hall_payment("Пятница", "2026-03", 6000)
        update_fund_balance(-6000)

        assert get_fund_balance() == -4000

        # Подписчики оплачивают свои долги
        for i in range(num_subs):
            uid = 1000 + i
            update_player_balance(uid, per_person)
            update_fund_balance(per_person)

        assert get_fund_balance() == 2000 + (per_person * num_subs) - 6000
