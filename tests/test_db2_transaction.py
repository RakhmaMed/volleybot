"""Тесты сохранения состояния через функциональный интерфейс БД."""

from returns.result import Failure, Success

from src.db2 import DB, load_state, save_state


def test_save_state_stores_json_serialized_value(test_db: DB) -> None:
    value = {"players": [1, 2], "enabled": True}

    result = save_state(test_db, "bot_state", value)
    assert result == Success(None)
    result = load_state(test_db, "bot_state")
    assert result.unwrap() == value


def test_save_state_updates_existing_key(test_db: DB) -> None:
    assert save_state(test_db, "bot_state", {"version": 1}) == Success(None)

    result = save_state(test_db, "bot_state", {"version": 2})
    assert result == Success(None)
    result = load_state(test_db, "bot_state")
    assert result.unwrap() == {"version": 2}


def test_save_state_returns_failure_for_non_json_serializable_value(test_db: DB) -> None:
    result = save_state(test_db, "bot_state", {"invalid": object()})

    assert isinstance(result, Failure)
    assert "Не удалось сериализовать данные в JSON для ключа 'bot_state'" in result.failure()
    count = test_db.conn.execute("SELECT COUNT(*) FROM kv_store").fetchone()[0]
    assert count == 0

def test_load_state_returns_default_for_nonexistent_key(test_db: DB) -> None:
    result = load_state(test_db, "nonexistent_key")
    assert result.unwrap() is None

def test_load_state_returns_default_for_nonexistent_key_with_default(test_db: DB) -> None:
    result = load_state(test_db, "nonexistent_key", default=42)
    assert result.unwrap() == 42
