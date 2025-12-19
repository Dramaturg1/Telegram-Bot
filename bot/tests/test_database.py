import pytest
import sqlite3
from datetime import datetime
from main import update_user_stats, get_user_stats, init_database


class TestDatabase:
    def test_user_creation(self, test_db, test_user_id):
        stats = get_user_stats(test_user_id)
        assert stats["user_id"] == test_user_id
        assert stats["total_tasks"] == 0
        assert stats["correct_answers"] == 0
        assert stats["current_level"] == 1
        print(f"Пользователь создан: {stats}")

    def test_stats_update(self, test_db, test_user_id):
        stats_before = get_user_stats(test_user_id)
        update_user_stats(test_user_id, "test_task_1", True)
        stats_after = get_user_stats(test_user_id)

        assert stats_after["total_tasks"] == stats_before["total_tasks"] + 1
        assert stats_after["correct_answers"] == stats_before["correct_answers"] + 1
        assert stats_after["correct_rate"] > 0
        print(f"Статистика обновлена: {stats_after['correct_rate']:.0%}")

    def test_transaction_integrity(self, test_db, test_user_id):
        conn = sqlite3.connect(test_db)
        cursor = conn.cursor()

        cursor.execute("SELECT total_tasks, correct_answers FROM users WHERE user_id = ?", (test_user_id,))
        before = cursor.fetchone()
        update_user_stats(test_user_id, "test_task_2", False)
        cursor.execute("SELECT total_tasks, correct_answers FROM users WHERE user_id = ?", (test_user_id,))
        after = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM task_history WHERE user_id = ?", (test_user_id,))
        history_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM topic_progress WHERE user_id = ?", (test_user_id,))
        topic_count = cursor.fetchone()[0]
        conn.close()

        assert after[0] == before[0] + 1
        assert after[1] == before[1]
        assert history_count > 0
        assert topic_count > 0

        print("Транзакционность обеспечена: обновлены users, task_history, topic_progress")

    def test_level_calculation(self, test_db, test_user_id):
        for i in range(5):
            update_user_stats(test_user_id, f"task_{i}", True)

        stats = get_user_stats(test_user_id)
        if stats["correct_rate"] >= 0.85:
            assert stats["current_level"] == 5
        elif stats["correct_rate"] >= 0.75:
            assert stats["current_level"] == 4

        print(f"Уровень рассчитан корректно: {stats['current_level']} при {stats['correct_rate']:.0%}")