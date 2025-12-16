"""Логика работы с опросами."""

import asyncio
import logging
import traceback
from asyncio import Task

from aiogram import Bot
from aiogram.exceptions import TelegramMigrateToChat
from pydantic import BaseModel, Field

from .config import POLL_OPTIONS, REQUIRED_PLAYERS
from .db import POLL_STATE_KEY, load_state, save_state
from .utils import escape_html, save_error_dump


class VoterInfo(BaseModel):
    """Информация о проголосовавшем."""

    id: int = Field(..., description="ID пользователя Telegram")
    name: str = Field(..., description="Имя пользователя")
    update_id: int = Field(default=0, description="ID обновления для сортировки")

    model_config = {"frozen": False}  # Разрешаем изменение полей


class PollData(BaseModel):
    """Данные активного опроса."""

    chat_id: int = Field(..., description="ID чата")
    poll_msg_id: int = Field(..., description="ID сообщения с опросом")
    info_msg_id: int | None = Field(
        default=None, description="ID информационного сообщения"
    )
    yes_voters: list[VoterInfo] = Field(
        default_factory=list, description="Список проголосовавших 'Да'"
    )
    last_message_text: str = Field(
        default="⏳ Идёт сбор голосов...", description="Последний отправленный текст"
    )
    subs: list[int] = Field(default_factory=list, description="Список ID подписчиков")
    # update_task не сериализуется, хранится отдельно

    model_config = {"arbitrary_types_allowed": True, "frozen": False}


# Глобальное хранилище данных опросов
poll_data: dict[str, PollData] = {}
# Отдельное хранилище для задач обновления (не сериализуются)
update_tasks: dict[str, Task[None] | None] = {}


def persist_poll_state() -> None:
    """Сохраняет актуальное состояние опросов в базе."""
    serializable: dict[str, dict] = {}
    for poll_id, data in poll_data.items():
        # Используем model_dump для сериализации Pydantic модели
        serializable[poll_id] = data.model_dump(mode="json")
    save_state(POLL_STATE_KEY, serializable)


def load_persisted_poll_state() -> None:
    """Восстанавливает состояние опросов из базы после рестарта."""
    stored = load_state(POLL_STATE_KEY, default={})
    if not isinstance(stored, dict):
        logging.warning(
            "Сохранённое состояние опросов повреждено, пропускаем восстановление"
        )
        return

    poll_data.clear()
    update_tasks.clear()

    for poll_id, data in stored.items():
        try:
            # Восстанавливаем из словаря в Pydantic модель
            restored = PollData(**data)
            poll_data[poll_id] = restored
            update_tasks[poll_id] = None
        except Exception as e:
            logging.error(f"Не удалось восстановить состояние опроса {poll_id}: {e}")


def sort_voters_by_update_id(voters: list[VoterInfo]) -> list[VoterInfo]:
    """Возвращает список голосовавших, отсортированный по update_id (порядок событий)."""
    return sorted(voters, key=lambda v: (v.update_id, v.id))


async def send_poll(
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

    poll_data.clear()
    update_tasks.clear()
    persist_poll_state()

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
        await bot.pin_chat_message(chat_id=chat_id, message_id=poll_message.message_id)
    except Exception as e:
        logging.warning(f"Не удалось закрепить сообщение: {e}")

    # Сохраняем данные опроса
    if poll_message.poll is None:
        logging.error(f"Опрос создан, но poll объект отсутствует для '{poll_name}'")
        return chat_id

    poll_data[poll_message.poll.id] = PollData(
        chat_id=chat_id,
        poll_msg_id=poll_message.message_id,
        info_msg_id=info_message.message_id if info_message else None,
        yes_voters=[],
        last_message_text="⏳ Идёт сбор голосов...",
        subs=subs or [],
    )
    update_tasks[poll_message.poll.id] = None
    persist_poll_state()

    logging.info(f"Создан {poll_name} {poll_message.poll.id}")
    return chat_id


async def update_players_list(bot: Bot, poll_id: str) -> None:
    """Обновляет список игроков с задержкой 10 секунд."""
    await asyncio.sleep(10)

    if poll_id not in poll_data:
        return

    data = poll_data[poll_id]
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
        update_tasks[poll_id] = None
        persist_poll_state()
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

    update_tasks[poll_id] = None
    persist_poll_state()


async def close_poll(bot: Bot, poll_name: str) -> None:
    """
    Закрытие активного опроса и публикация финального списка.

    Args:
        bot: Экземпляр бота
        poll_name: Название опроса для логирования
    """

    if not poll_data:
        logging.info(f"Нет активных опросов для закрытия ({poll_name})")
        return

    # Берём первый (и обычно единственный) активный опрос
    poll_id: str = list(poll_data.keys())[0]
    data: PollData = poll_data[poll_id]

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
    del poll_data[poll_id]
    if poll_id in update_tasks:
        del update_tasks[poll_id]
    persist_poll_state()
    logging.info(f"Опрос '{poll_name}' закрыт, данные очищены")
