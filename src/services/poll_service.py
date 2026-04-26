"""Сервис для управления состоянием опросов."""

from __future__ import annotations

import asyncio
import json
import logging
from asyncio import Task
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramMigrateToChat,
    TelegramNetworkError,
)

from ..config import (
    ADMIN_USER_ID,
    MIN_PLAYERS,
    PAYMENT_BANK,
    PAYMENT_NAME,
    PAYMENT_PHONE,
    POLL_OPTIONS,
)
from ..db import (
    POLL_STATE_KEY,
    add_transaction,
    close_game,
    create_backup,
    create_game,
    ensure_player,
    get_fund_balance,
    get_game,
    get_open_game_by_template_id,
    get_open_games,
    get_open_monthly_game,
    get_player_balance,
    get_poll_templates,
    get_single_game_income_stats,
    load_monthly_votes,
    load_state,
    save_game_participants,
    save_poll_template,
    save_state,
    update_game_info_message,
    update_game_last_info_text,
    update_player_balance,
)
from ..poll import PollData, PollRoster, VoterInfo, build_regular_poll_roster
from ..types import (
    HallBreakdown,
    PollCreationSpec,
    PollTemplate,
    SubscriberCharge,
    SubscriptionResult,
)
from ..utils import (
    call_with_network_retry,
    count_games_in_month,
    escape_html,
    format_player_link,
    get_next_month_str,
    retry_async,
    save_error_dump,
)

# ── Константы бюджетного расчёта абонемента ──────────────────────────────────
AVG_SINGLES_PER_GAME = 7  # Среднее кол-во разовых игроков за игру
SINGLE_GAME_PRICE = 150  # Цена разового входа (руб.)
SINGLE_GAME_STATS_MONTHS_BACK = 3  # Окно истории для прогноза разовых игроков
HALL_SINGLE_GAME_STATS_MIN_GAMES = 2  # Минимум игр для статистики конкретного зала
GLOBAL_SINGLE_GAME_STATS_MIN_GAMES = 4  # Минимум игр для общей статистики
GAMES_PER_MONTH = 4  # fallback для '*'/некорректного дня
SAFETY_K = 0.7  # Коэффициент надёжности (риск неявки)
TARGET_GROWTH = 1000  # Желаемый прирост казны в месяц (руб.)
SAVINGS_BUFFER = 6000  # Целевая «подушка» казны (руб.)
COMBO_DISCOUNT_COEFF = 1.7  # Комбо = 1.7× одного зала (скидка ~15%)
MIN_SUB_PRICE = 400  # Минимальная цена абонемента за 1 зал
MAX_SUB_PRICE = 500  # Максимальная цена абонемента за 1 зал
DEFAULT_SUB_PRICE = 450  # Цена по умолчанию, если нет подписчиков
PLAYERS_LIST_UPDATE_DELAY_SECONDS = 5  # Задержка перед обновлением списка игроков


def calculate_subscription(
    paid_polls: list[PollTemplate],
    votes_by_poll: dict[str, set[int]],
    target_month: str | None = None,
    fund_balance: int = 0,
    single_game_income_stats: dict[str, Any] | None = None,
) -> SubscriptionResult:
    """
    Бюджетный расчёт стоимости абонемента без побочных эффектов.

    Держит единую цену абонемента за каждый зал в диапазоне 400-500 руб.
    Недостающая часть аренды покрывается ожидаемым доходом с разовых игроков.
    Подписчики на 2+ зала получают комбо-скидку (~15%).

    Args:
        paid_polls: шаблоны залов, участвующих в расчёте абонемента.
        votes_by_poll: маппинг ``poll_name → {user_id, …}`` из голосования.
        target_month: месяц расчёта в формате ``YYYY-MM``.
        fund_balance: текущий баланс казны (влияет на целевую сумму сбора).
        single_game_income_stats: исторический доход с разовых игроков из БД.

    Returns:
        :class:`SubscriptionResult` с разбивкой по залам и списаниями по
        подписчикам.
    """
    # --- 1. Собираем данные по залам ---
    if not target_month:
        target_month = datetime.now().strftime("%Y-%m")

    paid_poll_rows: list[HallBreakdown] = []

    for template in paid_polls:
        name = str(template.get("name", ""))
        poll_template_id = template["id"]
        cost_per_game = int(template.get("cost_per_game", 0) or 0)
        game_day = str(template.get("game_day", "*") or "*")
        games_in_month = count_games_in_month(game_day, target_month, GAMES_PER_MONTH)
        monthly_rent = cost_per_game * games_in_month
        subs_set = votes_by_poll.get(name, set())
        num_subs = len(subs_set)

        if monthly_rent <= 0:
            continue

        paid_poll_rows.append(
            HallBreakdown(
                poll_template_id=poll_template_id,
                name=name,
                cost_per_game=cost_per_game,
                games_in_month=games_in_month,
                monthly_rent=monthly_rent,
                num_subs=num_subs,
                per_person=0,  # заполним ниже
            )
        )

    total_rent = sum(h.monthly_rent for h in paid_poll_rows)

    # --- 2. Классифицируем подписчиков: single-hall vs combo ---
    user_halls: dict[int, list[str]] = {}
    for hall in paid_poll_rows:
        for uid in votes_by_poll.get(hall.name, set()):
            user_halls.setdefault(uid, []).append(hall.name)

    n_combo = sum(1 for halls in user_halls.values() if len(halls) >= 2)
    n_single = sum(1 for halls in user_halls.values() if len(halls) == 1)

    # --- 3. Прогноз дохода с разовых игроков ---
    # Историческая статистика приходит из БД как средний доход с разовых за одну
    # закрытую платную игру. Нам нужен прогноз на будущий месяц, поэтому для
    # каждого зала умножаем его средний доход за игру на число игр этого зала.
    #
    # Fallback сохраняет старое поведение: если истории недостаточно, считаем,
    # что на каждой игре будет AVG_SINGLES_PER_GAME разовых игроков по
    # SINGLE_GAME_PRICE рублей.
    fallback_income_per_game = AVG_SINGLES_PER_GAME * SINGLE_GAME_PRICE
    global_stats = (
        single_game_income_stats.get("global", {})
        if isinstance(single_game_income_stats, dict)
        else {}
    )
    by_poll_template_id = (
        single_game_income_stats.get("by_poll_template_id", {})
        if isinstance(single_game_income_stats, dict)
        else {}
    )
    global_games_count = int(global_stats.get("games_count", 0) or 0)
    global_avg_income = float(global_stats.get("avg_income_per_game", 0.0) or 0.0)

    expected_singles_income_before_safety = 0.0
    for hall in paid_poll_rows:
        avg_income_per_game = fallback_income_per_game
        hall_stats = by_poll_template_id.get(hall.poll_template_id)

        # Приоритет 1: статистика конкретного зала. Она точнее общей, потому что
        # понедельник и пятница могут стабильно собирать разное число разовых
        # игроков. Используем её только после минимального числа закрытых игр,
        # чтобы одна случайная игра не задавала цену абонемента.
        hall_games_count = (
            int(hall_stats.get("games_count", 0) or 0) if hall_stats else 0
        )
        if hall_games_count >= HALL_SINGLE_GAME_STATS_MIN_GAMES:
            avg_income_per_game = float(
                hall_stats.get("avg_income_per_game", 0.0) or 0.0
            )
        # Приоритет 2: общая статистика по платным играм. Это полезно для новых
        # залов без собственной истории, но только когда общая выборка уже не
        # совсем мала.
        elif global_games_count >= GLOBAL_SINGLE_GAME_STATS_MIN_GAMES:
            avg_income_per_game = global_avg_income

        # На этом шаге сумма ещё без SAFETY_K: это "средний исторический доход",
        # растянутый на календарь будущего месяца для конкретного зала.
        expected_singles_income_before_safety += (
            avg_income_per_game * hall.games_in_month
        )

    # SAFETY_K применяем один раз ко всему прогнозу как консервативную скидку на
    # риск неявок/слабого месяца. Старый расчет делал то же самое.
    expected_singles_income = round(expected_singles_income_before_safety * SAFETY_K)

    # --- 4. Корректировка целевой суммы по состоянию казны ---
    if fund_balance >= SAVINGS_BUFFER * 1.5:
        adjustment = -1000
    elif fund_balance >= SAVINGS_BUFFER:
        adjustment = 0
    else:
        adjustment = TARGET_GROWTH

    # --- 5. Сколько нужно собрать с подписчиков ---
    needed_from_subs = total_rent + adjustment - expected_singles_income

    # --- 6. Расчёт единой цены за 1 зал ---
    divisor = n_single + (COMBO_DISCOUNT_COEFF * n_combo)

    if divisor > 0:
        raw_price = needed_from_subs / divisor
    else:
        raw_price = DEFAULT_SUB_PRICE

    # Ограничиваем диапазоном и округляем до 10 руб.
    price_per_hall = max(MIN_SUB_PRICE, min(MAX_SUB_PRICE, raw_price))
    price_per_hall = round(price_per_hall / 10) * 10

    # --- 7. Комбо-цена ---
    combo_price = round((price_per_hall * COMBO_DISCOUNT_COEFF) / 10) * 10

    # --- 8. Заполняем per_person в paid_polls ---
    for h in paid_poll_rows:
        if h.num_subs > 0:
            h.per_person = price_per_hall

    # --- 9. Формируем списания ---
    subscriber_charges: list[SubscriberCharge] = []
    for uid, halls in sorted(user_halls.items()):
        is_combo = len(halls) >= 2
        total = combo_price if is_combo else price_per_hall
        subscriber_charges.append(
            SubscriberCharge(
                user_id=uid,
                total=total,
                halls=sorted(halls),
            )
        )

    # --- 10. Финансовый прогноз ---
    total_sub_income = sum(c.total for c in subscriber_charges)
    projected_savings = (
        fund_balance + total_sub_income + expected_singles_income - total_rent
    )

    return SubscriptionResult(
        paid_polls=paid_poll_rows,
        subscriber_charges=subscriber_charges,
        price_per_hall=price_per_hall,
        combo_price=combo_price,
        expected_singles_income=expected_singles_income,
        projected_savings=projected_savings,
    )


class PollService:
    """Сервис для управления опросами и их состоянием."""

    def __init__(self) -> None:
        """Инициализация сервиса опросов."""
        self._poll_data: dict[str, PollData] = {}
        self._update_tasks: dict[str, Task[None] | None] = {}

    async def _safe_send_message(
        self,
        bot: Bot,
        *,
        action_name: str,
        **kwargs,
    ) -> Any | None:
        return await call_with_network_retry(
            lambda: bot.send_message(**kwargs),
            action_name=action_name,
            tries=3,
            delay=2.0,
            backoff=2.0,
            max_delay=8.0,
            timeout=15.0,
            exceptions=(TelegramNetworkError, asyncio.TimeoutError, OSError),
            logger=logging.getLogger(__name__),
        )

    async def _notify_admin_failed_poll_persistence(
        self,
        bot: Bot,
        *,
        chat_id: int,
        poll_name: str,
        poll_id: str,
        poll_message_id: int,
        info_message_id: int | None,
    ) -> None:
        """Уведомляет администратора о poll, созданном в Telegram, но не сохранённом в БД."""
        if not ADMIN_USER_ID:
            logging.warning(
                "⚠️ ADMIN_USER_ID не задан, уведомление о несохранённом опросе не отправлено"
            )
            return

        lines = [
            "🚨 <b>Инцидент при создании опроса</b>",
            "",
            f"Опрос <b>{escape_html(poll_name)}</b> был отправлен в чат, но не сохранился в БД.",
            "Бот не будет управлять этим опросом после перезапуска.",
            "",
            f"chat_id: <code>{chat_id}</code>",
            f"poll_id: <code>{escape_html(poll_id)}</code>",
            f"poll_message_id: <code>{poll_message_id}</code>",
        ]
        if info_message_id is not None:
            lines.append(f"info_message_id: <code>{info_message_id}</code>")

        try:
            await self._safe_send_message(
                bot,
                chat_id=ADMIN_USER_ID,
                text="\n".join(lines),
                parse_mode="HTML",
                action_name="notify admin about failed poll persistence",
            )
            logging.info(
                "✅ Уведомление об инциденте по опросу '%s' отправлено админу",
                poll_name,
            )
        except (
            TelegramAPIError,
            TelegramNetworkError,
            asyncio.TimeoutError,
            OSError,
        ):
            logging.exception(
                "❌ Не удалось отправить админу уведомление о несохранённом опросе '%s'",
                poll_name,
            )

    def get_poll_data(self, poll_id: str) -> PollData | None:
        """Получить данные опроса по ID."""
        return self._poll_data.get(poll_id)

    def has_poll(self, poll_id: str) -> bool:
        """Проверить существование опроса."""
        return poll_id in self._poll_data

    def get_all_polls(self) -> dict[str, PollData]:
        """Получить все активные опросы."""
        return self._poll_data.copy()

    def has_active_polls(self) -> bool:
        """Проверить наличие активных опросов."""
        return len(self._poll_data) > 0

    def get_first_poll(self) -> tuple[str, PollData] | None:
        """Получить первый активный опрос."""
        if not self._poll_data:
            return None
        poll_id = list(self._poll_data.keys())[0]
        return poll_id, self._poll_data[poll_id]

    @staticmethod
    def _format_monthly_option(
        template: PollTemplate,
        *,
        utc_tz: ZoneInfo,
        moscow_tz: ZoneInfo,
    ) -> str:
        """Форматирует подпись опции месячного опроса."""
        name = str(template.get("name", ""))
        game_hour_utc = int(template.get("game_hour_utc", 0) or 0)
        game_minute_utc = int(template.get("game_minute_utc", 0) or 0)
        dt_utc = datetime(2000, 1, 1, game_hour_utc, game_minute_utc, tzinfo=utc_tz)
        dt_moscow = dt_utc.astimezone(moscow_tz)
        return f"{name} — {dt_moscow.strftime('%H:%M')} МСК"

    def build_regular_poll_spec(
        self, poll_template_id: int
    ) -> PollCreationSpec | None:
        """Собирает spec обычного опроса из актуальных данных БД."""
        poll_templates = get_poll_templates()
        template = next(
            (
                p
                for p in poll_templates
                if int(p.get("id", 0) or 0) == poll_template_id
            ),
            None,
        )
        if template is None:
            logging.warning(
                "⚠️ Шаблон опроса с ID %s не найден, открытие пропущено",
                poll_template_id,
            )
            return None
        if int(template.get("enabled", 1) or 0) != 1:
            logging.info(
                "⏸️ Шаблон опроса %s выключен, плановое открытие пропущено",
                poll_template_id,
            )
            return None

        return PollCreationSpec(
            kind="regular",
            poll_name=str(template.get("name") or ""),
            question=str(template.get("message") or ""),
            options=tuple(POLL_OPTIONS),
            allows_multiple_answers=False,
            subs=tuple(int(user_id) for user_id in template.get("subs", []) or []),
            option_poll_names=(),
            poll_template_id=poll_template_id,
            place_snapshot=str(template.get("place") or ""),
            cost_snapshot=int(template.get("cost", 0) or 0),
            cost_per_game_snapshot=int(template.get("cost_per_game", 0) or 0),
            target_month_snapshot=None,
        )

    def build_monthly_subscription_poll_spec(self) -> PollCreationSpec | None:
        """Собирает spec месячного опроса из актуальных данных БД."""
        poll_templates = get_poll_templates()
        paid_polls = [
            p
            for p in poll_templates
            if int(p.get("cost", 0) or 0) > 0 and int(p.get("enabled", 1) or 0) == 1
        ]
        if not paid_polls:
            return None

        utc_tz = ZoneInfo("UTC")
        moscow_tz = ZoneInfo("Europe/Moscow")
        options = tuple(
            self._format_monthly_option(poll, utc_tz=utc_tz, moscow_tz=moscow_tz)
            for poll in paid_polls
        ) + ("Смотреть результат",)
        option_poll_names = tuple(str(poll.get("name") or "") for poll in paid_polls) + (
            None,
        )
        target_month_snapshot = get_next_month_str(datetime.now(timezone.utc))

        return PollCreationSpec(
            kind="monthly_subscription",
            poll_name="monthly_subscription",
            question=(
                "Абонемент на следующий месяц.\n"
                "Выберите игры для подписки. Можно выбрать несколько вариантов."
            ),
            options=options,
            allows_multiple_answers=True,
            subs=(),
            option_poll_names=option_poll_names,
            poll_template_id=None,
            place_snapshot="",
            cost_snapshot=0,
            cost_per_game_snapshot=0,
            target_month_snapshot=target_month_snapshot,
        )

    async def open_regular_poll(
        self,
        bot: Bot,
        chat_id: int,
        poll_template_id: int,
        bot_enabled: bool,
    ) -> int:
        """Открывает обычный опрос по шаблону из БД."""
        if get_open_game_by_template_id(poll_template_id) is not None:
            logging.info(
                "ℹ️ Опрос для шаблона %s уже открыт, повторное открытие пропущено",
                poll_template_id,
            )
            return chat_id

        spec = self.build_regular_poll_spec(poll_template_id)
        if spec is None:
            return chat_id
        return await self.send_poll_spec(bot, chat_id, spec, bot_enabled)

    async def open_monthly_subscription_poll(
        self,
        bot: Bot,
        chat_id: int,
        bot_enabled: bool,
    ) -> int:
        """Открывает месячный опрос по актуальным данным БД."""
        if get_open_monthly_game() is not None:
            logging.info("ℹ️ Месячный опрос уже открыт, повторное открытие пропущено")
            return chat_id

        spec = self.build_monthly_subscription_poll_spec()
        if spec is None:
            logging.info("ℹ️ Платные опросы не найдены, открытие месячного опроса пропущено")
            return chat_id
        return await self.send_poll_spec(bot, chat_id, spec, bot_enabled)

    def persist_state(self) -> None:
        """Сохранить состояние опросов в базу данных."""
        serializable: dict[str, dict] = {}
        for poll_id, data in self._poll_data.items():
            serializable[poll_id] = data.model_dump(mode="json")
        save_state(POLL_STATE_KEY, serializable)

    def load_persisted_state(self) -> None:
        """Восстановить состояние опросов из базы данных."""
        logging.debug("Попытка восстановления сохраненного состояния опросов...")
        stored = load_state(POLL_STATE_KEY, default={})
        if not isinstance(stored, dict):
            logging.warning(
                "⚠️ Сохранённое состояние опросов повреждено (не словарь), fallback будет пропущен"
            )
            stored = {}

        self._poll_data.clear()
        self._update_tasks.clear()

        successful = 0
        failed = 0
        stored_games = {row["poll_id"]: row for row in get_open_games()}
        templates_by_id = {
            int(template["id"]): template
            for template in get_poll_templates()
            if "id" in template
        }
        if stored_games:
            for poll_id, row in stored_games.items():
                try:
                    fallback = stored.get(poll_id, {})
                    options = json.loads(row.get("options_json") or "[]")
                    option_poll_names = json.loads(
                        row.get("option_poll_names_json") or "[]"
                    )
                    template_subs = []
                    template_id = row.get("poll_template_id")
                    if template_id is not None and int(template_id) in templates_by_id:
                        template_subs = list(
                            templates_by_id[int(template_id)].get("subs", [])
                        )
                    poll_kind = str(row.get("kind") or "regular")
                    restored_subs = list(fallback.get("subs", template_subs))
                    if poll_kind == "regular":
                        # TEMP: после рестарта regular poll должен доверять БД, а не stale
                        # списку подписчиков из poll_state. Это точечная синхронизация, пока
                        # не придумаем более чистую стратегию хранения/восстановления subs.
                        restored_subs = template_subs
                    restored_opened_at = str(row.get("opened_at") or "")
                    restored = PollData(
                        kind=poll_kind,
                        status=str(row.get("status") or "open"),
                        poll_template_id=row.get("poll_template_id"),
                        poll_name_snapshot=str(row.get("poll_name_snapshot") or ""),
                        question_snapshot=str(row.get("question_snapshot") or ""),
                        opened_at=restored_opened_at,
                        chat_id=int(row["chat_id"]),
                        poll_msg_id=int(row["poll_message_id"]),
                        info_msg_id=row.get("info_message_id"),
                        final_message_id=row.get("final_message_id"),
                        yes_voters=[
                            VoterInfo(
                                **{
                                    **item,
                                    "voted_at": item.get("voted_at")
                                    or restored_opened_at,
                                }
                            )
                            for item in fallback.get("yes_voters", [])
                            if isinstance(item, dict)
                        ],
                        last_message_text=str(
                            fallback.get(
                                "last_message_text",
                                row.get("last_info_text") or "⏳ Идёт сбор голосов...",
                            )
                        ),
                        subs=restored_subs,
                        options=[str(value) for value in options],
                        option_poll_names=[
                            str(value) if value is not None else None
                            for value in option_poll_names
                        ],
                        monthly_votes=load_monthly_votes(poll_id)
                        or fallback.get("monthly_votes", {}),
                        target_month=(
                            str(row.get("target_month_snapshot"))
                            if row.get("target_month_snapshot")
                            else fallback.get("target_month")
                        ),
                    )
                    if poll_kind == "regular":
                        restored.yes_voters = self._normalize_voter_timestamps(
                            restored.yes_voters, restored.opened_at
                        )
                    self._poll_data[poll_id] = restored
                    self._update_tasks[poll_id] = None
                    successful += 1
                    logging.debug(f"  Восстановлен опрос из games {poll_id}")
                except (TypeError, KeyError, ValueError):
                    failed += 1
                    logging.exception(
                        f"❌ Не удалось восстановить состояние игры {poll_id} из таблицы games"
                    )
            if successful > 0:
                logging.info(f"✅ Восстановлено опросов из games: {successful}")
            if failed > 0:
                logging.warning(f"⚠️ Не удалось восстановить опросов из games: {failed}")
            return

        for poll_id, data in stored.items():
            try:
                restored = PollData(**data)
                if restored.kind == "regular":
                    restored.yes_voters = self._normalize_voter_timestamps(
                        restored.yes_voters, restored.opened_at
                    )
                self._poll_data[poll_id] = restored
                self._update_tasks[poll_id] = None
                successful += 1
                logging.debug(f"  Восстановлен опрос {poll_id}")
            except (TypeError, KeyError, ValueError):
                failed += 1
                logging.exception(
                    f"❌ Не удалось восстановить состояние опроса {poll_id}. "
                    f"Проверьте структуру данных в БД."
                )

        if successful > 0:
            logging.info(f"✅ Восстановлено опросов: {successful}")
        if failed > 0:
            logging.warning(f"⚠️ Не удалось восстановить опросов: {failed}")

    def refresh_restored_regular_polls(self, bot: Bot) -> None:
        """Планирует обновление UI для восстановленных regular poll."""
        for poll_id, data in self._poll_data.items():
            if data.kind != "regular" or data.info_msg_id is None:
                continue
            self.cancel_update_task(poll_id)
            self.create_update_task(poll_id, bot)

    def clear_all_polls(self) -> None:
        """Очистить все опросы."""
        self._poll_data.clear()
        self._update_tasks.clear()

    def delete_poll(self, poll_id: str) -> None:
        """Удалить опрос по ID."""
        if poll_id in self._poll_data:
            del self._poll_data[poll_id]
        if poll_id in self._update_tasks:
            del self._update_tasks[poll_id]

    def cancel_update_task(self, poll_id: str) -> None:
        """Отменить задачу обновления для опроса."""
        task = self._update_tasks.get(poll_id)
        if task is not None:
            task.cancel()
            logging.debug("Предыдущая задача обновления отменена")

    def create_update_task(self, poll_id: str, bot: Bot) -> None:
        """Создать задачу обновления списка игроков для опроса."""
        self._update_tasks[poll_id] = asyncio.create_task(
            self._update_players_list(bot, poll_id)
        )
        logging.debug(
            "Создана новая задача отложенного обновления "
            f"({PLAYERS_LIST_UPDATE_DELAY_SECONDS} сек)"
        )

    @staticmethod
    def _normalize_voter_timestamps(
        voters: list[VoterInfo], opened_at: str
    ) -> list[VoterInfo]:
        """Заполняет voted_at для legacy-голосов, если поле ещё пустое."""
        normalized: list[VoterInfo] = []
        for voter in voters:
            if voter.voted_at:
                normalized.append(voter)
                continue
            normalized.append(voter.model_copy(update={"voted_at": opened_at}))
        return normalized

    def _build_regular_roster(self, data: PollData) -> PollRoster:
        """Возвращает единый состав regular-опроса и нормализует legacy-состояние."""
        normalized_voters = self._normalize_voter_timestamps(
            data.yes_voters, data.opened_at
        )
        if normalized_voters != data.yes_voters:
            data.yes_voters = normalized_voters
        return build_regular_poll_roster(data)

    @staticmethod
    def _format_roster_lines(entries: list[Any]) -> str:
        return "\n".join(
            f"{index}) {escape_html(entry.rendered_name)}"
            for index, entry in enumerate(entries, start=1)
        )

    def _build_live_roster_text(self, roster: PollRoster) -> str:
        """Строит промежуточный текст списка игроков из готового состава."""
        if roster.total == 0:
            return "⏳ Идёт сбор голосов...\n\n⭐️ — абонемент\n🏐 — донат на мяч"

        if roster.total < MIN_PLAYERS:
            text = (
                f"⏳ <b>Идёт сбор голосов:</b> {roster.total}/{MIN_PLAYERS}\n\n"
                "<b>Проголосовали:</b>\n"
                f"{self._format_roster_lines(roster.entries)}"
            )
        elif not roster.reserve_entries and not roster.booked_entries:
            text = "✅ <b>Список игроков:</b>\n"
            text += self._format_roster_lines(roster.main_entries)
        elif not roster.booked_entries:
            text = "✅ <b>Список игроков:</b>\n"
            text += self._format_roster_lines(roster.main_entries)
            text += "\n\n🕗 <b>Запасные игроки:</b>\n"
            text += self._format_roster_lines(roster.reserve_entries)
        else:
            text = "✅ <b>Список игроков:</b>\n"
            text += self._format_roster_lines(roster.main_entries)
            text += "\n\n🕗 <b>Запасные игроки:</b>\n"
            text += self._format_roster_lines(roster.reserve_entries)
            text += "\n\n🎫 <b>Лист ожидания:</b>\n"
            text += self._format_roster_lines(roster.booked_entries)

        return text + "\n\n⭐️ — абонемент\n🏐 — донат на мяч"

    def _build_final_roster_text(self, roster: PollRoster) -> str:
        """Строит финальный текст regular-опроса из готового состава."""
        if roster.total == 0:
            return "📊 <b>Голосование завершено</b>\n\nНикто не записался."

        if roster.total < MIN_PLAYERS:
            text = (
                f"📊 <b>Голосование завершено:</b> {roster.total}/{MIN_PLAYERS}\n\n"
                "<b>Записались:</b>\n"
                f"{self._format_roster_lines(roster.entries)}"
                "\n\n⚠️ <b>Не хватает игроков!</b>"
            )
        elif not roster.reserve_entries and not roster.booked_entries:
            text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(roster.main_entries)}):</b>\n"
                f"{self._format_roster_lines(roster.main_entries)}"
            )
        elif not roster.booked_entries:
            text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(roster.main_entries)}):</b>\n"
                f"{self._format_roster_lines(roster.main_entries)}"
                f"\n\n🕗 <b>Запасные ({len(roster.reserve_entries)}):</b>\n"
                f"{self._format_roster_lines(roster.reserve_entries)}"
            )
        else:
            text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(roster.main_entries)}):</b>\n"
                f"{self._format_roster_lines(roster.main_entries)}"
                f"\n\n🕗 <b>Запасные ({len(roster.reserve_entries)}):</b>\n"
                f"{self._format_roster_lines(roster.reserve_entries)}"
                f"\n\n🎫 <b>Лист ожидания ({len(roster.booked_entries)}):</b>\n"
                f"{self._format_roster_lines(roster.booked_entries)}"
                "\n\n⚠️ <b>Превышен лимит игроков!</b>\n"
                "Игроков в листе ожидания просим остаться дома и не нарушать правила."
            )

        return text + "\n\n⭐️ — абонемент\n🏐 — донат на мяч"

    def update_voters(
        self,
        poll_id: str,
        user_id: int,
        user_name: str,
        update_id: int,
        voted_at: str,
        voted_yes: bool,
    ) -> list[VoterInfo]:
        """
        Обновить raw-список голосующих.

        Args:
            poll_id: ID опроса
            user_id: ID пользователя
            user_name: Имя пользователя
            update_id: ID обновления
            voted_at: Время голоса в UTC ISO-формате
            voted_yes: Проголосовал ли "Да"

        Returns:
            Обновлённый raw-список голосующих
        """
        if poll_id not in self._poll_data:
            return []

        data = self._poll_data[poll_id]

        # Удаляем пользователя, если был
        data.yes_voters = [v for v in data.yes_voters if v.id != user_id]
        if voted_yes:
            data.yes_voters.append(
                VoterInfo(
                    id=user_id,
                    name=user_name,
                    update_id=update_id,
                    voted_at=voted_at,
                )
            )
        return data.yes_voters

    async def send_poll_spec(
        self,
        bot: Bot,
        chat_id: int,
        spec: PollCreationSpec,
        bot_enabled: bool,
    ) -> int:
        """
        Отправка готового spec опроса в чат.

        Args:
            bot: Экземпляр бота
            chat_id: ID чата для отправки
            spec: Готовый payload и снапшоты опроса
            bot_enabled: Флаг включения бота

        Returns:
            Новый chat_id (может измениться при миграции группы)
        """
        poll_name = spec.poll_name
        question = spec.question

        if not bot_enabled:
            logging.info(f"⏸️ Бот выключен, опрос '{poll_name}' не создан")
            return chat_id

        logging.info(f"📋 Создание опроса '{poll_name}' в чате {chat_id}")
        logging.debug(f"  Вопрос: {question}")
        import typing

        from aiogram.types import InputPollOption

        poll_options = list(spec.options)
        logging.debug(f"  Опции: {poll_options}")
        logging.debug(f"  Подписчиков: {len(spec.subs)}")

        try:

            @retry_async((TelegramNetworkError, asyncio.TimeoutError, OSError))
            async def send_poll_with_retry():
                return await bot.send_poll(
                    chat_id=chat_id,
                    question=question,
                    options=typing.cast(list[InputPollOption | str], poll_options),
                    is_anonymous=False,
                    allows_multiple_answers=spec.allows_multiple_answers,
                )

            poll_message = await send_poll_with_retry()
            logging.debug(
                f"✅ Опрос успешно отправлен, message_id={poll_message.message_id}"
            )
        except TelegramMigrateToChat as e:
            new_chat_id: int = e.migrate_to_chat_id
            logging.warning(
                f"🔄 Группа мигрирована в супергруппу при создании опроса '{poll_name}'. "
                f"Старый ID: {chat_id}, Новый ID: {new_chat_id}"
            )
            save_error_dump(e, poll_name, question, chat_id)

            try:
                error_msg: str = (
                    f'❌ *Ошибка при создании опроса "{poll_name}"*\n\n'
                    f"Группа была мигрирована в супергруппу.\n"
                    f"Новый ID чата: `{new_chat_id}`"
                )
                await self._safe_send_message(
                    bot,
                    chat_id=new_chat_id,
                    text=error_msg,
                    parse_mode="Markdown",
                    action_name="notify migration",
                )
                logging.debug(
                    f"✅ Уведомление о миграции отправлено в новый чат {new_chat_id}"
                )
            except (
                TelegramAPIError,
                TelegramNetworkError,
                asyncio.TimeoutError,
                OSError,
            ):
                logging.exception(
                    f"❌ Не удалось отправить уведомление о миграции в чат {new_chat_id}"
                )

            return new_chat_id

        except (
            TelegramAPIError,
            TelegramNetworkError,
            asyncio.TimeoutError,
            OSError,
            ValueError,
        ) as e:
            logging.exception(
                f"❌ Критическая ошибка при создании опроса '{poll_name}' в чате {chat_id}. "
                f"Проверьте права бота и корректность chat_id."
            )
            save_error_dump(e, poll_name, question, chat_id)

            try:
                error_msg = (
                    f'❌ *Ошибка при создании опроса "{poll_name}"*\n\n'
                    f"Не удалось создать опрос. Пожалуйста, проверьте логи и файл дампа для подробностей."
                )
                await self._safe_send_message(
                    bot,
                    chat_id=chat_id,
                    text=error_msg,
                    parse_mode="Markdown",
                    action_name="notify poll creation error",
                )
                logging.debug("✅ Уведомление об ошибке отправлено в чат")
            except (
                TelegramAPIError,
                TelegramNetworkError,
                asyncio.TimeoutError,
                OSError,
            ):
                logging.exception(
                    f"❌ Не удалось отправить уведомление об ошибке в чат {chat_id}"
                )

            return chat_id

        # Отправляем информационное сообщение
        info_message = None
        try:
            logging.debug("Отправка информационного сообщения...")

            info_message = await self._safe_send_message(
                bot,
                chat_id=chat_id,
                text="⏳ Идёт сбор голосов...",
                action_name="send poll info message",
            )
            logging.debug(
                f"✅ Информационное сообщение отправлено, message_id={info_message.message_id if info_message else 'unknown'}"
            )
        except (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError):
            logging.exception(
                f"❌ Не удалось отправить информационное сообщение для опроса '{poll_name}'"
            )

        # Закрепляем опрос
        try:
            logging.debug(
                f"Закрепление опроса (message_id={poll_message.message_id})..."
            )

            @retry_async(
                (TelegramNetworkError, asyncio.TimeoutError, OSError), tries=3, delay=2
            )
            async def pin_with_retry():
                await bot.pin_chat_message(
                    chat_id=chat_id, message_id=poll_message.message_id
                )

            await pin_with_retry()
            logging.debug("✅ Опрос успешно закреплен")
        except (
            TelegramAPIError,
            TelegramNetworkError,
            asyncio.TimeoutError,
            OSError,
        ) as e:
            logging.exception(
                f"⚠️ Не удалось закрепить опрос '{poll_name}' (message_id={poll_message.message_id}): {e}. "
                f"Возможно, у бота нет прав на закрепление сообщений."
            )

        # Сохраняем данные опроса
        if poll_message.poll is None:
            logging.error(
                f"❌ Опрос '{poll_name}' создан, но poll объект отсутствует в ответе Telegram API. "
                f"Это неожиданная ошибка API."
            )
            return chat_id

        opened_dt = datetime.now(timezone.utc)
        opened_at = opened_dt.isoformat()
        
        if not create_game(
            poll_id=poll_message.poll.id,
            kind=spec.kind,
            status="open",
            poll_template_id=spec.poll_template_id,
            poll_name_snapshot=poll_name,
            question_snapshot=question,
            chat_id=chat_id,
            poll_message_id=poll_message.message_id,
            info_message_id=info_message.message_id if info_message else None,
            opened_at=opened_at,
            place_snapshot=spec.place_snapshot,
            cost_snapshot=spec.cost_snapshot,
            cost_per_game_snapshot=spec.cost_per_game_snapshot,
            target_month_snapshot=spec.target_month_snapshot,
            options=poll_options,
            option_poll_names=list(spec.option_poll_names),
        ):
            # Критическая ошибка: опрос в Telegram создан, но запись в БД не удалась
            logging.error(
                f"❌ КРИТИЧЕСКАЯ ОШИБКА: Опрос '{poll_name}' создан в Telegram, "
                f"но запись в БД не удалась! poll_id={poll_message.poll.id}. "
                f"Опрос будет потерян при перезапуске бота."
            )
            await self._notify_admin_failed_poll_persistence(
                bot,
                chat_id=chat_id,
                poll_name=poll_name,
                poll_id=poll_message.poll.id,
                poll_message_id=poll_message.message_id,
                info_message_id=info_message.message_id if info_message else None,
            )
            # Не создаём PollData, чтобы бот не управлял несохранённым опросом
            return chat_id
        
        update_game_info_message(
            poll_message.poll.id,
            info_message_id=info_message.message_id if info_message else None,
            last_info_text="⏳ Идёт сбор голосов...",
        )

        self._poll_data[poll_message.poll.id] = PollData(
            kind=spec.kind,
            status="open",
            poll_template_id=spec.poll_template_id,
            poll_name_snapshot=poll_name,
            question_snapshot=question,
            opened_at=opened_at,
            chat_id=chat_id,
            poll_msg_id=poll_message.message_id,
            info_msg_id=info_message.message_id if info_message else None,
            final_message_id=None,
            yes_voters=[],
            last_message_text="⏳ Идёт сбор голосов...",
            subs=list(spec.subs),
            options=poll_options,
            option_poll_names=list(spec.option_poll_names),
            target_month=spec.target_month_snapshot,
        )
        self._update_tasks[poll_message.poll.id] = None
        self.persist_state()

        logging.info(
            f"✅ Опрос '{poll_name}' успешно создан! "
            f"poll_id={poll_message.poll.id}, chat_id={chat_id}, "
            f"poll_msg_id={poll_message.message_id}, "
            f"info_msg_id={info_message.message_id if info_message else 'None'}"
        )
        return chat_id

    async def _update_players_list(self, bot: Bot, poll_id: str) -> None:
        """Обновить список игроков с настраиваемой задержкой."""
        logging.debug(
            "Задержка "
            f"{PLAYERS_LIST_UPDATE_DELAY_SECONDS} секунд перед обновлением "
            f"списка игроков для опроса {poll_id}..."
        )
        await asyncio.sleep(PLAYERS_LIST_UPDATE_DELAY_SECONDS)

        if poll_id not in self._poll_data:
            logging.debug(f"Опрос {poll_id} больше не существует, отмена обновления")
            return

        data = self._poll_data[poll_id]
        roster = self._build_regular_roster(data)
        text = self._build_live_roster_text(roster)

        info_msg_id = data.info_msg_id
        if info_msg_id is None:
            logging.warning(
                f"⚠️ info_msg_id отсутствует для опроса {poll_id}, невозможно обновить список игроков. "
                f"Возможно, информационное сообщение не было отправлено."
            )
            self._update_tasks[poll_id] = None
            self.persist_state()
            return

        if text == data.last_message_text:
            logging.debug(
                f"Текст сообщения не изменился для опроса {poll_id}, пропускаем обновление"
            )
        else:
            try:
                logging.debug(
                    f"Обновление информационного сообщения для опроса {poll_id}..."
                )

                @retry_async(
                    (TelegramNetworkError, asyncio.TimeoutError, OSError),
                    tries=3,
                    delay=2,
                )
                async def edit_with_retry():
                    await bot.edit_message_text(
                        chat_id=data.chat_id,
                        message_id=info_msg_id,
                        text=text,
                        parse_mode="HTML",
                    )

                await edit_with_retry()
                data.last_message_text = text
                update_game_last_info_text(poll_id, text)
                main_count = len(roster.main_entries)
                reserve_count = len(roster.reserve_entries)
                booked_count = len(roster.booked_entries)
                logging.info(
                    f"✅ Список игроков обновлен для опроса {poll_id}: {roster.total} человек "
                    f"(основных: {main_count}, запасных: {reserve_count}, в листе ожидания: {booked_count})"
                )
            except (
                TelegramAPIError,
                TelegramNetworkError,
                asyncio.TimeoutError,
                OSError,
            ):
                logging.exception(
                    f"❌ Не удалось отредактировать информационное сообщение для опроса {poll_id} "
                    f"(chat_id={data.chat_id}, message_id={info_msg_id}). "
                    f"Проверьте права бота и существование сообщения."
                )

        self._update_tasks[poll_id] = None
        self.persist_state()

    async def close_poll(self, bot: Bot, poll_id: str) -> None:
        """
        Закрыть активный опрос и опубликовать финальный список.

        Args:
            bot: Экземпляр бота
            poll_id: ID опроса Telegram
        """
        logging.info(f"🔒 Начало процедуры закрытия опроса poll_id='{poll_id}'...")

        data = self.get_poll_data(poll_id)
        if data is None:
            game = get_game(poll_id)
            if game is None or str(game.get("status")) != "open":
                logging.info(f"⚠️ Нет активного опроса для закрытия poll_id={poll_id}")
                return
            logging.warning(
                f"⚠️ Игра poll_id={poll_id} есть в БД, но отсутствует в памяти. Закрытие пропущено."
            )
            return

        poll_name = data.poll_name_snapshot or poll_id
        logging.debug(f"Закрываем опрос: poll_id={poll_id}, chat_id={data.chat_id}")
        backup_reason = (
            "monthly_poll_finalize"
            if data.kind == "monthly_subscription"
            else "daily_poll_finalize"
        )
        create_backup(backup_reason)

        # Останавливаем опрос
        try:
            logging.debug(f"Остановка опроса (message_id={data.poll_msg_id})...")

            @retry_async(
                (TelegramNetworkError, asyncio.TimeoutError, OSError), tries=3, delay=2
            )
            async def stop_poll_with_retry():
                try:
                    await bot.stop_poll(
                        chat_id=data.chat_id, message_id=data.poll_msg_id
                    )
                except TelegramBadRequest as e:
                    if "message with poll to stop not found" in e.message:
                        logging.warning(
                            f"⚠️ Опрос '{poll_name}' уже удален из Telegram."
                        )
                        return
                    raise

            await stop_poll_with_retry()
            logging.info(f"✅ Опрос '{poll_name}' (poll_id={poll_id}) остановлен")
        except (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError):
            logging.exception(
                f"❌ Не удалось остановить опрос '{poll_name}' "
                f"(chat_id={data.chat_id}, poll_msg_id={data.poll_msg_id}). "
                f"Продолжаем обработку финального списка..."
            )

        if data.kind == "monthly_subscription":
            await self._close_monthly_subscription_poll(bot, poll_id, poll_name, data)
            logging.debug(f"Очистка данных опроса {poll_id}...")
            self.delete_poll(poll_id)
            self.persist_state()
            logging.info(
                f"✅ Опрос '{poll_name}' (poll_id={poll_id}) успешно закрыт, данные очищены"
            )
            return

        roster = self._build_regular_roster(data)
        final_text = self._build_final_roster_text(roster)

        # Добавляем реквизиты для перевода
        payment_lines = [
            line
            for line in (
                escape_html(PAYMENT_NAME),
                escape_html(PAYMENT_BANK),
                escape_html(PAYMENT_PHONE),
            )
            if line
        ]
        if payment_lines:
            final_text += "\n\n<b>Реквизиты для перевода:</b>\n"
            final_text += "\n".join(payment_lines)

        # Обработка списания средств для платных залов
        charge_rows = await self._process_payment_deduction(bot, poll_name, roster)

        # Отправляем финальный список новым сообщением с ответом на голосовалку
        info_msg_id = data.info_msg_id
        final_message_id: int | None = None
        try:
            logging.debug(
                f"Отправка финального списка новым сообщением для опроса '{poll_name}'..."
            )

            final_message = await self._safe_send_message(
                bot,
                chat_id=data.chat_id,
                reply_to_message_id=data.poll_msg_id,
                text=final_text,
                parse_mode="HTML",
                action_name="send final poll roster",
            )
            if final_message is None:
                from aiogram.methods import SendMessage

                raise TelegramNetworkError(
                    method=SendMessage(chat_id=data.chat_id, text=final_text),
                    message="final poll roster send failed",
                )
            final_message_id = final_message.message_id
            data.final_message_id = final_message_id
            main_count = len(roster.main_entries)
            reserve_count = len(roster.reserve_entries)
            booked_count = len(roster.booked_entries)
            logging.info(
                f"✅ Финальный список отправлен новым сообщением для '{poll_name}': "
                f"{roster.total} участников (основных: {main_count}, "
                f"запасных: {reserve_count}, в листе ожидания: {booked_count})"
            )

            # Удаляем старое информационное сообщение
            if info_msg_id:
                try:
                    logging.debug("Удаление старого информационного сообщения...")

                    @retry_async(
                        (TelegramNetworkError, asyncio.TimeoutError, OSError),
                        tries=3,
                        delay=2,
                    )
                    async def delete_old_with_retry():
                        await bot.delete_message(
                            chat_id=data.chat_id, message_id=info_msg_id
                        )

                    await delete_old_with_retry()
                    logging.info("✅ Старое сообщение удалено")
                except (
                    TelegramAPIError,
                    TelegramNetworkError,
                    asyncio.TimeoutError,
                    OSError,
                ):
                    logging.warning(
                        f"⚠️ Не удалось удалить старое сообщение (message_id={info_msg_id}). "
                        f"Возможно, оно уже удалено вручную."
                    )
        except (
            TelegramAPIError,
            TelegramNetworkError,
            asyncio.TimeoutError,
            OSError,
        ):
            logging.exception(
                f"❌ Не удалось отправить финальный список для '{poll_name}' "
                f"(chat_id={data.chat_id}, reply_to={data.poll_msg_id})"
            )

        participant_rows: list[dict[str, Any]] = []
        charge_by_player = {int(row["player_id"]): row for row in charge_rows}
        for entry in roster.entries:
            charge = charge_by_player.get(entry.player_id, {})
            participant_rows.append(
                {
                    "player_id": entry.player_id,
                    "roster_bucket": entry.roster_bucket,
                    "sort_order": entry.sort_order,
                    "is_subscriber": bool(
                        charge.get("is_subscriber", entry.is_subscriber)
                    ),
                    "charged_amount": int(charge.get("charged_amount", 0) or 0),
                    "charge_source": str(charge.get("charge_source", "none")),
                    "balance_before": charge.get("balance_before"),
                    "balance_after": charge.get("balance_after"),
                }
            )
        if participant_rows:
            save_game_participants(poll_id, participant_rows)

        close_game(
            poll_id,
            status="closed",
            closed_at=datetime.now(timezone.utc).isoformat(),
            final_message_id=final_message_id,
        )

        # Очищаем данные опроса
        logging.debug(f"Очистка данных опроса {poll_id}...")
        self.delete_poll(poll_id)
        self.persist_state()
        logging.info(
            f"✅ Опрос '{poll_name}' (poll_id={poll_id}) успешно закрыт, данные очищены"
        )

    async def _close_monthly_subscription_poll(
        self, bot: Bot, poll_id: str, poll_name: str, data: PollData
    ) -> None:
        """Закрыть месячный опрос, записать подписчиков и рассчитать стоимость абонемента."""
        persisted_votes = load_monthly_votes(poll_id)
        if persisted_votes:
            data.monthly_votes = persisted_votes
        option_poll_names = data.option_poll_names
        votes_by_poll: dict[str, set[int]] = {}
        for user_id, option_ids in data.monthly_votes.items():
            for option_id in option_ids:
                if option_id < 0 or option_id >= len(option_poll_names):
                    continue
                poll_target = option_poll_names[option_id]
                if poll_target is None:
                    continue
                if poll_target not in votes_by_poll:
                    votes_by_poll[poll_target] = set()
                votes_by_poll[poll_target].add(user_id)

        poll_templates = get_poll_templates()
        paid_polls = [p for p in poll_templates if int(p.get("cost", 0) or 0) > 0]
        for template in paid_polls:
            name = str(template.get("name", ""))
            subs = sorted(votes_by_poll.get(name, set()))
            template["subs"] = subs
            save_poll_template(template)

        # --- Расчёт стоимости абонемента ---
        total_voters = len(data.monthly_votes)
        target_month = self._resolve_target_month(data)
        fund_balance = get_fund_balance()
        single_game_income_stats = get_single_game_income_stats(
            months_back=SINGLE_GAME_STATS_MONTHS_BACK,
            before_month=target_month,
        )

        result = calculate_subscription(
            paid_polls,
            votes_by_poll,
            target_month,
            fund_balance,
            single_game_income_stats,
        )
        # Касса не меняется при закрытии опроса — уменьшается только при оплате залов

        # Применяем списания к БД
        charged_subscribers = self._apply_subscription_charges(result, target_month)

        # --- Формируем и отправляем итоговое сообщение ---
        summary_text = self._format_hall_summary(result)
        final_text = self._format_subscription_report(
            total_voters,
            summary_text,
            charged_subscribers,
            fund_balance,
            result,
        )

        final_message_id: int | None = None
        try:
            try:
                final_message = await self._safe_send_message(
                    bot,
                    chat_id=data.chat_id,
                    reply_to_message_id=data.poll_msg_id,
                    text=final_text,
                    parse_mode="HTML",
                    action_name="send monthly final report with reply",
                )
            except TelegramBadRequest as e:
                if "message to be replied not found" not in e.message:
                    raise

                logging.warning(
                    "⚠️ Сообщение для ответа не найдено, отправляем новым сообщением"
                )
                final_message = await self._safe_send_message(
                    bot,
                    chat_id=data.chat_id,
                    text=final_text,
                    parse_mode="HTML",
                    action_name="send monthly final report without reply",
                )

            if final_message is None:
                from aiogram.methods import SendMessage

                raise TelegramNetworkError(
                    method=SendMessage(chat_id=data.chat_id, text=final_text),
                    message="monthly final report send failed",
                )
            final_message_id = final_message.message_id
            data.final_message_id = final_message_id
            logging.info(
                f"✅ Итоги голосования за абонемент отправлены для '{poll_name}'"
            )
        except (
            TelegramAPIError,
            TelegramNetworkError,
            asyncio.TimeoutError,
            OSError,
        ):
            logging.exception(
                f"❌ Не удалось отправить итоги голосования для '{poll_name}'"
            )

        # Отправляем подробный отчёт админу
        if ADMIN_USER_ID and charged_subscribers:
            admin_report = self._format_admin_subscription_report(
                target_month,
                summary_text,
                charged_subscribers,
                fund_balance,
                result,
            )

            try:
                if ADMIN_USER_ID is not None:
                    await self._safe_send_message(
                        bot,
                        chat_id=ADMIN_USER_ID,
                        text=admin_report,
                        parse_mode="HTML",
                        action_name="send monthly admin report",
                    )
                logging.info("✅ Отчёт по абонементам отправлен админу")
            except (
                TelegramAPIError,
                TelegramNetworkError,
                asyncio.TimeoutError,
                OSError,
            ):
                logging.exception("❌ Не удалось отправить отчёт по абонементам админу")

        close_game(
            poll_id,
            status="closed",
            closed_at=datetime.now(timezone.utc).isoformat(),
            final_message_id=final_message_id,
        )

    @staticmethod
    def _resolve_target_month(data: PollData) -> str:
        """Определяет месяц абонемента, зафиксированный при открытии опроса."""
        if data.target_month:
            return data.target_month
        if data.opened_at:
            try:
                opened_dt = datetime.fromisoformat(str(data.opened_at))
                return get_next_month_str(opened_dt)
            except ValueError:
                pass
        return get_next_month_str(datetime.now(timezone.utc))

    # ── Вспомогательные методы для абонемента ────────────────────────────────

    @staticmethod
    def _apply_subscription_charges(
        result: SubscriptionResult, month: str
    ) -> list[dict[str, Any]]:
        """Применяет списания к БД и возвращает список данных для отчёта."""
        charged: list[dict[str, Any]] = []
        for charge in result.subscriber_charges:
            ensure_player(charge.user_id)
            player_data = get_player_balance(charge.user_id)
            old_balance = player_data.get("balance", 0) if player_data else 0

            update_player_balance(charge.user_id, -charge.total)
            new_balance = old_balance - charge.total

            halls_str = ", ".join(charge.halls)
            add_transaction(
                charge.user_id,
                -charge.total,
                f"Абонемент: {halls_str} ({month})",
            )

            player_name = ""
            if player_data:
                player_name = (
                    player_data.get("fullname")
                    or player_data.get("name")
                    or f"ID: {charge.user_id}"
                )
            else:
                player_name = f"ID: {charge.user_id}"

            charged.append(
                {
                    "user_id": charge.user_id,
                    "name": player_name,
                    "username": player_data.get("name") if player_data else None,
                    "fullname": player_data.get("fullname") if player_data else None,
                    "halls": charge.halls,
                    "amount": charge.total,
                    "old_balance": old_balance,
                    "new_balance": new_balance,
                }
            )
        return charged

    @staticmethod
    def _format_hall_summary(result: SubscriptionResult) -> str:
        """Форматирует разбивку по залам в HTML."""
        lines: list[str] = []
        for h in result.paid_polls:
            if h.monthly_rent > 0 and h.num_subs > 0:
                lines.append(
                    f"• {escape_html(h.name)}: {h.cost_per_game} ₽ × {h.games_in_month} = {h.monthly_rent} ₽, "
                    f"подписчиков: {h.num_subs}"
                )
            elif h.monthly_rent > 0:
                lines.append(
                    f"• {escape_html(h.name)}: {h.cost_per_game} ₽ × {h.games_in_month} = {h.monthly_rent} ₽ — "
                    f"<b>нет подписчиков</b>"
                )
        if result.price_per_hall > 0:
            lines.append(f"\n💰 Абонемент на 1 зал: <b>{result.price_per_hall} ₽</b>")
            lines.append(f"💰 Комбо (2 зала): <b>{result.combo_price} ₽</b>")
        if result.expected_singles_income > 0:
            lines.append(
                f"📈 Ожидаемый доход с разовых: {result.expected_singles_income} ₽"
            )
        return "\n".join(lines) if lines else "Платные игры не найдены."

    @staticmethod
    def _format_subscription_report(
        total_voters: int,
        summary_text: str,
        charged_subscribers: list[dict[str, Any]],
        fund_balance: int,
        result: SubscriptionResult | None = None,
    ) -> str:
        """Форматирует итоговое сообщение для группового чата."""
        sorted_subscribers = sorted(
            charged_subscribers,
            key=lambda sub: str(sub.get("name", "")).casefold(),
        )
        total_due = sum(
            max(int(sub["amount"]) - int(sub["old_balance"]), 0)
            for sub in sorted_subscribers
        )
        text = (
            "📊 <b>Голосование за абонемент завершено</b>\n\n"
            f"Проголосовали: {total_voters}\n\n"
            f"<b>Расчёт абонемента:</b>\n{summary_text}\n"
        )
        if sorted_subscribers:
            text += "\n<b>К оплате с учётом текущего баланса:</b>\n"
            for i, sub in enumerate(sorted_subscribers, 1):
                amount_due = max(int(sub["amount"]) - int(sub["old_balance"]), 0)
                player_link = format_player_link(
                    {
                        "id": sub.get("user_id"),
                        "name": sub.get("username"),
                        "fullname": sub.get("fullname") or sub.get("name"),
                    },
                    user_id=sub.get("user_id"),
                )
                text += f"{i}. {player_link} - {amount_due} ₽\n"
        text += f"\n🏦 Касса: <b>{fund_balance} ₽</b>"
        text += f"\n💸 Ожидаемая сумма к оплате: <b>{total_due} ₽</b>"
        payment_lines = [
            line
            for line in (
                escape_html(PAYMENT_NAME),
                escape_html(PAYMENT_BANK),
                escape_html(PAYMENT_PHONE),
            )
            if line
        ]
        if payment_lines:
            text += "\n\n<b>Реквизиты для перевода:</b>\n"
            text += "\n".join(payment_lines)
        return text

    @staticmethod
    def _format_admin_subscription_report(
        month: str,
        summary_text: str,
        charged_subscribers: list[dict[str, Any]],
        fund_balance: int,
        result: SubscriptionResult | None = None,
    ) -> str:
        """Форматирует подробный отчёт для администратора."""
        total_due = sum(
            max(int(sub["amount"]) - int(sub["old_balance"]), 0)
            for sub in charged_subscribers
        )
        text = (
            "📊 <b>Отчёт по абонементам</b>\n\n"
            f"📅 Месяц: {month}\n\n"
            f"<b>Расчёт:</b>\n{summary_text}\n\n"
        )
        text += f"<b>К оплате ({len(charged_subscribers)}):</b>\n"
        for i, sub in enumerate(charged_subscribers, 1):
            halls_str = ", ".join(sub["halls"])
            amount_due = max(int(sub["amount"]) - int(sub["old_balance"]), 0)
            text += (
                f"{i}. {escape_html(sub['name'])} — к оплате {amount_due} ₽ "
                f"(списание: {sub['amount']} ₽, было: {sub['old_balance']} ₽, "
                f"станет: {sub['new_balance']} ₽, {escape_html(halls_str)})\n"
            )
        text += f"\n🏦 Касса: <b>{fund_balance} ₽</b>"
        text += f"\n💸 Ожидаемая сумма к оплате: <b>{total_due} ₽</b>"
        return text

    async def _process_payment_deduction(
        self,
        bot: Bot,
        poll_name: str,
        roster: PollRoster,
    ) -> list[dict[str, Any]]:
        """
        Обработка списания средств с игроков без подписки для платных залов.

        Args:
            bot: Экземпляр бота
            poll_name: Название опроса
            roster: Единый состав игроков regular-опроса
        """
        # Получаем информацию о стоимости опроса из БД
        poll_templates = get_poll_templates()
        poll_config = next((p for p in poll_templates if p["name"] == poll_name), None)

        if not poll_config:
            logging.warning(
                f"⚠️ Конфигурация опроса '{poll_name}' не найдена, списание пропущено"
            )
            return []

        cost = poll_config.get("cost", 0)

        # Если стоимость 0 или не указана, ничего не делаем
        if cost <= 0:
            logging.info(
                f"ℹ️ Опрос '{poll_name}' бесплатный (cost={cost}), списание не требуется"
            )
            return [
                {
                    "player_id": entry.player_id,
                    "name": entry.rendered_name,
                    "is_subscriber": entry.is_subscriber,
                    "charged_amount": 0,
                    "charge_source": "none",
                    "balance_before": None,
                    "balance_after": None,
                }
                for entry in roster.entries
            ]

        logging.info(
            f"💳 Начало списания для опроса '{poll_name}' (стоимость: {cost}₽)"
        )

        # Список для статистики
        charged_players: list[dict[str, Any]] = []
        subscribed_players: list[str] = []
        participant_finance_rows: list[dict[str, Any]] = []
        booked_count = 0

        def append_participant_finance_row(
            entry: Any,
            *,
            is_subscriber: bool,
            charged_amount: int = 0,
            charge_source: str = "none",
            balance_before: int | None = None,
            balance_after: int | None = None,
        ) -> None:
            participant_finance_rows.append(
                {
                    "player_id": entry.player_id,
                    "name": entry.rendered_name,
                    "is_subscriber": is_subscriber,
                    "charged_amount": charged_amount,
                    "charge_source": charge_source,
                    "balance_before": balance_before,
                    "balance_after": balance_after,
                }
            )

        for entry in roster.entries:
            if entry.roster_bucket == "booked":
                booked_count += 1
                append_participant_finance_row(
                    entry, is_subscriber=entry.is_subscriber
                )
                logging.info(
                    "  ⏭️  Игрок %s (ID: %s) в листе ожидания, списание пропущено",
                    entry.rendered_name,
                    entry.player_id,
                )
                continue

            # Проверяем, есть ли у игрока подписка
            if entry.is_subscriber:
                subscribed_players.append(entry.rendered_name)
                append_participant_finance_row(
                    entry,
                    is_subscriber=True,
                    charge_source="subscription",
                )
                logging.debug(
                    f"  ⏭️  Игрок {entry.rendered_name} (ID: {entry.player_id}) с подпиской, списание пропущено"
                )
                continue

            # Убеждаемся, что игрок есть в БД
            ensure_player(entry.player_id, entry.rendered_name)

            # Получаем текущий баланс (get_player_balance возвращает dict с ключом "balance" или None)
            player_data = get_player_balance(entry.player_id)
            old_balance = player_data.get("balance", 0) if player_data else 0

            # Списываем средства
            update_player_balance(entry.player_id, -cost)
            new_balance = old_balance - cost

            # Добавляем транзакцию в историю
            game_date = datetime.now().strftime("%d.%m.%Y")
            description = f"Зал: {poll_name} ({game_date})"
            add_transaction(
                entry.player_id,
                -cost,
                description,
                poll_template_id=int(poll_config["id"]),
                poll_name_snapshot=poll_name,
            )

            charged_players.append(
                {
                    "name": entry.rendered_name,
                    "id": entry.player_id,
                    "old_balance": old_balance,
                    "new_balance": new_balance,
                }
            )
            append_participant_finance_row(
                entry,
                is_subscriber=False,
                charged_amount=cost,
                charge_source="single_game",
                balance_before=old_balance,
                balance_after=new_balance,
            )

            logging.info(
                f"  💳 Списано {cost}₽ с {entry.rendered_name} (ID: {entry.player_id}), "
                f"баланс: {old_balance}₽ → {new_balance}₽"
            )

        # Отправляем сводку админу
        if charged_players or subscribed_players:
            await self._send_admin_report(
                bot, poll_name, cost, charged_players, subscribed_players
            )

        total_charged = len(charged_players) * cost
        logging.info(
            f"✅ Списание завершено: {len(charged_players)} игроков, "
            f"итого {total_charged}₽. С подпиской: {len(subscribed_players)}. "
            f"В листе ожидания (без списания): {booked_count}"
        )
        return participant_finance_rows

    async def _send_admin_report(
        self,
        bot: Bot,
        poll_name: str,
        cost: int,
        charged_players: list[dict[str, Any]],
        subscribed_players: list[str],
    ) -> None:
        """
        Отправить сводку о списании админу.

        Args:
            bot: Экземпляр бота
            poll_name: Название опроса
            cost: Стоимость одной игры
            charged_players: Список игроков, с которых списано
            subscribed_players: Список игроков с подпиской
        """
        game_date = datetime.now().strftime("%d.%m.%Y")
        report = "💳 <b>Списание за игру</b>\n\n"
        report += f"📅 {poll_name} ({game_date})\n"
        report += f"💰 Стоимость: {cost}₽\n\n"

        if charged_players:
            report += f"<b>Списано по {cost}₽ с {len(charged_players)} игроков:</b>\n"
            for i, player in enumerate(charged_players, 1):
                balance_emoji = "🔴" if player["new_balance"] < 0 else "🟢"
                report += (
                    f"{i}. {escape_html(player['name'])} "
                    f"{balance_emoji} (баланс: {player['new_balance']}₽)\n"
                )

            total_charged = len(charged_players) * cost
            report += f"\n<b>Итого списано:</b> {total_charged}₽\n"

        if subscribed_players:
            report += f"\n<b>С подпиской (не списано): {len(subscribed_players)}</b>\n"
            for i, name in enumerate(subscribed_players, 1):
                report += f"{i}. {escape_html(name)}\n"

        # Отправляем сообщение админу
        if not ADMIN_USER_ID:
            logging.warning(
                "⚠️ ADMIN_USER_ID не задан в .env, сводка о списании не отправлена"
            )
            return

        try:
            logging.debug(f"Отправка сводки о списании админу (ID: {ADMIN_USER_ID})...")

            if ADMIN_USER_ID is not None:
                await self._safe_send_message(
                    bot,
                    chat_id=ADMIN_USER_ID,
                    text=report,
                    parse_mode="HTML",
                    action_name="send charge report to admin",
                )
            logging.info("✅ Сводка о списании отправлена админу")
        except (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError):
            logging.exception(
                f"❌ Не удалось отправить сводку о списании админу (ID: {ADMIN_USER_ID})"
            )
