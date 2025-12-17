"""Сервис для управления состоянием опросов."""

from __future__ import annotations

import asyncio
import logging
import traceback
from asyncio import Task

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat

from ..config import POLL_OPTIONS, REQUIRED_PLAYERS
from ..db import POLL_STATE_KEY, load_state, save_state
from ..poll import PollData, VoterInfo, sort_voters_by_update_id
from ..utils import escape_html, save_error_dump


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
        stored = load_state(POLL_STATE_KEY, default={})
        if not isinstance(stored, dict):
            logging.warning(
                "Сохранённое состояние опросов повреждено, пропускаем восстановление"
            )
            return

        self._poll_data.clear()
        self._update_tasks.clear()

        for poll_id, data in stored.items():
            try:
                restored = PollData(**data)
                self._poll_data[poll_id] = restored
                self._update_tasks[poll_id] = None
            except Exception as e:
                logging.error(
                    f"Не удалось восстановить состояние опроса {poll_id}: {e}"
                )

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

        sorted_yes_voters = sort_voters_by_update_id(yes_voters)
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
            logging.info(f"Бот выключен, {poll_name} не создан")
            return chat_id

        self.clear_all_polls()
        self.persist_state()

        try:
            poll_message = await bot.send_poll(
                chat_id=chat_id,
                question=question,
                options=list(POLL_OPTIONS),
                is_anonymous=False,
            )
        except TelegramMigrateToChat as e:
            new_chat_id: int = e.migrate_to_chat_id
            logging.error(
                f"Группа мигрирована в супергруппу. Старый ID: {chat_id}, Новый ID: {new_chat_id}"
            )
            save_error_dump(e, poll_name, question, chat_id)

            try:
                error_msg: str = (
                    f'❌ *Ошибка при создании опроса "{poll_name}"*\n\n'
                    f"Группа была мигрирована в супергруппу.\n"
                    f"Новый ID чата: `{new_chat_id}`"
                )
                await bot.send_message(
                    chat_id=new_chat_id, text=error_msg, parse_mode="Markdown"
                )
            except Exception as send_error:
                logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")

            return new_chat_id

        except Exception as e:
            logging.error(f'Ошибка при создании опроса "{poll_name}": {e}')
            logging.error(traceback.format_exc())
            save_error_dump(e, poll_name, question, chat_id)

            try:
                error_msg = (
                    f'❌ *Ошибка при создании опроса "{poll_name}"*\n\n'
                    f"Не удалось создать опрос. Пожалуйста, проверьте логи и файл дампа для подробностей."
                )
                await bot.send_message(
                    chat_id=chat_id, text=error_msg, parse_mode="Markdown"
                )
            except Exception as send_error:
                logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")

            return chat_id

        # Отправляем информационное сообщение
        info_message = None
        try:
            info_message = await bot.send_message(
                chat_id=chat_id, text="⏳ Идёт сбор голосов..."
            )
        except Exception as e:
            logging.error(f"Ошибка при отправке информационного сообщения: {e}")

        # Закрепляем опрос
        try:
            await bot.pin_chat_message(
                chat_id=chat_id, message_id=poll_message.message_id
            )
        except Exception as e:
            logging.warning(f"Не удалось закрепить сообщение: {e}")

        # Сохраняем данные опроса
        if poll_message.poll is None:
            logging.error(f"Опрос создан, но poll объект отсутствует для '{poll_name}'")
            return chat_id

        self._poll_data[poll_message.poll.id] = PollData(
            chat_id=chat_id,
            poll_msg_id=poll_message.message_id,
            info_msg_id=info_message.message_id if info_message else None,
            yes_voters=[],
            last_message_text="⏳ Идёт сбор голосов...",
            subs=subs or [],
        )
        self._update_tasks[poll_message.poll.id] = None
        self.persist_state()

        logging.info(f"Создан {poll_name} {poll_message.poll.id}")
        return chat_id

    async def _update_players_list(self, bot: Bot, poll_id: str) -> None:
        """Обновить список игроков с задержкой 10 секунд."""
        await asyncio.sleep(10)

        if poll_id not in self._poll_data:
            return

        data = self._poll_data[poll_id]
        yes_voters: list[VoterInfo] = sort_voters_by_update_id(data.yes_voters)
        data.yes_voters = yes_voters

        # Формируем текст (HTML-разметка)
        text: str
        if len(yes_voters) == 0:
            text = "⏳ Идёт сбор голосов..."
        elif len(yes_voters) < REQUIRED_PLAYERS:
            text = (
                f"⏳ <b>Идёт сбор голосов:</b> "
                f"{len(yes_voters)}/{REQUIRED_PLAYERS}\n\n"
                "<b>Проголосовали:</b>\n"
            )
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(yes_voters)
            )
        else:
            main_players: list[VoterInfo] = yes_voters[:REQUIRED_PLAYERS]
            reserves: list[VoterInfo] = yes_voters[REQUIRED_PLAYERS:]

            text = "✅ <b>Список игроков:</b>\n"
            text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(main_players)
            )

            if reserves:
                text += "\n\n🕗 <b>Запасные игроки:</b>\n"
                text += "\n".join(
                    f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(reserves)
                )

        # Добавляем легенду
        text += "\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"

        info_msg_id = data.info_msg_id
        if info_msg_id is None:
            logging.warning(
                f"info_msg_id отсутствует для опроса {poll_id}, пропускаем обновление списка игроков"
            )
            self._update_tasks[poll_id] = None
            self.persist_state()
            return

        if text == data.last_message_text:
            logging.debug("Текст не изменился, пропускаем обновление")
        else:
            try:
                await bot.edit_message_text(
                    chat_id=data.chat_id,
                    message_id=info_msg_id,
                    text=text,
                    parse_mode="HTML",
                )
                data.last_message_text = text
                logging.info(f"Список игроков обновлен: {len(yes_voters)} человек")
            except Exception as e:
                logging.error(f"Ошибка редактирования сообщения: {e}")

        self._update_tasks[poll_id] = None
        self.persist_state()

    async def close_poll(self, bot: Bot, poll_name: str) -> None:
        """
        Закрыть активный опрос и опубликовать финальный список.

        Args:
            bot: Экземпляр бота
            poll_name: Название опроса для логирования
        """
        if not self.has_active_polls():
            logging.info(f"Нет активных опросов для закрытия ({poll_name})")
            return

        # Берём первый (и обычно единственный) активный опрос
        first_poll = self.get_first_poll()
        if first_poll is None:
            return

        poll_id, data = first_poll

        # Останавливаем опрос
        try:
            await bot.stop_poll(chat_id=data.chat_id, message_id=data.poll_msg_id)
            logging.info(f"Опрос '{poll_name}' остановлен")
        except Exception as e:
            logging.error(f"Ошибка при остановке опроса '{poll_name}': {e}")

        # Формируем финальный список
        yes_voters: list[VoterInfo] = data.yes_voters

        final_text: str
        if len(yes_voters) == 0:
            final_text = "📊 <b>Голосование завершено</b>\n\nНикто не записался."
        elif len(yes_voters) < REQUIRED_PLAYERS:
            final_text = (
                f"📊 <b>Голосование завершено:</b> "
                f"{len(yes_voters)}/{REQUIRED_PLAYERS}\n\n"
                "<b>Записались:</b>\n"
            )
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(yes_voters)
            )
            final_text += "\n\n⚠️ <b>Не хватает игроков!</b>"
        else:
            main_players: list[VoterInfo] = yes_voters[:REQUIRED_PLAYERS]
            reserves: list[VoterInfo] = yes_voters[REQUIRED_PLAYERS:]

            final_text = (
                "📊 <b>Голосование завершено</b> ✅\n\n"
                f"<b>Основной состав ({len(main_players)}):</b>\n"
            )
            final_text += "\n".join(
                f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(main_players)
            )

            if reserves:
                final_text += f"\n\n🕗 <b>Запасные ({len(reserves)}):</b>\n"
                final_text += "\n".join(
                    f"{i + 1}) {escape_html(p.name)}" for i, p in enumerate(reserves)
                )

        # Добавляем легенду
        final_text += "\n\n⭐️ — оплативший за месяц\n🏐 — донат на мяч"

        # Обновляем информационное сообщение с финальным списком
        info_msg_id = data.info_msg_id
        if info_msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=data.chat_id,
                    message_id=info_msg_id,
                    text=final_text,
                    parse_mode="HTML",
                )
                logging.info(f"Финальный список опубликован для '{poll_name}'")
            except Exception as e:
                logging.error(f"Ошибка обновления финального сообщения: {e}")
        else:
            logging.warning(
                f"info_msg_id отсутствует для '{poll_name}', финальное сообщение не обновлено"
            )

        # Очищаем данные опроса
        self.delete_poll(poll_id)
        self.persist_state()
        logging.info(f"Опрос '{poll_name}' закрыт, данные очищены")
