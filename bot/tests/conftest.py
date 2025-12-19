import pytest
import sqlite3
import json
import os
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import (
    init_database, get_user_stats, update_user_stats,
    get_adaptive_task, check_answer, normalize_answer,
    load_tasks, DIFFICULTY_LEVELS
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_db():
    test_db_name = "test_users_stats.db"
    if os.path.exists(test_db_name):
        os.remove(test_db_name)

    from main import DB_NAME as original_db_name

    import main
    main.DB_NAME = test_db_name

    init_database()

    yield test_db_name

    main.DB_NAME = original_db_name

    if os.path.exists(test_db_name):
        os.remove(test_db_name)


@pytest.fixture
def test_user_id():
    return 999999


@pytest.fixture
def sample_tasks():
    return [
        {
            "id": "test_1",
            "topic": "Кодирование информации",
            "task_text": "Тестовое задание 1",
            "correct_answer": "42",
            "difficulty": 1,
            "solution": "Решение 1",
            "hint": "Подсказка 1"
        },
        {
            "id": "test_2",
            "topic": "Логические выражения",
            "task_text": "Тестовое задание 2",
            "correct_answer": "yxwz",
            "difficulty": 3,
            "solution": "Решение 2",
            "hint": "Подсказка 2"
        },
        {
            "id": "test_3",
            "topic": "Алгоритмы",
            "task_text": "Тестовое задание 3",
            "correct_answer": "1001",
            "difficulty": 4,
            "solution": "Решение 3",
            "hint": "Подсказка 3"
        }
    ]


@pytest.fixture
def mock_tasks(monkeypatch, sample_tasks):

    def mock_load_tasks():
        return sample_tasks

    monkeypatch.setattr("main.load_tasks", mock_load_tasks)
    return sample_tasks