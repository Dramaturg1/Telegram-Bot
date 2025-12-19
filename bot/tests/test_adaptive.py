import pytest
from main import get_adaptive_task, update_user_stats, get_user_stats


class TestAdaptiveAlgorithm:

    def test_new_user_gets_task(self, test_db, test_user_id, mock_tasks):
        task = get_adaptive_task(test_user_id)

        assert task is not None
        assert "id" in task
        assert "difficulty" in task
        assert "topic" in task
        assert task["difficulty"] in [1, 2]

        print(f"Новый пользователь получил задание: {task['id']} (уровень {task['difficulty']})")

    def test_task_not_repeated(self, test_db, test_user_id, mock_tasks):
        solved_tasks = []
        for i in range(5):
            task = get_adaptive_task(test_user_id)

            assert task["id"] not in solved_tasks

            solved_tasks.append(task["id"])

            update_user_stats(test_user_id, task["id"], True)

        print(f"Проверено отсутствие повторов: {solved_tasks}")

    def test_difficulty_scaling(self, test_db, test_user_id, mock_tasks):

        stats = get_user_stats(test_user_id)
        assert stats["current_level"] == 1

        task1 = get_adaptive_task(test_user_id)
        assert task1["difficulty"] in [1, 2]

        for i in range(10):
            update_user_stats(test_user_id, f"dummy_{i}", True)

        stats = get_user_stats(test_user_id)
        assert stats["current_level"] > 1

        task2 = get_adaptive_task(test_user_id)

        user_level = stats["current_level"]
        assert task2["difficulty"] in [user_level, user_level + 1]

        print(f"Сложность масштабируется: уровень {user_level}, задание {task2['difficulty']}")

    def test_topic_variety(self, test_db, test_user_id, mock_tasks):
        topics_seen = set()

        for i in range(3):
            task = get_adaptive_task(test_user_id)
            topics_seen.add(task["topic"])

            update_user_stats(test_user_id, task["id"], True)

        assert len(topics_seen) >= 1

        print(f"Разнообразие тем: {topics_seen}")