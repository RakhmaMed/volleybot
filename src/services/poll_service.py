"""Сервис для управления состоянием опросов."""

from __future__ import annotations

import asyncio
import logging
from asyncio import Task
from datetime import datetime, timezone
import json
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramMigrateToChat,
    TelegramNetworkError,
)

from ..config import (
    ADMIN_USER_ID,
    MAX_PLAYERS,
    MIN_PLAYERS,
    PAYMENT_BANK,
    PAYMENT_NAME,
    PAYMENT_PHONE,
    POLL_OPTIONS,
    RESERVE_PLAYERS,
)
from ..db import (
    POLL_STATE_KEY,
    add_transaction,
    create_backup,
    close_game,
    create_game,
    ensure_player,
    get_fund_balance,
    get_game,
    get_open_games,
    get_player_balance,
    get_poll_templates,
    load_monthly_votes,
    load_state,
    save_game_participants,
    save_poll_template,
    save_state,
    update_game_info_message,
    update_game_last_info_text,
    update_player_balance,
)
from ..poll import PollData, VoterInfo, sort_voters_by_update_id
from ..types import HallBreakdown, PollTemplate, SubscriberCharge, SubscriptionResult
from ..utils import (
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
GAMES_PER_MONTH = 4  # fallback для '*'/некорректного дня
SAFETY_K = 0.7  # Коэффициент надёжности (риск неявки)
TARGET_GROWTH = 1000  # Желаемый прирост казны в месяц (руб.)
SAVINGS_BUFFER = 6000  # Целевая «подушка» казны (руб.)
COMBO_DISCOUNT_COEFF = 1.7  # Комбо = 1.7× одного зала (скидка ~15%)
MIN_SUB_PRICE = 400  # Минимальная цена абонемента за 1 зал
MAX_SUB_PRICE = 500  # Максимальная цена абонемента за 1 зал
DEFAULT_SUB_PRICE = 450  # Цена по умолчанию, если нет подписчиков


def calculate_subscription(
    paid_polls: list[PollTemplate],
    votes_by_poll: dict[str, set[int]],
    target_month: str | None = None,
    fund_balance: int = 0,
) -> SubscriptionResult:
    """
    Бюджетный расчёт стоимости абонемента без побочных эффектов.

    Держит единую цену абонемента за каждый зал в диапазоне 400-500 руб.
    Недостающая часть аренды покрывается ожидаемым доходом с разовых игроков.
    Подписчики на 2+ зала получают комбо-скидку (~15%).

    Args:
        paid_polls: шаблоны платных опросов (``cost > 0``).
        votes_by_poll: маппинг ``poll_name → {user_id, …}`` из голосования.
        target_month: месяц расчёта в формате ``YYYY-MM``.
        fund_balance: текущий баланс казны (влияет на целевую сумму сбора).

    Returns:
        :class:`SubscriptionResult` с разбивкой по залам и списаниями по
        подписчикам.
    """
    # --- 1. Собираем данные по залам ---
    if not target_month:
        target_month = datetime.now().strftime("%Y-%m")

    hall_breakdown: list[HallBreakdown] = []
    paid_hall_names: list[str] = []

    for template in paid_polls:
        name = str(template.get("name", ""))
        cost_per_game = int(template.get("cost_per_game", 0) or 0)
        game_day = str(template.get("game_day", "*") or "*")
        games_in_month = count_games_in_month(game_day, target_month, GAMES_PER_MONTH)
        monthly_rent = cost_per_game * games_in_month
        subs_set = votes_by_poll.get(name, set())
        num_subs = len(subs_set)

        if monthly_rent > 0:
            paid_hall_names.append(name)

        hall_breakdown.append(
            HallBreakdown(
                name=name,
                cost_per_game=cost_per_game,
                games_in_month=games_in_month,
                monthly_rent=monthly_rent,
                num_subs=num_subs,
                per_person=0,  # заполним ниже
            )
        )

    total_rent = sum(h.monthly_rent for h in hall_breakdown if h.monthly_rent > 0)
    total_games_across_halls = sum(
        h.games_in_month for h in hall_breakdown if h.monthly_rent > 0
    )

    # --- 2. Классифицируем подписчиков: single-hall vs combo ---
    user_halls: dict[int, list[str]] = {}
    for hall_name in paid_hall_names:
        for uid in votes_by_poll.get(hall_name, set()):
            user_halls.setdefault(uid, []).append(hall_name)

    n_combo = sum(1 for halls in user_halls.values() if len(halls) >= 2)
    n_single = sum(1 for halls in user_halls.values() if len(halls) == 1)

    # --- 3. Прогноз дохода с разовых игроков ---
    expected_singles_income = round(
        AVG_SINGLES_PER_GAME
        * SINGLE_GAME_PRICE
        * total_games_across_halls
        * SAFETY_K
    )

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

    # --- 8. Заполняем per_person в hall_breakdown ---
    for h in hall_breakdown:
        if h.monthly_rent > 0 and h.num_subs > 0:
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
        hall_breakdown=hall_breakdown,
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
            int(template["id"]): template for template in get_poll_templates() if "id" in template
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
                    restored = PollData(
                        kind=str(row.get("kind") or "regular"),
                        status=str(row.get("status") or "open"),
                        poll_template_id=row.get("poll_template_id"),
                        poll_name_snapshot=str(row.get("poll_name_snapshot") or ""),
                        question_snapshot=str(row.get("question_snapshot") or ""),
                        opened_at=str(row.get("opened_at") or ""),
                        chat_id=int(row["chat_id"]),
                        poll_msg_id=int(row["poll_message_id"]),
                        info_msg_id=row.get("info_message_id"),
                        final_message_id=row.get("final_message_id"),
                        yes_voters=[
                            VoterInfo(**item)
                            for item in fallback.get("yes_voters", [])
                            if isinstance(item, dict)
                        ],
                        last_message_text=str(
                            fallback.get(
                                "last_message_text",
                                row.get("last_info_text") or "⏳ Идёт сбор голосов...",
                            )
                        ),
                        subs=list(fallback.get("subs", template_subs)),
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
                logging.warning(
                    f"⚠️ Не удалось восстановить опросов из games: {failed}"
                )
            return

        for poll_id, data in stored.items():
            try:
                restored = PollData(**data)
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
        logging.debug("Создана новая задача отложенного обновления (10 сек)")

    def update_voters(
        self,
        poll_id: str,
        user_id: int,
        user_name: str,
        update_id: int,
        voted_yes: bool,
    ) -> list[VoterInfo]:
        """
        Обновить список голосующих.

        Args:
            poll_id: ID опроса
            user_id: ID пользователя
            user_name: Имя пользователя
            update_id: ID обновления
            voted_yes: Проголосовал ли "Да"

        Returns:
            Обновлённый отсортированный список голосующих
        """
        if poll_id not in self._poll_data:
            return []

        data = self._poll_data[poll_id]
        yes_voters = data.yes_voters

        # Удаляем пользователя, если был
        yes_voters = [v for v in yes_voters if v.id != user_id]

        if voted_yes:
            yes_voters.append(
                VoterInfo(id=user_id, name=user_name, update_id=update_id)
            )

        sorted_yes_voters = sort_voters_by_update_id(yes_voters, data.subs)
        data.yes_voters = sorted_yes_voters
        return sorted_yes_voters

    async def send_poll(
        self,
        bot: Bot,
        chat_id: int,
        question: str,
        poll_name: str,
        bot_enabled: bool,
        subs: list[int] | None = None,
        options: list[str] | None = None,
        allows_multiple_answers: bool = False,
        poll_kind: str = "regular",
        option_poll_names: list[str | None] | None = None,
        poll_template_id: int | None = None,
    ) -> int:
        """
        Отправка опроса в чат.

        Args:
            bot: Экземпляр бота
            chat_id: ID чата для отправки
            question: Текст вопроса опроса
            poll_name: Название опроса для логирования
            bot_enabled: Флаг включения бота
            subs: Список ID подписчиков

        Returns:
            Новый chat_id (может измениться при миграции группы)
        """
        if not bot_enabled:
            logging.info(f"⏸️ Бот выключен, опрос '{poll_name}' не создан")
            return chat_id

        logging.info(f"📋 Создание опроса '{poll_name}' в чате {chat_id}")
        logging.debug(f"  Вопрос: {question}")
        poll_options = options if options is not None else list(POLL_OPTIONS)
        logging.debug(f"  Опции: {poll_options}")
        logging.debug(f"  Подписчиков: {len(subs) if subs else 0}")

        try:

            @retry_async((TelegramNetworkError, asyncio.TimeoutError, OSError))
            async def send_poll_with_retry():
                return await bot.send_poll(
                    chat_id=chat_id,
                    question=question,
                    options=poll_options,
                    is_anonymous=False,
                    allows_multiple_answers=allows_multiple_answers,
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

                @retry_async(
                    (TelegramNetworkError, asyncio.TimeoutError, OSError),
                    tries=2,
                    delay=1,
                )
                async def notify_migration():
                    await bot.send_message(
                        chat_id=new_chat_id, text=error_msg, parse_mode="Markdown"
                    )

                await notify_migration()
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

                @retry_async(
                    (TelegramNetworkError, asyncio.TimeoutError, OSError),
                    tries=2,
                    delay=1,
                )
                async def notify_error():
                    await bot.send_message(
                        chat_id=chat_id, text=error_msg, parse_mode="Markdown"
                    )

                await notify_error()
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

            @retry_async((TelegramNetworkError, asyncio.TimeoutError, OSError))
            async def send_info_with_retry():
                return await bot.send_message(
                    chat_id=chat_id, text="⏳ Идёт сбор голосов..."
                )

            info_message = await send_info_with_retry()
            logging.debug(
                f"✅ Информационное сообщение отправлено, message_id={info_message.message_id}"
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
        target_month_snapshot = (
            get_next_month_str(opened_dt)
            if poll_kind == "monthly_subscription"
            else None
        )
        poll_templates = get_poll_templates()
        poll_template = next(
            (
                template
                for template in poll_templates
                if poll_template_id is not None
                and int(template.get("id", 0) or 0) == poll_template_id
            ),
            None,
        )
        place_snapshot = (
            str(poll_template.get("place") or "") if poll_template is not None else ""
        )
        cost_snapshot = int(poll_template.get("cost", 0) or 0) if poll_template else 0
        cost_per_game_snapshot = (
            int(poll_template.get("cost_per_game", 0) or 0) if poll_template else 0
        )
        create_game(
            poll_id=poll_message.poll.id,
            kind=poll_kind,
            status="open",
            poll_template_id=poll_template_id,
            poll_name_snapshot=poll_name,
            question_snapshot=question,
            chat_id=chat_id,
            poll_message_id=poll_message.message_id,
            info_message_id=info_message.message_id if info_message else None,
            opened_at=opened_at,
            place_snapshot=place_snapshot,
            cost_snapshot=cost_snapshot,
            cost_per_game_snapshot=cost_per_game_snapshot,
            target_month_snapshot=target_month_snapshot,
            options=poll_options,
            option_poll_names=option_poll_names or [],
        )
        update_game_info_message(
            poll_message.poll.id,
            info_message_id=info_message.message_id if info_message else None,
            last_info_text="⏳ Идёт сбор голосов...",
        )

        self._poll_data[poll_message.poll.id] = PollData(
            kind=poll_kind,
            status="open",
            poll_template_id=poll_template_id,
            poll_name_snapshot=poll_name,
            question_snapshot=question,
            opened_at=opened_at,
            chat_id=chat_id,
            poll_msg_id=poll_message.message_id,
            info_msg_id=info_message.message_id if info_message else None,
            final_message_id=None,
            yes_voters=[],
            last_message_text="⏳ Идёт сбор голосов...",
            subs=subs or [],
            options=poll_options,
            option_poll_names=option_poll_names or [],
            target_month=target_month_snapshot,
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
        """Обновить список игроков с задержкой 10 секунд."""
        logging.debug(
            f"Задержка 10 секунд перед обновлением списка игроков для опроса {poll_id}..."
        )
        await asyncio.sleep(10)

        if poll_id not in self._poll_data:
            logging.debug(f"Опрос {poll_id} больше не существует, отмена обновления")
            return

        data = self._poll_data[poll_id]
        yes_voters: list[VoterInfo] = sort_voters_by_update_id(
            data.yes_voters, data.subs
        )
        data.yes_voters = yes_voters

        # Формируем текст (HTML-разметка)
        text: str
        if len(yes_voters) == 0:
            text = "⏳ Идёт сбор голосов..."
        elif len(yes_voters) < MIN_PLAYERS:
            text = (
                f"⏳ <b>Идёт сбор голосов:</b> "
                f"{len(yes_voters)}/{MIN_PLAYERS}\n\n"
                "<b>Проголосовали:</b>\n"
            )
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(yes_voters)
            )
        elif len(yes_voters) <= MAX_PLAYERS:
            text = "✅ <b>Список игроков:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(yes_voters)
            )
        elif len(yes_voters) <= MAX_PLAYERS + RESERVE_PLAYERS:
            main_players: list[VoterInfo] = yes_voters[:MAX_PLAYERS]
            reserves: list[VoterInfo] = yes_voters[MAX_PLAYERS:]

            text = "✅ <b>Список игроков:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(main_players)
            )
            text += "\n\n🕗 <b>Запасные игроки:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(reserves)
            )
        else:
            main_players = yes_voters[:MAX_PLAYERS]
            reserves = yes_voters[MAX_PLAYERS : MAX_PLAYERS + RESERVE_PLAYERS]
            booked: list[VoterInfo] = yes_voters[MAX_PLAYERS + RESERVE_PLAYERS :]

            text = "✅ <b>Список игроков:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(main_players)
            )
            text += "\n\n🕗 <b>Запасные игроки:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(reserves)
            )
            text += "\n\n🎫 <b>Забронированные места:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(booked)
            )

        # Добавляем легенду
        text += "\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"

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
                main_count = min(len(yes_voters), MAX_PLAYERS)
                reserve_count = max(
                    0, min(len(yes_voters) - MAX_PLAYERS, RESERVE_PLAYERS)
                )
                booked_count = max(0, len(yes_voters) - MAX_PLAYERS - RESERVE_PLAYERS)
                logging.info(
                    f"✅ Список игроков обновлен для опроса {poll_id}: {len(yes_voters)} человек "
                    f"(основных: {main_count}, запасных: {reserve_count}, забронированных: {booked_count})"
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

        # Формируем финальный список
        yes_voters: list[VoterInfo] = data.yes_voters

        final_text: str
        if len(yes_voters) == 0:
            final_text = "📊 <b>Голосование завершено</b>\n\nНикто не записался."
        elif len(yes_voters) < MIN_PLAYERS:
            final_text = (
                f"📊 <b>Голосование завершено:</b> "
                f"{len(yes_voters)}/{MIN_PLAYERS}\n\n"
                "<b>Записались:</b>\n"
            )
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(yes_voters)
            )
            final_text += "\n\n⚠️ <b>Не хватает игроков!</b>"
        elif len(yes_voters) <= MAX_PLAYERS:
            final_text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(yes_voters)}):</b>\n"
            )
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(yes_voters)
            )
        elif len(yes_voters) <= MAX_PLAYERS + RESERVE_PLAYERS:
            main_players: list[VoterInfo] = yes_voters[:MAX_PLAYERS]
            reserves: list[VoterInfo] = yes_voters[MAX_PLAYERS:]

            final_text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(main_players)}):</b>\n"
            )
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(main_players)
            )
            final_text += f"\n\n🕗 <b>Запасные ({len(reserves)}):</b>\n"
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(reserves)
            )
        else:
            main_players = yes_voters[:MAX_PLAYERS]
            reserves = yes_voters[MAX_PLAYERS : MAX_PLAYERS + RESERVE_PLAYERS]
            booked: list[VoterInfo] = yes_voters[MAX_PLAYERS + RESERVE_PLAYERS :]

            final_text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(main_players)}):</b>\n"
            )
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(main_players)
            )
            final_text += f"\n\n🕗 <b>Запасные ({len(reserves)}):</b>\n"
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(reserves)
            )
            final_text += f"\n\n🎫 <b>Забронированные места ({len(booked)}):</b>\n"
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(booked)
            )
            final_text += "\n\n⚠️ <b>Превышен лимит игроков!</b>"

        # Добавляем легенду
        final_text += "\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"

        # Добавляем реквизиты для перевода
        payment_lines = [
            line for line in (
                escape_html(PAYMENT_NAME),
                escape_html(PAYMENT_BANK),
                escape_html(PAYMENT_PHONE),
            ) if line
        ]
        if payment_lines:
            final_text += "\n\n<b>Реквизиты для перевода:</b>\n"
            final_text += "\n".join(payment_lines)

        # Обработка списания средств для платных залов
        charge_rows = await self._process_payment_deduction(
            bot, poll_name, yes_voters, data.subs
        )

        # Отправляем финальный список новым сообщением с ответом на голосовалку
        info_msg_id = data.info_msg_id
        final_message_id: int | None = None
        try:
            logging.debug(
                f"Отправка финального списка новым сообщением для опроса '{poll_name}'..."
            )

            @retry_async(
                (TelegramNetworkError, asyncio.TimeoutError, OSError),
                tries=3,
                delay=2,
            )
            async def send_final_with_retry():
                return await bot.send_message(
                    chat_id=data.chat_id,
                    reply_to_message_id=data.poll_msg_id,
                    text=final_text,
                    parse_mode="HTML",
                )

            final_message = await send_final_with_retry()
            final_message_id = final_message.message_id
            data.final_message_id = final_message_id
            main_count = min(len(yes_voters), MAX_PLAYERS)
            reserve_count = max(0, min(len(yes_voters) - MAX_PLAYERS, RESERVE_PLAYERS))
            booked_count = max(0, len(yes_voters) - MAX_PLAYERS - RESERVE_PLAYERS)
            logging.info(
                f"✅ Финальный список отправлен новым сообщением для '{poll_name}': "
                f"{len(yes_voters)} участников (основных: {main_count}, "
                f"запасных: {reserve_count}, забронированных: {booked_count})"
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
        for index, voter in enumerate(yes_voters):
            if index < MAX_PLAYERS:
                bucket = "main"
            elif index < MAX_PLAYERS + RESERVE_PLAYERS:
                bucket = "reserve"
            else:
                bucket = "booked"
            charge = charge_by_player.get(voter.id, {})
            participant_rows.append(
                {
                    "player_id": voter.id,
                    "roster_bucket": bucket,
                    "sort_order": index + 1,
                    "is_subscriber": bool(charge.get("is_subscriber", voter.id in data.subs)),
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

        result = calculate_subscription(
            paid_polls, votes_by_poll, target_month, fund_balance
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

            @retry_async(
                (TelegramNetworkError, asyncio.TimeoutError, OSError),
                tries=3,
                delay=2,
            )
            async def send_final_with_retry():
                try:
                    return await bot.send_message(
                        chat_id=data.chat_id,
                        reply_to_message_id=data.poll_msg_id,
                        text=final_text,
                        parse_mode="HTML",
                    )
                except TelegramBadRequest as e:
                    if "message to be replied not found" in e.message:
                        logging.warning(
                            "⚠️ Сообщение для ответа не найдено, отправляем новым сообщением"
                        )
                        return await bot.send_message(
                            chat_id=data.chat_id,
                            text=final_text,
                            parse_mode="HTML",
                        )
                    raise

            final_message = await send_final_with_retry()
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

                @retry_async(
                    (TelegramNetworkError, asyncio.TimeoutError, OSError),
                    tries=3,
                    delay=2,
                )
                async def send_admin_report():
                    if ADMIN_USER_ID is not None:
                        await bot.send_message(
                            chat_id=ADMIN_USER_ID,
                            text=admin_report,
                            parse_mode="HTML",
                        )

                await send_admin_report()
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
        for h in result.hall_breakdown:
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
                text += (
                    f"{i}. {player_link} - {amount_due} ₽\n"
                )
        text += f"\n🏦 Касса: <b>{fund_balance} ₽</b>"
        text += f"\n💸 Ожидаемая сумма к оплате: <b>{total_due} ₽</b>"
        payment_lines = [
            line for line in (
                escape_html(PAYMENT_NAME),
                escape_html(PAYMENT_BANK),
                escape_html(PAYMENT_PHONE),
            ) if line
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
        total_due = sum(max(int(sub["amount"]) - int(sub["old_balance"]), 0) for sub in charged_subscribers)
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
        yes_voters: list[VoterInfo],
        subs: list[int],
    ) -> list[dict[str, Any]]:
        """
        Обработка списания средств с игроков без подписки для платных залов.

        Args:
            bot: Экземпляр бота
            poll_name: Название опроса
            yes_voters: Список проголосовавших "Да"
            subs: Список ID подписчиков
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
                    "player_id": voter.id,
                    "name": voter.name,
                    "is_subscriber": voter.id in subs,
                    "charged_amount": 0,
                    "charge_source": "none",
                    "balance_before": None,
                    "balance_after": None,
                }
                for voter in yes_voters
            ]

        logging.info(
            f"💳 Начало списания для опроса '{poll_name}' (стоимость: {cost}₽)"
        )

        # Список для статистики
        charged_players: list[dict[str, Any]] = []
        subscribed_players: list[str] = []
        participant_finance_rows: list[dict[str, Any]] = []

        # Проходим по всем проголосовавшим (основной состав + запасные)
        for voter in yes_voters:
            # Проверяем, есть ли у игрока подписка
            if voter.id in subs:
                subscribed_players.append(voter.name)
                participant_finance_rows.append(
                    {
                        "player_id": voter.id,
                        "name": voter.name,
                        "is_subscriber": True,
                        "charged_amount": 0,
                        "charge_source": "subscription",
                        "balance_before": None,
                        "balance_after": None,
                    }
                )
                logging.debug(
                    f"  ⏭️  Игрок {voter.name} (ID: {voter.id}) с подпиской, списание пропущено"
                )
                continue

            # Убеждаемся, что игрок есть в БД
            ensure_player(voter.id, voter.name)

            # Получаем текущий баланс (get_player_balance возвращает dict с ключом "balance" или None)
            player_data = get_player_balance(voter.id)
            old_balance = player_data.get("balance", 0) if player_data else 0

            # Списываем средства
            update_player_balance(voter.id, -cost)
            new_balance = old_balance - cost

            # Добавляем транзакцию в историю
            game_date = datetime.now().strftime("%d.%m.%Y")
            description = f"Зал: {poll_name} ({game_date})"
            add_transaction(
                voter.id,
                -cost,
                description,
                poll_template_id=int(poll_config["id"]),
                poll_name_snapshot=poll_name,
            )

            charged_players.append(
                {
                    "name": voter.name,
                    "id": voter.id,
                    "old_balance": old_balance,
                    "new_balance": new_balance,
                }
            )
            participant_finance_rows.append(
                {
                    "player_id": voter.id,
                    "name": voter.name,
                    "is_subscriber": False,
                    "charged_amount": cost,
                    "charge_source": "single_game",
                    "balance_before": old_balance,
                    "balance_after": new_balance,
                }
            )

            logging.info(
                f"  💳 Списано {cost}₽ с {voter.name} (ID: {voter.id}), "
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
            f"итого {total_charged}₽. С подпиской: {len(subscribed_players)}"
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

            @retry_async(
                (TelegramNetworkError, asyncio.TimeoutError, OSError), tries=3, delay=2
            )
            async def send_report_with_retry():
                if ADMIN_USER_ID is not None:
                    await bot.send_message(
                        chat_id=ADMIN_USER_ID, text=report, parse_mode="HTML"
                    )

            await send_report_with_retry()
            logging.info("✅ Сводка о списании отправлена админу")
        except (TelegramAPIError, TelegramNetworkError, asyncio.TimeoutError, OSError):
            logging.exception(
                f"❌ Не удалось отправить сводку о списании админу (ID: {ADMIN_USER_ID})"
            )
