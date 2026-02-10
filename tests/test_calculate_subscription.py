"""Тесты для бюджетного расчёта абонемента (calculate_subscription)."""

import pytest
from rich import print

from src.services.poll_service import (
    AVG_SINGLES_PER_GAME,
    COMBO_DISCOUNT_COEFF,
    DEFAULT_SUB_PRICE,
    GAMES_PER_MONTH,
    MAX_SUB_PRICE,
    MIN_SUB_PRICE,
    SAFETY_K,
    SAVINGS_BUFFER,
    SINGLE_GAME_PRICE,
    TARGET_GROWTH,
    calculate_subscription,
)
from src.types import PollTemplate


def _make_poll(
    name: str,
    cost: int = 150,
    monthly_cost: int = 0,
) -> PollTemplate:
    """Фабрика для создания PollTemplate с минимальными полями."""
    return PollTemplate(
        name=name,
        message=f"Игра {name}",
        open_day="mon",
        open_hour_utc=10,
        open_minute_utc=0,
        game_day="tue",
        game_hour_utc=18,
        game_minute_utc=0,
        cost=cost,
        monthly_cost=monthly_cost,
    )


# ── Пустые / граничные входные данные ────────────────────────────────────────


class TestEmptyInputs:
    """Пустые и вырожденные входные данные."""

    def test_no_paid_polls(self):
        """Нет платных опросов — пустой результат."""
        result = calculate_subscription([], {})
        assert result.hall_breakdown == []
        assert result.subscriber_charges == []
        assert result.price_per_hall == DEFAULT_SUB_PRICE
        assert result.combo_price == round(DEFAULT_SUB_PRICE * COMBO_DISCOUNT_COEFF / 10) * 10

    def test_no_votes(self):
        """Есть платные опросы, но никто не голосовал."""
        polls = [_make_poll("Пятница", monthly_cost=6000)]
        result = calculate_subscription(polls, {})

        assert len(result.hall_breakdown) == 1
        h = result.hall_breakdown[0]
        assert h.name == "Пятница"
        assert h.monthly_cost == 6000
        assert h.num_subs == 0
        assert h.per_person == 0
        assert result.subscriber_charges == []
        # Цена по умолчанию, так как нет подписчиков (divisor == 0)
        assert result.price_per_hall == DEFAULT_SUB_PRICE

    def test_empty_votes_set(self):
        """Запись в votes_by_poll есть, но множество пустое."""
        polls = [_make_poll("Пятница", monthly_cost=6000)]
        result = calculate_subscription(polls, {"Пятница": set()})

        assert result.hall_breakdown[0].num_subs == 0
        assert result.hall_breakdown[0].per_person == 0
        assert result.subscriber_charges == []


# ── monthly_cost = 0 ────────────────────────────────────────────────────────


class TestZeroMonthlyCost:
    """Залы с нулевой или отсутствующей monthly_cost."""

    def test_monthly_cost_zero(self):
        """Зал с monthly_cost=0 — per_person=0, нет списаний."""
        polls = [_make_poll("Среда", monthly_cost=0)]
        votes = {"Среда": {1, 2, 3}}
        result = calculate_subscription(polls, votes)

        assert len(result.hall_breakdown) == 1
        assert result.hall_breakdown[0].per_person == 0
        assert result.subscriber_charges == []

    def test_monthly_cost_missing(self):
        """Зал без поля monthly_cost (по умолчанию 0)."""
        poll: PollTemplate = PollTemplate(
            name="Среда",
            message="Бесплатная игра",
            open_day="wed",
            open_hour_utc=10,
            open_minute_utc=0,
            game_day="wed",
            game_hour_utc=18,
            game_minute_utc=0,
            cost=0,
        )
        votes = {"Среда": {1}}
        result = calculate_subscription([poll], votes)

        assert result.hall_breakdown[0].per_person == 0
        assert result.subscriber_charges == []


# ── Ценовой диапазон 400-500 ─────────────────────────────────────────────────


class TestPriceRange:
    """Цена абонемента за 1 зал должна быть в диапазоне 400-500 руб."""

    def test_price_within_bounds(self):
        """Цена за 1 зал попадает в [400, 500]."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3, 4, 5},
            "Четверг": {1, 2, 3, 6, 7},
        }
        result = calculate_subscription(polls, votes)

        assert MIN_SUB_PRICE <= result.price_per_hall <= MAX_SUB_PRICE
        assert result.price_per_hall == MAX_SUB_PRICE

    def test_price_rounded_to_10(self):
        """Цена округлена до 10 руб."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3},
            "Четверг": {1, 4, 5},
        }
        result = calculate_subscription(polls, votes)

        assert result.price_per_hall % 10 == 0
        assert result.combo_price % 10 == 0
        assert result.price_per_hall == MAX_SUB_PRICE

    def test_price_clamped_at_min(self):
        """При большом числе подписчиков цена не опускается ниже MIN."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        # Очень много подписчиков — raw_price будет мал
        votes = {
            "Понедельник": set(range(100)),
            "Четверг": set(range(100)),
        }
        result = calculate_subscription(polls, votes, fund_balance=50000)

        assert result.price_per_hall == MIN_SUB_PRICE

    def test_price_clamped_at_max(self):
        """При малом числе подписчиков цена не поднимается выше MAX."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        # Мало подписчиков, казна пуста — raw_price будет велик
        votes = {
            "Понедельник": {1},
            "Четверг": {2},
        }
        result = calculate_subscription(polls, votes, fund_balance=0)

        assert result.price_per_hall == MAX_SUB_PRICE

    def test_all_halls_same_per_person(self):
        """Единая цена: per_person одинаков для всех платных залов."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=5000),
        ]
        votes = {
            "Понедельник": {1, 2, 3},
            "Четверг": {1, 4, 5},
        }
        result = calculate_subscription(polls, votes)

        paid_halls = [h for h in result.hall_breakdown if h.monthly_cost > 0 and h.num_subs > 0]
        prices = {h.per_person for h in paid_halls}
        assert len(prices) == 1  # все цены одинаковые


# ── Комбо-скидка ─────────────────────────────────────────────────────────────


class TestComboDiscount:
    """Подписчики на 2+ зала получают скидку."""

    def test_combo_cheaper_than_two_singles(self):
        """Комбо дешевле, чем два абонемента по отдельности."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3},
            "Четверг": {1, 4, 5},
        }
        result = calculate_subscription(polls, votes)
        print(result)

        assert result.combo_price < result.price_per_hall * 2

    def test_combo_price_is_coeff_times_single(self):
        """Комбо = price_per_hall * COMBO_DISCOUNT_COEFF, округлено до 10."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2},
            "Четверг": {1, 3},
        }
        result = calculate_subscription(polls, votes)

        expected_combo = round(result.price_per_hall * COMBO_DISCOUNT_COEFF / 10) * 10
        assert result.combo_price == expected_combo

    def test_combo_subscriber_charged_combo_price(self):
        """Подписчик на оба зала платит combo_price."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3},
            "Четверг": {1, 2, 4},
        }
        result = calculate_subscription(polls, votes)

        charges_by_id = {c.user_id: c for c in result.subscriber_charges}
        # user 1, 2 — комбо
        assert charges_by_id[1].total == result.combo_price
        assert charges_by_id[2].total == result.combo_price
        assert sorted(charges_by_id[1].halls) == ["Понедельник", "Четверг"]
        # user 3 — только Понедельник
        assert charges_by_id[3].total == result.price_per_hall
        assert charges_by_id[3].halls == ["Понедельник"]
        # user 4 — только Четверг
        assert charges_by_id[4].total == result.price_per_hall
        assert charges_by_id[4].halls == ["Четверг"]

    def test_all_combo_subscribers(self):
        """Все подписчики на оба зала — все платят combo_price."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3},
            "Четверг": {1, 2, 3},
        }
        result = calculate_subscription(polls, votes)

        for charge in result.subscriber_charges:
            assert charge.total == result.combo_price
            assert len(charge.halls) == 2

    def test_all_single_hall_subscribers(self):
        """Нет комбо — все платят price_per_hall."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2},
            "Четверг": {3, 4},
        }
        result = calculate_subscription(polls, votes)

        for charge in result.subscriber_charges:
            assert charge.total == result.price_per_hall
            assert len(charge.halls) == 1


# ── Влияние казны (fund_balance) ──────────────────────────────────────────────


class TestFundBalance:
    """Баланс казны влияет на целевую сумму сбора."""

    def _two_halls_with_votes(self):
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3, 4, 5},
            "Четверг": {1, 2, 3, 6, 7},
        }
        return polls, votes

    def test_low_savings_higher_price(self):
        """Мало денег в казне → прирост TARGET_GROWTH → цена выше."""
        polls, votes = self._two_halls_with_votes()
        result_low = calculate_subscription(polls, votes, fund_balance=0)
        result_high = calculate_subscription(polls, votes, fund_balance=SAVINGS_BUFFER * 2)

        assert result_low.price_per_hall >= result_high.price_per_hall

    def test_full_savings_no_growth(self):
        """Казна >= SAVINGS_BUFFER → adjustment = 0."""
        polls, votes = self._two_halls_with_votes()
        result_zero = calculate_subscription(polls, votes, fund_balance=0)
        result_full = calculate_subscription(polls, votes, fund_balance=SAVINGS_BUFFER)

        # При полной казне цена не должна быть выше, чем при пустой
        assert result_full.price_per_hall <= result_zero.price_per_hall

    def test_excess_savings_negative_adjustment(self):
        """Казна >= 1.5 * SAVINGS_BUFFER → adjustment = -1000 → цена ещё ниже."""
        polls, votes = self._two_halls_with_votes()
        result_full = calculate_subscription(
            polls, votes, fund_balance=SAVINGS_BUFFER
        )
        result_excess = calculate_subscription(
            polls, votes, fund_balance=int(SAVINGS_BUFFER * 1.5)
        )

        assert result_excess.price_per_hall <= result_full.price_per_hall


# ── Один зал ─────────────────────────────────────────────────────────────────


class TestSingleHall:
    """Один платный зал — нет комбо, все платят price_per_hall."""

    def test_single_hall_no_combo(self):
        """Один зал — все подписчики single-hall."""
        polls = [_make_poll("Пятница", monthly_cost=6000)]
        votes = {"Пятница": {1, 2, 3, 4, 5}}
        result = calculate_subscription(polls, votes)

        assert result.price_per_hall > 0
        for charge in result.subscriber_charges:
            assert charge.total == result.price_per_hall
            assert charge.halls == ["Пятница"]

    def test_single_hall_price_in_range(self):
        """Один зал — цена в диапазоне [400, 500]."""
        polls = [_make_poll("Пятница", monthly_cost=6000)]
        votes = {"Пятница": set(range(10))}
        result = calculate_subscription(polls, votes)

        assert MIN_SUB_PRICE <= result.price_per_hall <= MAX_SUB_PRICE


# ── Смешанные залы (платные и бесплатные) ────────────────────────────────────


class TestMixedHalls:
    """Комбинация платных и бесплатных залов."""

    def test_free_hall_no_charges(self):
        """Бесплатный зал не влияет на цену и не создаёт списаний."""
        polls = [
            _make_poll("Среда", monthly_cost=0),
            _make_poll("Пятница", monthly_cost=6000),
        ]
        votes = {
            "Среда": {1, 2, 10, 11},
            "Пятница": {1, 2, 3},
        }
        result = calculate_subscription(polls, votes)

        # Среда: per_person == 0
        hall_by_name = {h.name: h for h in result.hall_breakdown}
        assert hall_by_name["Среда"].per_person == 0

        # Подписчики 10, 11 — только в бесплатном зале, не должно быть списаний
        charged_ids = {c.user_id for c in result.subscriber_charges}
        assert 10 not in charged_ids
        assert 11 not in charged_ids

    def test_mixed_zero_and_paid(self):
        """Один зал бесплатный, другой платный — списание только за платный."""
        polls = [
            _make_poll("Среда", monthly_cost=0),
            _make_poll("Пятница", monthly_cost=6000),
        ]
        votes = {
            "Среда": {1, 2},
            "Пятница": {1, 2, 3},
        }
        result = calculate_subscription(polls, votes)

        assert len(result.subscriber_charges) == 3
        for charge in result.subscriber_charges:
            assert charge.total == result.price_per_hall
            assert charge.halls == ["Пятница"]


# ── Финансовый прогноз ────────────────────────────────────────────────────────


class TestProjectedSavings:
    """Проверка прогноза казны."""

    def test_projected_savings_formula(self):
        """projected_savings = fund + sub_income + singles_income - total_rent."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3, 4, 5},
            "Четверг": {1, 2, 3, 6, 7},
        }
        fund = 2000
        result = calculate_subscription(polls, votes, fund_balance=fund)

        total_sub_income = sum(c.total for c in result.subscriber_charges)
        total_rent = sum(h.monthly_cost for h in result.hall_breakdown if h.monthly_cost > 0)
        expected = fund + total_sub_income + result.expected_singles_income - total_rent

        assert result.projected_savings == expected

    def test_expected_singles_income_formula(self):
        """Проверяем формулу дохода от разовых игроков."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1},
            "Четверг": {2},
        }
        result = calculate_subscription(polls, votes)

        num_halls = 2
        expected = round(
            AVG_SINGLES_PER_GAME * SINGLE_GAME_PRICE * GAMES_PER_MONTH * num_halls * SAFETY_K
        )
        assert result.expected_singles_income == expected


# ── Структура результата ─────────────────────────────────────────────────────


class TestResultStructure:
    """Проверка корректности структуры результата."""

    def test_hall_breakdown_order_matches_input(self):
        """hall_breakdown сохраняет порядок входных polls."""
        polls = [
            _make_poll("Среда", monthly_cost=0),
            _make_poll("Пятница", monthly_cost=6000),
            _make_poll("Понедельник", monthly_cost=4000),
        ]
        votes = {
            "Пятница": {1},
            "Понедельник": {2},
        }
        result = calculate_subscription(polls, votes)

        names = [h.name for h in result.hall_breakdown]
        assert names == ["Среда", "Пятница", "Понедельник"]

    def test_subscriber_charges_sorted_by_user_id(self):
        """subscriber_charges отсортированы по user_id."""
        polls = [_make_poll("Пятница", monthly_cost=6000)]
        votes = {"Пятница": {99, 5, 42, 1}}
        result = calculate_subscription(polls, votes)

        user_ids = [c.user_id for c in result.subscriber_charges]
        assert user_ids == sorted(user_ids)

    def test_halls_in_charge_sorted_alphabetically(self):
        """Список залов в SubscriberCharge отсортирован по алфавиту."""
        polls = [
            _make_poll("Яблоко", monthly_cost=1000),
            _make_poll("Арбуз", monthly_cost=2000),
        ]
        votes = {
            "Яблоко": {1},
            "Арбуз": {1},
        }
        result = calculate_subscription(polls, votes)

        assert result.subscriber_charges[0].halls == ["Арбуз", "Яблоко"]

    def test_dataclass_fields(self):
        """Все поля SubscriptionResult заполнены."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {"Понедельник": {1}, "Четверг": {1}}
        result = calculate_subscription(polls, votes)

        assert isinstance(result.price_per_hall, int)
        assert isinstance(result.combo_price, int)
        assert isinstance(result.expected_singles_income, int)
        assert isinstance(result.projected_savings, int)

        h = result.hall_breakdown[0]
        assert isinstance(h.name, str)
        assert isinstance(h.monthly_cost, int)
        assert isinstance(h.num_subs, int)
        assert isinstance(h.per_person, int)

        c = result.subscriber_charges[0]
        assert isinstance(c.user_id, int)
        assert isinstance(c.total, int)
        assert isinstance(c.halls, list)


# ── Реальный сценарий (из test_cash.py) ──────────────────────────────────────


class TestRealWorldScenario:
    """Реалистичные сценарии использования."""

    def test_typical_two_halls(self):
        """
        Типичный сценарий: два зала по 6000₽.
        5 подписчиков на ПН, 4 на ЧТ, 3 комбо.
        Цена за 1 зал должна быть 400-500₽, комбо со скидкой.
        """
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        # Комбо: {1, 2, 3}, только ПН: {4, 5}, только ЧТ: {6}
        votes = {
            "Понедельник": {1, 2, 3, 4, 5},
            "Четверг": {1, 2, 3, 6},
        }
        result = calculate_subscription(polls, votes, fund_balance=0)
        print(result)

        assert MIN_SUB_PRICE <= result.price_per_hall <= MAX_SUB_PRICE
        assert result.combo_price < result.price_per_hall * 2
        assert result.combo_price > result.price_per_hall
        assert len(result.subscriber_charges) == 6

        charges_by_id = {c.user_id: c for c in result.subscriber_charges}
        # Комбо
        for uid in [1, 2, 3]:
            assert charges_by_id[uid].total == result.combo_price
        # Одиночные
        for uid in [4, 5, 6]:
            assert charges_by_id[uid].total == result.price_per_hall

    def test_scenario_from_test_cash(self):
        """
        Сценарий из test_cash.py: n_a=5, n_b=4, n_combo=8, savings=0.
        Ожидаем цену в диапазоне 400-500.
        """
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        # n_combo = 8: users 1-8 подписаны на оба зала
        # n_a = 5: users 9-13 только на ПН
        # n_b = 4: users 14-17 только на ЧТ
        combo_users = set(range(1, 9))
        only_mon = set(range(9, 14))
        only_thu = set(range(14, 18))

        votes = {
            "Понедельник": combo_users | only_mon,
            "Четверг": combo_users | only_thu,
        }
        result = calculate_subscription(polls, votes, fund_balance=0)

        assert MIN_SUB_PRICE <= result.price_per_hall <= MAX_SUB_PRICE
        assert result.combo_price == round(
            result.price_per_hall * COMBO_DISCOUNT_COEFF / 10
        ) * 10
        assert len(result.subscriber_charges) == 17

    def test_with_free_wednesday(self):
        """
        Два платных зала + бесплатный.
        Бесплатный зал не влияет на расчёт.
        """
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Среда", monthly_cost=0),
            _make_poll("Четверг", monthly_cost=6000),
        ]
        votes = {
            "Понедельник": {1, 2, 3, 4, 5},
            "Среда": {1, 2, 10, 11, 12},
            "Четверг": {1, 2, 3, 6, 7},
        }
        result = calculate_subscription(polls, votes)

        hall_by_name = {h.name: h for h in result.hall_breakdown}
        assert hall_by_name["Среда"].per_person == 0
        assert hall_by_name["Понедельник"].per_person == result.price_per_hall
        assert hall_by_name["Четверг"].per_person == result.price_per_hall

        # Подписчики бесплатного зала не платят
        charged_ids = {c.user_id for c in result.subscriber_charges}
        assert 10 not in charged_ids
        assert 11 not in charged_ids
        assert 12 not in charged_ids


# ── Голоса за неизвестные опросы ──────────────────────────────────────────────


class TestVotePollMismatch:
    """Случаи расхождения между голосами и шаблонами."""

    def test_votes_for_unknown_poll(self):
        """Голоса за опрос, которого нет в paid_polls — игнорируются."""
        polls = [_make_poll("Пятница", monthly_cost=6000)]
        votes = {
            "Пятница": {1, 2},
            "Неизвестный": {3},
        }
        result = calculate_subscription(polls, votes)

        charges_by_id = {c.user_id: c for c in result.subscriber_charges}
        assert 3 not in charges_by_id

    def test_poll_without_votes(self):
        """Платный опрос без голосов — в hall_breakdown, но без списаний."""
        polls = [
            _make_poll("Понедельник", monthly_cost=6000),
            _make_poll("Пятница", monthly_cost=4000),
        ]
        votes = {"Пятница": {1, 2}}
        result = calculate_subscription(polls, votes)

        hall_by_name = {h.name: h for h in result.hall_breakdown}
        assert hall_by_name["Понедельник"].num_subs == 0
        assert hall_by_name["Понедельник"].per_person == 0

        # Списание только с подписчиков Пятницы
        for charge in result.subscriber_charges:
            assert charge.halls == ["Пятница"]


# ── Отрицательный monthly_cost ────────────────────────────────────────────────


class TestNegativeMonthlyCost:
    """Отрицательная monthly_cost обрабатывается как нулевая."""

    def test_negative_monthly_cost_ignored(self):
        """Зал с отрицательной monthly_cost — per_person=0, нет списаний."""
        polls = [_make_poll("Глюк", monthly_cost=-500)]
        votes = {"Глюк": {1, 2}}
        result = calculate_subscription(polls, votes)

        assert result.hall_breakdown[0].per_person == 0
        assert result.subscriber_charges == []
