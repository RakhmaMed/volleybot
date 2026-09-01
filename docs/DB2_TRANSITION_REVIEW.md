# Переход с `db.py` на `db2.py`: решения, ошибки и план работ

Дата ревью: 2026-08-16

Этот документ фиксирует архитектурные решения по переходу на `db2.py`, объясняет найденные проблемы и содержит список работ, которые важно не потерять.

## Зафиксированные решения

1. Runtime-миграции схемы не нужны: проект ведёт один разработчик, приложение развёрнуто в одном экземпляре, а файл БД можно подготовить перед запуском новой версии.
2. Строгая проверка схемы и данных обязательна, но основная полная проверка должна стать deploy-gate, а не выполняться перед каждым запросом.
3. Приложение должно использовать одно долгоживущее SQLite-соединение.
4. Все операции с этим соединением выполняются в одном OS thread — потоке asyncio event loop.
5. Бизнес-операции из нескольких SQL-запросов должны иметь одну атомарную транзакционную границу.
6. Ошибки должны возвращаться через типизированный `Result`, а ожидаемые варианты — через enum или отдельные типы ошибок.
7. `get_player_balance()` намеренно возвращает только `int`; полные данные игрока нужно получать через `get_player_info()`.
8. Загрузка monthly votes намеренно строгая: одна повреждённая запись делает ошибкой весь результат.
9. Путь к БД разрешается один раз в composition root и не меняется во время жизни приложения.
10. Перевод всех production call sites и исправление truthiness-проверок будет отдельным этапом.
11. Перенос и переработка backup API нужны, но не являются первым приоритетом.

---

# Краткий checklist

## P0 — сделать до переключения production-кода

- [ ] Разделить создание новой БД, подготовку существующей БД и runtime-открытие готовой БД.
- [ ] Убрать автоматическое создание/изменение схемы из production-конструктора `DB`.
- [ ] Добавить CLI-команды `db create`, `db validate` и при необходимости одноразовую `db prepare`.
- [ ] Валидировать БД новым образом/кодом до запуска приложения.
- [ ] Проверять `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, версию и полную структуру схемы.
- [ ] Добавить обязательный data invariant: ключ `fund_balance` существует и содержит корректный integer.
- [ ] Перевести соединение на явное управление транзакциями.
- [ ] Исправить `@transactional`: операция должна сама владеть `BEGIN`/`COMMIT`/`ROLLBACK`.
- [ ] Не применять commit-producing декоратор к обычным read-методам.
- [ ] Определить правило вложенности: приватные raw-шаги без транзакций либо `SAVEPOINT`.
- [ ] Сделать DB-зависимый `clear_job` асинхронным.
- [ ] Запретить DB-вызовы через `run_in_executor`, `asyncio.to_thread` и обычные APScheduler sync jobs.
- [ ] Не держать транзакцию открытой через `await`.
- [ ] Ввести базовый тип `DBError` и mapper `sqlite3.Error -> DBError`.
- [ ] Различать UNIQUE, PRIMARY KEY, FOREIGN KEY, CHECK и NOT NULL.

## P1 — выполнить во время перевода call sites

- [ ] Определить контракты `find_*`, `get_*` и `require_*` для отсутствующих сущностей.
- [ ] Для нормального отсутствия использовать `Result[Maybe[T], DBError]` или собственный `Found/Missing` ADT.
- [ ] Для обязательного состояния возвращать `InvariantViolation`, а не `Nothing`.
- [ ] Заменить строковые статусы на `StrEnum` или dataclass-union ошибок.
- [ ] Явно разбирать каждый `Result`; не использовать truthiness контейнера.
- [ ] Проверять результаты write-операций, не игнорировать `Failure`.
- [ ] В местах, где нужны имя/username/fullname, заменить старый `get_player_balance()` на `get_player_info()`.
- [ ] Передать один экземпляр `DB` в сервисы, handlers и scheduler через composition root.
- [ ] Реализовать корректный shutdown: остановить producers, сохранить состояние, сделать backup, закрыть DB.
- [ ] Закрывать соединение и при частично неуспешном startup.

## P2 — тесты, backup и технический долг

- [ ] Перевести integration tests с raw `True`/`False`/`None`/`dict` на `Success`/`Failure`.
- [ ] Удалить прямые импорты `src.db` из тестов после завершения перехода.
- [ ] Добавить тесты deploy-validator на корректную, повреждённую и чужую БД.
- [ ] Добавить тест, что read-операция не коммитит внешние изменения.
- [ ] Добавить тесты rollback всех шагов составной операции.
- [ ] Добавить тесты вложенных `SAVEPOINT`, если они будут поддерживаться.
- [ ] Добавить тест вызова DB из другого потока и зафиксировать ожидаемый fail-fast.
- [ ] Добавить строгие тесты monthly votes, включая JSON `true`.
- [ ] Перенести `create_backup` и `cleanup_old_backups` на новую lifecycle-схему.
- [ ] Исправить literal `{user_id}` в `set_player_guest()`.
- [ ] Обновить docstring, которые всё ещё обещают `True`/`False`.

---

# 1. Схема без runtime-миграций

## Проблема

Отказ от runtime-миграций для одного разработчика и одного сервера — нормальное решение. Но сам факт изменения схемы никуда не исчезает: новую версию файла БД всё равно нужно получить воспроизводимым и безопасным способом.

Сейчас `DB.__init__` одновременно:

- создаёт каталог;
- открывает файл;
- создаёт таблицы, если их нет;
- пытается проверить схему.

Для production это опасно. Опечатка в пути или забытый volume может создать новую пустую БД, после чего приложение запустится так, будто всё нормально.

Если runtime-миграций больше не будет, production-приложение не должно автоматически выполнять `CREATE TABLE` или `ALTER TABLE`.

## Рекомендованное разделение ответственности

### `create_database(path)`

Используется только:

- в тестах;
- при первоначальном создании production-БД;
- утилитой подготовки нового файла.

Создаёт точную актуальную схему и обязательные начальные данные, включая `fund_balance`.

### `prepare_database(source, destination)`

Опциональная одноразовая deploy-утилита для конкретного изменения схемы.

Для небольшой SQLite-БД безопаснее не изменять production-файл на месте, а создать новый файл:

1. создать `volleybot.next.db` по актуальной схеме;
2. подключить старую БД через `ATTACH DATABASE`;
3. скопировать данные явными `INSERT ... SELECT ...`;
4. преобразовать изменённые поля;
5. создать обязательные служебные записи;
6. проверить новый файл;
7. атомарно заменить рабочий файл.

Это фактически одноразовая миграция, но без runtime migration framework и без цепочки миграций в приложении.

### `validate_database(path)`

Открывает файл read-only и собирает полный список проблем. Ничего не создаёт и не исправляет.

### `DB.open_existing(path)`

Production API:

- требует существующий файл;
- открывает его в read-write режиме без автоматического создания схемы;
- включает runtime PRAGMA;
- при желании выполняет только очень дешёвый startup guard;
- не выполняет полный `integrity_check` при каждом запуске и тем более при каждом запросе.

## Что должен проверять deploy-validator

### Файл и идентичность БД

- файл существует и является обычным файлом;
- SQLite может открыть его в read-only режиме;
- `PRAGMA application_id` совпадает с VolleyBot;
- `PRAGMA user_version` совпадает с ожидаемой версией схемы.

`application_id` полезен, чтобы случайно не запустить бота с другим SQLite-файлом.

### Физическая целостность

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
```

`integrity_check` должен вернуть единственную строку `ok`.

`foreign_key_check` не должен вернуть ни одной строки.

Для частых быстрых проверок можно применять `quick_check`, но перед deploy разумно использовать полный `integrity_check`.

### Структура схемы

Нужно проверять не только набор колонок:

- таблицы;
- колонки через `PRAGMA table_xinfo`;
- типы колонок;
- `NOT NULL`;
- default values;
- порядок и состав PRIMARY KEY;
- UNIQUE и обычные индексы через `PRAGMA index_list`, `index_info`, `index_xinfo`;
- FOREIGN KEY через `PRAGMA foreign_key_list`;
- `ON DELETE`/`ON UPDATE`;
- CHECK constraints — через нормализованный SQL из `sqlite_schema.sql` либо отдельные проверочные правила;
- обязательные служебные индексы.

Текущий `_validate_schema_strict()` проверяет только часть этих свойств и потому не является полной строгой проверкой.

### Data invariants

Валидация должна проверять данные, без которых приложение не может корректно работать:

- `fund_balance` существует ровно один раз;
- значение `fund_balance` преобразуется в `int`;
- обязательные JSON-поля декодируются;
- `monthly_poll_votes.option_ids_json` имеет ожидаемый формат;
- при необходимости все открытые игры имеют валидные snapshots;
- отсутствуют неизвестные `kind`, `status`, `charge_source` и другие enum-подобные значения.

## Баланс кассы

Если касса всегда должна существовать, правильный runtime-контракт:

```py
Result[int, DBError]
```

Отсутствие ключа — не `Success(0)` и не `Nothing`, а нарушение подготовленного состояния:

```py
Failure(InvariantViolation("Отсутствует обязательный fund_balance"))
```

Создание значения `0` должно происходить в `create_database` или `prepare_database`, а deploy-validator должен блокировать запуск, если записи нет или она повреждена.

В перспективе можно вынести кассу из общего JSON/text `kv_store` в отдельную таблицу с integer-колонкой. Тогда SQLite сам будет сильнее контролировать тип и структуру.

## Рекомендованный deploy flow

### Общая последовательность

1. Остановить старое приложение или гарантировать отсутствие записей.
2. Создать backup штатным SQLite backup API.
3. Подготовить новый файл БД рядом со старым.
4. Запустить validator из **нового Docker image**, а не из локального старого окружения.
5. Если validator успешен — атомарно заменить файл на том же filesystem.
6. Запустить новую версию приложения.
7. Выполнить smoke check и проверить логи.
8. При ошибке остановить приложение и восстановить backup.

Нельзя копировать только основной `.db` во время активных записей: при WAL/journal часть актуального состояния может находиться в соседних файлах. Либо приложение останавливается, либо используется `Connection.backup()`.

### Интеграция в текущий `manage.sh`

В проекте уже есть `db-pull` и `db-push`. Логичное продолжение:

```text
./manage.sh db-create <path>
./manage.sh db-prepare <source> <destination>
./manage.sh db-validate <path>
```

Перед запуском контейнера новый image можно вызвать одноразово:

```sh
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  volleybot:latest \
  python -m src.db_admin validate /app/data/volleybot.db
```

Только после exit code `0` запускается основной контейнер.

## Небольшая startup-проверка всё равно полезна

Даже при deploy validation разумно оставить дешёвый fail-fast guard:

- файл существует;
- `application_id` совпадает;
- `user_version` совпадает;
- обязательный `fund_balance` существует.

Это не постоянная валидация и почти не расходует ресурсы, зато защищает от неверного volume/path или ручной подмены файла после deploy.

---

# 2. Транзакции: `BEGIN`, границы и `SAVEPOINT`

## Что такое граница транзакции

Граница транзакции — это не отдельный SQL-запрос и не обязательно отдельная repository-функция. Это минимальная бизнес-операция, которая должна выполниться целиком или не выполниться совсем.

Пример одной транзакционной границы:

1. изменить баланс игрока;
2. изменить баланс кассы;
3. добавить строку истории транзакций.

Если третий шаг не удался, первые два должны быть отменены.

Именно функция вроде `update_player_and_fund_balance_atomic()` должна владеть транзакцией. Внутренние шаги не должны сами выполнять `commit()`.

## SQLite autocommit и Python

У текущего `sqlite3.connect(path)` используется legacy transaction control. Python неявно открывает транзакцию перед DML, но обычный `SELECT` её не открывает. Это создаёт скрытые границы и делает поведение декоратора менее очевидным.

Для полностью явного управления можно открыть соединение так:

```py
conn = sqlite3.connect(path, autocommit=True)
```

В этом режиме каждый запрос вне явной транзакции выполняется отдельно. Транзакция начинается SQL-командой `BEGIN`.

Важная особенность Python 3.14: при `autocommit=True` метод `conn.commit()` не завершает вручную открытый `BEGIN`. Нужно выполнять SQL-команды:

```py
conn.execute("COMMIT")
conn.execute("ROLLBACK")
```

Это поведение было проверено на используемом окружении Python 3.14 / SQLite 3.53.

При `autocommit=True` необходимо скрыть прямой доступ к соединению или договориться, что ни одна write-функция не выполняется вне транзакционного runner: иначе одиночный DML будет автоматически зафиксирован.

## Виды `BEGIN`

### `BEGIN DEFERRED`

- блокировка writer заранее не захватывается;
- подходит для read-only snapshot;
- write lock запрашивается только при первой записи;
- при конкуренции двух writers обновление read-транзакции до write может завершиться `SQLITE_BUSY`.

### `BEGIN IMMEDIATE`

- сразу резервирует право записи;
- другие readers обычно могут продолжать читать;
- другой writer не сможет начать запись;
- хороший default для коротких write-транзакций VolleyBot.

### `BEGIN EXCLUSIVE`

- более сильная блокировка;
- для текущего приложения обычно не нужна.

Рекомендация:

- `BEGIN IMMEDIATE` для составных write-операций;
- `BEGIN DEFERRED`/`BEGIN` для multi-query read snapshot;
- без транзакции для одного простого `SELECT`, если snapshot между несколькими запросами не требуется.

## Почему текущий декоратор неправильный

Текущий `@transactional` не выполняет `BEGIN`, но всегда вызывает `commit()` или `rollback()` всего соединения.

Из-за этого:

- read-функция может зафиксировать чужую незавершённую запись;
- вложенная функция может преждевременно зафиксировать внешнюю транзакцию;
- название декоратора обещает границу, которой на самом деле нет;
- multi-query read не получает единый snapshot.

## Предпочтительный простой вариант: граница только у верхней операции

Самый простой и надёжный подход — не делать вложенные публичные транзакционные операции.

- Публичная бизнес-операция владеет `BEGIN`/`COMMIT`/`ROLLBACK`.
- Приватные шаги принимают `Connection`, возвращают `Result`, но не управляют транзакцией.
- Составление шагов выполняется через `bind`.

### Базовые типы ошибок

```py
from dataclasses import dataclass
from enum import StrEnum


class ConstraintKind(StrEnum):
    UNIQUE = "unique"
    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    CHECK = "check"
    NOT_NULL = "not_null"


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    kind: ConstraintKind
    operation: str
    detail: str


@dataclass(frozen=True, slots=True)
class NotFound:
    entity: str
    key: object


@dataclass(frozen=True, slots=True)
class StorageFailure:
    operation: str
    sqlite_code: int | None
    sqlite_name: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class InvariantViolation:
    detail: str


type DBError = ConstraintViolation | NotFound | StorageFailure | InvariantViolation
type DBResult[T] = Result[T, DBError]
```

### Runner одной транзакции

```py
from collections.abc import Callable
from enum import StrEnum
import sqlite3

from returns.result import Failure, Result, Success


class TransactionMode(StrEnum):
    DEFERRED = "DEFERRED"
    IMMEDIATE = "IMMEDIATE"


def run_transaction[T](
    db: DB,
    operation: Callable[[sqlite3.Connection], DBResult[T]],
    *,
    mode: TransactionMode = TransactionMode.IMMEDIATE,
) -> DBResult[T]:
    conn = db.conn

    # При простом варианте вложенность считается ошибкой проектирования.
    if conn.in_transaction:
        return Failure(
            InvariantViolation("Попытка начать вложенную транзакцию без SAVEPOINT")
        )

    try:
        conn.execute(f"BEGIN {mode.value}")
        result = operation(conn)

        match result:
            case Success(_):
                conn.execute("COMMIT")
            case Failure(_):
                conn.execute("ROLLBACK")

        return result
    except sqlite3.Error as error:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        return Failure(map_sqlite_error(error, operation="transaction"))
    except BaseException:
        # Не превращаем programmer bug, KeyboardInterrupt или cancellation
        # в обычный DBError, но соединение обязательно возвращаем
        # в корректное состояние.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
```

В production-версии rollback helper должен отдельно обрабатывать ошибку самого rollback, чтобы она не скрыла исходную проблему.

### Приватные шаги

```py
def _update_player_balance(
    conn: sqlite3.Connection,
    player_id: int,
    amount: int,
) -> DBResult[None]:
    cursor = conn.execute(
        "UPDATE players SET balance = balance + ? WHERE id = ?",
        (amount, player_id),
    )
    if cursor.rowcount == 0:
        return Failure(NotFound("player", player_id))
    return Success(None)


def _update_fund_balance(
    conn: sqlite3.Connection,
    amount: int,
) -> DBResult[None]:
    conn.execute(
        """
        UPDATE kv_store
        SET value = CAST(value AS INTEGER) + ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE key = ?
        """,
        (amount, FUND_BALANCE_KEY),
    )
    return Success(None)


def _insert_transaction(
    conn: sqlite3.Connection,
    player_id: int,
    amount: int,
    description: str,
) -> DBResult[None]:
    conn.execute(
        """
        INSERT INTO transactions(player_id, amount, description)
        VALUES (?, ?, ?)
        """,
        (player_id, amount, description),
    )
    return Success(None)
```

### Выразительная композиция

```py
def update_player_and_fund_balance_atomic(
    db: DB,
    player_id: int,
    amount: int,
    description: str,
) -> DBResult[None]:
    def operation(conn: sqlite3.Connection) -> DBResult[None]:
        return (
            _update_player_balance(conn, player_id, amount)
            .bind(lambda _: _update_fund_balance(conn, amount))
            .bind(
                lambda _: _insert_transaction(
                    conn,
                    player_id,
                    amount,
                    description,
                )
            )
        )

    return run_transaction(db, operation)
```

Если любой шаг возвращает `Failure`, последующие `bind` не выполняются, а `run_transaction` делает rollback.

Если SQLite выбрасывает исключение на любом шаге, оно поднимается до единственного `try/except` в `run_transaction`, преобразуется в типизированную ошибку, а вся транзакция откатывается.

## Декоратор можно оставить

Декоратор может быть тонким синтаксическим сахаром над правильным runner:

```py
def transactional[T, **P](
    operation: Callable[Concatenate[DB, P], DBResult[T]],
) -> Callable[Concatenate[DB, P], DBResult[T]]:
    @wraps(operation)
    def wrapper(db: DB, *args: P.args, **kwargs: P.kwargs) -> DBResult[T]:
        return run_transaction(
            db,
            lambda _: operation(db, *args, **kwargs),
        )

    return wrapper
```

При таком варианте декорированная функция не должна вызывать другие декорированные write-функции. Она вызывает только приватные raw-шаги.

## Кто именно обрабатывает исключения

Приватные SQL-шаги намеренно не содержат `try/except`. Если `conn.execute()` выбрасывает `sqlite3.Error`, исключение проходит через всю цепочку `.bind(...)`, выходит из декорируемой функции и попадает в `try/except` внутри `run_transaction()`.

Полная цепочка вызова:

```text
handler
  -> update_player_and_fund_balance_atomic(db, ...)
    -> wrapper из @transactional
      -> run_transaction(...)
        -> исходная update_player_and_fund_balance_atomic(...)
          -> _update_player_balance(...)
          -> _update_fund_balance(...)
          -> _insert_transaction(...)
             ! sqlite3.IntegrityError
        <- except sqlite3.Error в run_transaction
        <- ROLLBACK
        <- Failure(ConstraintViolation(...))
  <- handler разбирает Result
```

Декорируемая версия составной операции:

```py
@transactional
def update_player_and_fund_balance_atomic(
    db: DB,
    player_id: int,
    amount: int,
    description: str,
) -> DBResult[None]:
    conn = db.conn
    return (
        _update_player_balance(conn, player_id, amount)
        .bind(lambda _: _update_fund_balance(conn, amount))
        .bind(
            lambda _: _insert_transaction(
                conn,
                player_id,
                amount,
                description,
            )
        )
    )
```

Обычный вызов функции ничего не знает о `BEGIN`/`COMMIT`/`ROLLBACK`:

```py
result = update_player_and_fund_balance_atomic(
    db,
    player_id=123,
    amount=500,
    description="Пополнение",
)

match result:
    case Success(None):
        LOGGER.info("Оплата сохранена")
    case Failure(NotFound(entity="player", key=player_id)):
        LOGGER.warning("Игрок %s не найден", player_id)
    case Failure(ConstraintViolation() as error):
        LOGGER.error("Нарушено ограничение БД: %s", error)
    case Failure(StorageFailure() as error):
        LOGGER.error("Ошибка SQLite: %s", error)
    case Failure(InvariantViolation() as error):
        LOGGER.critical("Нарушен invariant: %s", error)
```

В async handler транзакционная функция вызывается синхронно, а `await` выполняется уже после завершения транзакции:

```py
async def pay_handler(message: Message, db: DB) -> None:
    result = update_player_and_fund_balance_atomic(
        db,
        player_id=message.from_user.id,
        amount=500,
        description="Пополнение",
    )

    match result:
        case Success(None):
            text = "✅ Оплата сохранена"
        case Failure(NotFound()):
            text = "❌ Игрок не найден"
        case Failure(error):
            LOGGER.error("Не удалось сохранить оплату: %s", error)
            text = "❌ Не удалось сохранить оплату"

    # К этому моменту COMMIT или ROLLBACK уже выполнен.
    await message.answer(text)
```

Здесь есть три разных пути ошибок:

1. Приватный шаг сам возвращает доменный `Failure`, например `NotFound`. Исключения нет; `run_transaction` видит `Failure` и выполняет rollback.
2. SQLite выбрасывает `sqlite3.Error`. Исключение ловит `run_transaction`, выполняет rollback и возвращает типизированный `Failure`.
3. Возникает неожиданный programmer bug, например `AttributeError`. `run_transaction` выполняет rollback и повторно выбрасывает исключение, чтобы баг не превратился в обычную ошибку БД.

## Когда нужен `SAVEPOINT`

`SAVEPOINT` — вложенная точка отката внутри уже открытой транзакции.

```sql
SAVEPOINT sp_1;
-- несколько запросов
ROLLBACK TO SAVEPOINT sp_1;
RELEASE SAVEPOINT sp_1;
```

или при успехе:

```sql
RELEASE SAVEPOINT sp_1;
```

Важно:

- `ROLLBACK TO SAVEPOINT` отменяет изменения после savepoint, но оставляет savepoint активным;
- после него нужно выполнить `RELEASE SAVEPOINT`;
- успешный `RELEASE` вложенного savepoint не фиксирует данные окончательно;
- внешний `ROLLBACK` всё ещё отменит эти изменения.

`SAVEPOINT` нужен, если действительно хочется свободно составлять транзакционные функции друг из друга.

### Схема nested runner

```py
# Псевдокод основных веток.
root = db.transaction_depth == 0

if root:
    conn.execute("BEGIN IMMEDIATE")
else:
    savepoint = f"sp_{next(db.savepoint_ids)}"
    conn.execute(f"SAVEPOINT {savepoint}")

result = operation(conn)

match result:
    case Success(_):
        if root:
            conn.execute("COMMIT")
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    case Failure(_):
        if root:
            conn.execute("ROLLBACK")
        else:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
```

Savepoint names должны генерироваться только приложением из integer counter. Нельзя подставлять в SQL имя из пользовательского ввода.

### Что выбрать для VolleyBot

Начать лучше с простого варианта:

- одна транзакция на публичную составную операцию;
- приватные `_step`-функции без commit/rollback;
- вложенность запрещена.

Это проще тестировать и сложнее использовать неправильно.

`SAVEPOINT` стоит добавлять только когда появится реальная потребность переиспользовать публичные транзакционные операции внутри других транзакций.

## Read-транзакции

Один `SELECT` не требует отдельного `BEGIN`.

Несколько SELECT, которые должны видеть один snapshot, можно обернуть в `BEGIN DEFERRED`. Например, чтение шаблонов и подписок двумя запросами логически должно быть консистентным.

Для read-функций нужны два разных механизма:

- `run_query` — только преобразует ожидаемые SQLite-ошибки в `DBError`, без commit/rollback;
- `run_read_transaction` — открывает `BEGIN DEFERRED`, если нужен единый snapshot нескольких запросов.

---

# 3. Одно долгоживущее соединение и asyncio

## Нормально ли держать одно соединение открытым

Да. Для SQLite одно долгоживущее соединение — нормальная практика.

Это не сетевое подключение к отдельному серверу. Оно расходует файловый дескриптор, память на connection state и cache prepared statements, но для VolleyBot эти расходы незначительны.

Само открытое соединение обычно не держит БД заблокированной. Проблема возникает, если долго держать **открытую транзакцию** или незавершённый cursor.

Плюсы одного соединения в текущем проекте:

- единообразные PRAGMA;
- меньше lifecycle-кода;
- естественная сериализация коротких sync DB-операций на event loop thread;
- in-memory тесты работают предсказуемо;
- нет необходимости валидировать схему перед каждым запросом.

## Что делает `check_same_thread=True`

При создании `sqlite3.Connection` Python запоминает ID текущего OS thread.

Любой вызов connection/cursor из другого thread вызывает `sqlite3.ProgrammingError`.

Это защитный механизм. Он предотвращает конкурентное использование одного connection и общей transaction state несколькими потоками.

`check_same_thread=False` только отключает проверку. Он не добавляет lock, не делает транзакции безопасными и не определяет порядок операций.

## Как APScheduler запускает jobs

`AsyncIOScheduler` различает два вида функций.

### `async def job`

Распознаётся как coroutine function и запускается на asyncio event loop thread.

### Обычный `def job`

Запускается через executor, обычно `ThreadPoolExecutor`, чтобы не блокировать event loop.

Текущий `clear_job` — обычная функция. После передачи в неё singleton `DB` произойдёт следующее:

1. connection создан на основном event loop thread;
2. APScheduler запускает `clear_job` в worker thread;
3. `db.conn.execute(...)` вызывается в другом thread;
4. SQLite выбрасывает `ProgrammingError`;
5. попытка декоратора вызвать `rollback()` из того же неправильного thread тоже может выбросить `ProgrammingError`.

## Правильный DB job

```py
async def clear_job() -> None:
    match clear_paid_poll_subscriptions(db):
        case Success(None):
            return
        case Failure(error):
            LOGGER.error("Не удалось очистить подписки: %s", error)
```

Внутри такой функции DB-операция выполняется синхронно на event loop thread.

Для маленькой локальной SQLite-БД это приемлемо. Если запросы станут долгими, нужна уже другая архитектура: отдельный DB worker/queue или async DB layer. Просто переносить singleton connection в thread нельзя.

## Правила для одного connection

- Все DB-вызовы выполняются на event loop thread.
- Не использовать `asyncio.to_thread()` для функций, принимающих этот `DB`.
- Не использовать обычные APScheduler jobs для DB-операций.
- Не передавать connection в сторонние thread callbacks.
- Не выполнять `await` внутри открытой транзакции.
- Все транзакции должны быть короткими.
- File-only cleanup старых backup-файлов можно оставить sync job в executor, если он не использует `db.conn`.
- `Connection.backup()` для singleton connection следует запускать на thread-владельце connection.

## Почему нельзя делать `await` внутри транзакции

Пока coroutine ожидает сеть или timer, event loop может запустить другой handler. Если он использует то же соединение, его запросы попадут в открытую чужую транзакцию.

Поэтому правильная форма:

```py
# Сначала сеть.
telegram_data = await load_something()

# Затем короткая полностью синхронная DB-транзакция.
result = save_something_atomic(db, telegram_data)

# После завершения транзакции снова можно await.
await report_result(result)
```

---

# 4. Отсутствие значения без `None`

## Нормально ли помещать Optional внутрь Result

Да. `Result[Optional[T], E]` — нормальная и распространённая модель:

- `Success(value)` — найдено;
- `Success(None)` — корректно не найдено;
- `Failure(error)` — запрос не удалось выполнить.

Это аналог Rust-типа `Result<Option<T>, E>`.

Но если `None` нежелателен, есть более явные варианты.

## Вариант 1: `returns.maybe.Maybe`

```py
from returns.maybe import Maybe, Nothing, Some
from returns.result import Failure, Result, Success


def find_open_monthly_game(
    db: DB,
) -> Result[Maybe[GameInfo], DBError]:
    row = db.conn.execute(...).fetchone()
    if row is None:
        return Success(Nothing)
    return Success(Some(game_info_from_row(row)))
```

Разбор:

```py
match find_open_monthly_game(db):
    case Success(Some(game)):
        close_game(game)
    case Success(Nothing):
        open_new_game()
    case Failure(error):
        report_database_error(error)
```

Плюс: невозможно перепутать отсутствие с ошибкой.

Минус: два вложенных контейнера увеличивают синтаксический шум.

## Вариант 2: собственный `Found/Missing` ADT

```py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Found[T]:
    value: T


@dataclass(frozen=True, slots=True)
class Missing:
    pass


type Lookup[T] = Found[T] | Missing
```

Контракт:

```py
Result[Lookup[GameInfo], DBError]
```

Это полезно, если `Missing` в будущем должен нести доменный контекст.

## Вариант 3: отсутствие как типизированная ошибка

```py
Result[GameInfo, NotFound | DBError]
```

Подходит, когда функция обещает существующую сущность и отсутствие означает невозможность выполнить команду.

Не подходит для вопроса «есть ли сейчас открытая игра?», где отсутствие — ожидаемый положительный результат проверки.

## Рекомендуемое naming rule

- `find_*` — отсутствие нормально, возвращает `Result[Maybe[T], DBError]`;
- `get_*` или `require_*` — сущность обязана быть, отсутствие возвращает `Failure(NotFound(...))`;
- `exists_*`/`has_*` — если нужен только факт, возвращает `Result[bool, DBError]`.

Примеры:

```py
find_open_monthly_game() -> Result[Maybe[GameInfo], DBError]
require_game(poll_id) -> Result[GameInfo, NotFound | DBError]
has_open_game(template_id) -> Result[bool, DBError]
```

## Касса — не optional

Для `fund_balance` отсутствие является нарушением deploy invariant. Поэтому `Maybe` там не нужен:

```py
get_fund_balance() -> Result[int, InvariantViolation | DBError]
```

---

# 5. Enum и алгебраические типы ошибок в Python

## Простой `StrEnum`

Для небольшого закрытого набора вариантов:

```py
from enum import StrEnum


class AddSubscriptionError(StrEnum):
    DUPLICATE = "duplicate"
    MISSING_HALL = "missing_hall"
    MISSING_PLAYER = "missing_player"
```

Использование:

```py
def add_poll_subscription(...) -> Result[None, AddSubscriptionError | DBError]:
    ...
```

Разбор:

```py
match add_poll_subscription(db, hall_id, player_id):
    case Success(None):
        ...
    case Failure(AddSubscriptionError.DUPLICATE):
        ...
    case Failure(AddSubscriptionError.MISSING_HALL):
        ...
    case Failure(AddSubscriptionError.MISSING_PLAYER):
        ...
    case Failure(db_error):
        ...
```

Преимущества перед строками:

- typo обнаруживается type checker;
- IDE предлагает варианты;
- проще искать usages;
- значение остаётся стабильной строкой для логов/serialization.

## Когда enum недостаточно

Enum не умеет удобно хранить данные конкретной ошибки:

- какой игрок не найден;
- какая операция выполнялась;
- какой SQLite code получен;
- какое значение повреждено.

Для таких случаев лучше frozen dataclass и union:

```py
@dataclass(frozen=True, slots=True)
class PlayerNotFound:
    player_id: int


@dataclass(frozen=True, slots=True)
class HallAlreadyPaid:
    poll_template_id: int
    month: str


@dataclass(frozen=True, slots=True)
class CorruptData:
    field: str
    detail: str


type HallPaymentError = (
    PlayerNotFound
    | HallAlreadyPaid
    | CorruptData
    | ConstraintViolation
    | StorageFailure
)
```

Pattern matching:

```py
match result:
    case Failure(PlayerNotFound(player_id)):
        ...
    case Failure(HallAlreadyPaid(poll_template_id, month)):
        ...
    case Failure(StorageFailure() as error):
        LOGGER.error("Storage error: %s", error)
```

## Практическая рекомендация

- Enum использовать для простых доменных статусов без payload.
- Dataclass union использовать для ошибок с контекстом.
- Не показывать пользователю raw SQLite message.
- В логах сохранять operation, SQLite code/name и техническую detail.

---

# 6. `get_player_balance()` возвращает `int`

Это изменение принято осознанно и само по себе не является ошибкой.

Новый контракт:

```py
get_player_balance(db, player_id) -> Result[int, NotFound | DBError]
```

Правило перевода call sites:

- если нужен только баланс — использовать `get_player_balance()`;
- если нужны `name`, `fullname`, `username`, ссылка или флаги — использовать `get_player_info()`;
- не выполнять два запроса, если `get_player_info()` уже содержит balance.

Для ясности можно переименовать функцию в `get_player_balance_amount()`, но это необязательно.

---

# 7. Рекомендуемый lifecycle одного `DB`

## Владение ресурсом

Экземпляр `DB` должен создаваться ровно в одном месте — composition root (`run_polling`/`run_webhook` или общий app factory).

Только composition root читает settings/env и разрешает путь:

```py
db_path = settings.volleybot_db_path.resolve()
db = DB.open_existing(db_path)
```

Сам `DB` хранит immutable `Path`:

```py
class DB:
    def __init__(self, path: Path, conn: sqlite3.Connection):
        self.path = path
        self.conn = conn
```

Внутренние функции не читают `VOLLEYBOT_DB_PATH` повторно.

## Startup

Рекомендуемый порядок:

1. Загрузить и проверить settings.
2. Разрешить единственный абсолютный путь к БД.
3. Убедиться, что файл существует.
4. Открыть connection.
5. Установить `row_factory`, `foreign_keys`, timeout и другие runtime PRAGMA.
6. Выполнить дешёвый startup guard.
7. При необходимости создать startup backup до первых бизнес-записей.
8. Создать сервисы, передав им `DB`.
9. Восстановить состояние сервисов и обработать каждый `Result`.
10. Зарегистрировать handlers/jobs.
11. Запустить scheduler.
12. Начать polling/webhook.

Если любой этап после открытия connection падает, connection должен закрываться в `finally`.

## Dependency injection

```py
db = DB.open_existing(db_path)

bot_state_service = BotStateService(db, default_chat_id=CHAT_ID)
poll_service = PollService(db)

register_handlers(dp, bot, db)
setup_scheduler(scheduler, bot, bot_state_service, poll_service, db)
```

Альтернатива — handlers работают только через сервисы и напрямую `DB` не получают. Это архитектурно чище, но потребует больше рефакторинга.

## Shutdown

Рекомендуемый порядок:

1. Прекратить принимать новые updates или начать shutdown dispatcher.
2. Остановить scheduler и дождаться активных jobs.
3. Отменить/дождаться background tasks `PollService`.
4. Сохранить `PollService` state и проверить `Result`.
5. Сохранить `BotStateService` state и проверить `Result`.
6. Убедиться, что нет открытой транзакции.
7. Сделать shutdown backup — тогда он содержит финальное сохранённое состояние.
8. Закрыть SQLite connection.
9. Закрыть Telegram HTTP session и остальные ресурсы.

Даже если persistence или backup завершились ошибкой, `DB.close()` должен выполниться в `finally`.

Нельзя закрывать DB до scheduler/background tasks: они могут ещё попытаться выполнить запрос.

## Backup API

Рекомендуемая форма:

```py
db.backup(reason: BackupReason) -> Result[Path, BackupError]
```

Backup должен использовать уже разрешённый `db.path` или `db.conn`, а не повторно читать env.

`Connection.backup(destination)` даёт согласованный SQLite snapshot и безопаснее простого `shutil.copy` активного файла.

Очистка старых backup-файлов не обязана использовать DB connection. Ей достаточно заранее вычисленного immutable `backup_dir`.

---

# 8. Типизированная классификация SQLite errors

## Проблема

`sqlite3.IntegrityError` означает не только duplicate:

- UNIQUE;
- PRIMARY KEY;
- FOREIGN KEY;
- CHECK;
- NOT NULL.

Текущий общий текст «ошибка уникальности» и локальный возврат `"duplicate"` для любого IntegrityError дают пользователю неверную причину.

## Доступные коды Python 3.14

Фактически проверены в текущем окружении:

| Нарушение | `sqlite_errorcode` | `sqlite_errorname` |
|---|---:|---|
| UNIQUE | 2067 | `SQLITE_CONSTRAINT_UNIQUE` |
| PRIMARY KEY | 1555 | `SQLITE_CONSTRAINT_PRIMARYKEY` |
| FOREIGN KEY | 787 | `SQLITE_CONSTRAINT_FOREIGNKEY` |
| CHECK | 275 | `SQLITE_CONSTRAINT_CHECK` |
| NOT NULL | 1299 | `SQLITE_CONSTRAINT_NOTNULL` |

## Mapper

```py
def map_sqlite_error(
    error: sqlite3.Error,
    *,
    operation: str,
) -> DBError:
    code = getattr(error, "sqlite_errorcode", None)
    name = getattr(error, "sqlite_errorname", None)
    detail = str(error)

    if isinstance(error, sqlite3.IntegrityError):
        match code:
            case sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                return ConstraintViolation(
                    ConstraintKind.UNIQUE,
                    operation,
                    detail,
                )
            case sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY:
                return ConstraintViolation(
                    ConstraintKind.PRIMARY_KEY,
                    operation,
                    detail,
                )
            case sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY:
                return ConstraintViolation(
                    ConstraintKind.FOREIGN_KEY,
                    operation,
                    detail,
                )
            case sqlite3.SQLITE_CONSTRAINT_CHECK:
                return ConstraintViolation(
                    ConstraintKind.CHECK,
                    operation,
                    detail,
                )
            case sqlite3.SQLITE_CONSTRAINT_NOTNULL:
                return ConstraintViolation(
                    ConstraintKind.NOT_NULL,
                    operation,
                    detail,
                )

    return StorageFailure(
        operation=operation,
        sqlite_code=code,
        sqlite_name=name,
        detail=detail,
    )
```

## Generic error и domain error — не одно и то же

Generic mapper знает, что нарушен FOREIGN KEY, но обычно не знает бизнес-смысл: отсутствует игрок, зал или игра.

Если UI должен показать точный вариант, repository-функция может заранее проверить обязательные сущности и вернуть доменный enum/dataclass:

```py
PlayerNotFound(player_id)
PollTemplateNotFound(poll_template_id)
HallAlreadyPaid(poll_template_id, month)
```

UNIQUE constraint всё равно нужно оставить как защиту от race. Если он сработал, generic `ConstraintViolation(UNIQUE, ...)` можно преобразовать в `HallAlreadyPaid` на границе конкретной операции.

Для `record_hall_payment_atomic` нельзя считать любой IntegrityError duplicate. Только UNIQUE/PRIMARY KEY может означать повторную оплату; FOREIGN KEY/CHECK/NOT NULL должны возвращать другие ошибки.

---

# 9. Как минимизировать `try/except`, не скрывая ошибки

## Полностью избавиться от `try/except` нельзя

SQLite, JSON decoder и некоторые стандартные Python API сообщают об ошибках через исключения. Если внешний API exception-based, в одном месте нужен adapter из исключения в `Result`.

Цель — не «ноль try/except», а «один try/except на архитектурной границе вместо try/except в каждой функции».

## Три категории ошибок

### 1. Ожидаемые доменные состояния

Возвращаются напрямую через `Result`:

```py
Failure(PlayerNotFound(player_id))
Failure(AddSubscriptionError.DUPLICATE)
```

Для них исключения не нужны.

### 2. Ожидаемые boundary failures

- `sqlite3.Error`;
- `json.JSONDecodeError`;
- конкретный `ValueError` при разборе сохранённого числа/даты.

Они ловятся один раз в transaction/query/decoder adapter и преобразуются в typed error.

### 3. Programmer bugs и системные события

- `AttributeError` из-за неверного объекта;
- неожиданный `KeyError`;
- assertion failure;
- `KeyboardInterrupt`;
- cancellation;
- ошибки в собственной логике.

Их нельзя превращать в обычный `Failure("что-то пошло не так")`. Нужно откатить принадлежащую текущей функции транзакцию и повторно выбросить исключение.

## Почему `except Exception: return Failure(...)` плох

Он:

- скрывает программные дефекты;
- позволяет приложению продолжить работу после нарушения внутренних invariants;
- смешивает recoverable DB error и bug;
- теряет полезный traceback, если сохранить только строку;
- делает мониторинг ложноположительно «успешным»;
- может поймать ошибки, которые текущий уровень не умеет корректно обработать, например `MemoryError`.

Сам по себе широкий catch допустим для cleanup, если после cleanup исключение повторно выбрасывается:

```py
except BaseException:
    rollback_owned_transaction()
    raise
```

Плохо не наличие catch, а проглатывание или неверная классификация исключения.

## Централизованный query adapter

```py
def run_query[T](
    db: DB,
    *,
    operation: str,
    query: Callable[[sqlite3.Connection], T],
) -> DBResult[T]:
    try:
        return Success(query(db.conn))
    except sqlite3.Error as error:
        return Failure(map_sqlite_error(error, operation=operation))
```

Repository-функция остаётся короткой:

```py
def get_all_players(db: DB) -> DBResult[list[Player]]:
    return run_query(
        db,
        operation="get_all_players",
        query=lambda conn: [
            player_from_row(row)
            for row in conn.execute(PLAYER_SELECT).fetchall()
        ],
    )
```

## Конвертеры данных

Для чистых преобразований можно использовать узкий `@safe`:

```py
@safe(exceptions=(json.JSONDecodeError, TypeError))
def decode_votes(payload: str) -> JsonValue:
    return json.loads(payload)
```

После чего поменять exception на typed error через `.alt(...)`.

Не следует помещать все row adapters под один широкий catch. Каждый adapter должен ловить только те ошибки формата, которые действительно означают повреждённые данные.

---

# 10. Строгая загрузка monthly votes

Решение «одна повреждённая строка делает ошибкой весь результат» допустимо и хорошо сочетается с `Result`.

Это поведение нужно зафиксировать тестами.

## Обязательные тесты

- [ ] Все строки корректны — возвращается `Success[dict[int, list[int]]]`.
- [ ] Одна строка содержит malformed JSON — возвращается `Failure(CorruptData(...))`.
- [ ] Одна строка содержит scalar вместо list — весь результат `Failure`.
- [ ] Один option ID является строкой — весь результат `Failure`.
- [ ] Один option ID является float — весь результат `Failure`.
- [ ] Один option ID является `null` — весь результат `Failure`.
- [ ] Один option ID является JSON boolean — весь результат `Failure`.
- [ ] При одной плохой строке корректные строки не возвращаются частично.
- [ ] Пустой набор строк возвращает `Success({})`.

## Отдельная текущая ошибка: `bool` считается `int`

Сейчас используется:

```py
isinstance(option_id, int)
```

В Python:

```py
isinstance(True, int) is True
```

Поэтому JSON `[true]` ошибочно проходит валидацию как список integer IDs.

Для строгой проверки нужно:

```py
type(option_id) is int
```

При необходимости также проверить:

```py
option_id >= 0
```

Проверка верхней границы требует знания количества options конкретной игры и может выполняться на service/domain уровне.

---

# Дополнительные конкретные ошибки текущего `db2.py`

## Исправить обязательно

### Read-функции используют write-декоратор

`get_*`, `find_*`, `load_*` и `count_*` не должны безусловно выполнять commit/rollback всего connection.

### `set_player_guest` содержит неформатированную строку

Сейчас:

```py
return Failure("❌ Ошибка при изменении гостевого статуса игрока {user_id}")
```

Нужен `f` либо, предпочтительно, typed `PlayerNotFound(user_id)`.

### `record_hall_payment_atomic` неправильно классифицирует IntegrityError

Только UNIQUE означает уже выполненную оплату. FOREIGN KEY, CHECK и NOT NULL — другие ошибки.

### `save_poll_template` считает любой IntegrityError конфликтом имени

CHECK для дня/часа/стоимости и NOT NULL также попадут в сообщение «Имя должно быть уникальным».

### Row conversion может нарушить контракт Result

`int(...)`, parsing дат и `.unwrap()` способны выбросить `ValueError`/`UnwrapFailedError` наружу. Ожидаемые ошибки формата должны становиться `CorruptData`.

### Constructor должен закрывать connection при ошибке startup

Если открытие/проверка после `sqlite3.connect()` падает, connection необходимо закрыть до повторного выброса ошибки.

## Осознанные изменения, которые не считать ошибками

- `get_player_balance()` возвращает `int`.
- Строгая all-or-nothing загрузка monthly votes.
- Отказ от runtime-миграций.
- Одно долгоживущее соединение при соблюдении same-thread правил.

## Отложенные, но обязательные задачи

### Result call sites

- убрать проверки `if result`, `if not result`, `result is None`;
- не сравнивать `Result` со строкой;
- отдельно обрабатывать `Success(False)`;
- не игнорировать Failure write-операций.

### Backup API

- сохранить immutable DB path;
- использовать SQLite backup API;
- startup/shutdown backup должен быть частью lifecycle;
- cleanup файлов не должен использовать connection.

### Integration tests

Сейчас часть тестов всё ещё использует `src.db` и raw return values. Зелёный suite не подтверждает production-интеграцию `db2.py`.

Нужно перевести:

- `tests/test_db_messages.py`;
- `tests/test_backups.py` после переноса backup API;
- handler mocks с `True`/`False`/`None`/raw dict;
- scheduler tests;
- PollService tests с raw DB return values.

---

# Рекомендуемая структура модулей

Один из возможных вариантов:

```text
src/
  db2.py              # Runtime repository/query functions
  db_errors.py        # Typed DB/domain errors и SQLite mapper
  db_schema.py        # Canonical DDL, create_database, validation rules
  db_admin.py         # CLI: create / prepare / validate
  db_backup.py        # Backup и retention
```

Если это слишком много файлов для масштаба проекта, `db_errors.py` и `db_schema.py` можно оставить внутри `db2.py`, но runtime API и admin/deploy API всё равно стоит концептуально разделить.

---

# Рекомендуемый порядок реализации

1. Определить typed errors и правила `find/get/require`.
2. Разделить schema create/validate и runtime open.
3. Добавить `db_admin validate` с ненулевым exit code при ошибке.
4. Сделать `fund_balance` обязательным deploy invariant.
5. Перевести connection на явное transaction control.
6. Реализовать простой `run_transaction` без вложенности.
7. Разбить atomic-функции на transaction owner и приватные raw-шаги.
8. Разделить `run_query`, `run_read_transaction` и `run_transaction`.
9. Сделать DB jobs асинхронными и запретить thread executor для connection.
10. Добавить transaction/schema/thread/monthly-votes тесты.
11. Ввести один `DB` в composition root и передать сервисам.
12. Переводить production call sites вертикальными срезами.
13. Перевести integration mocks на typed `Success`/`Failure`.
14. Перенести backup API.
15. Удалить `db.py` только после grep по `src/` и `tests/` и полного end-to-end запуска.
