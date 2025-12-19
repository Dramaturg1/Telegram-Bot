import pytest
import time
from main import get_adaptive_task, check_answer, get_user_stats


class TestPerformance:

    def test_response_time_single_user(self, test_db, test_user_id, mock_tasks):
        start_time = time.time()
        for i in range(10):
            task = get_adaptive_task(test_user_id)
            stats = get_user_stats(test_user_id)

        end_time = time.time()
        avg_time = (end_time - start_time) / 10
        assert avg_time < 1.5, f"Среднее время {avg_time:.2f} сек превышает 1.5 сек"

        print(f"Время отклика: {avg_time:.3f} сек (требование: ≤1.5 сек)")

    def test_answer_check_performance(self):
        start_time = time.time()
        for i in range(1000):
            check_answer(f"answer_{i}", f"answer_{i}")

        end_time = time.time()
        total_time = end_time - start_time

        assert total_time < 1.0, f"Проверка 1000 ответов заняла {total_time:.2f} сек"

        print(f"Проверка ответов: {total_time:.3f} сек на 1000 операций")

    def test_concurrent_users_simulation(self, test_db, mock_tasks):
        user_times = []
        for user_id in range(1000, 1100):
            start_time = time.time()
            task = get_adaptive_task(user_id)
            end_time = time.time()
            user_times.append(end_time - start_time)
        avg_time = sum(user_times) / len(user_times)
        max_time = max(user_times)
        assert avg_time < 1.5, f"Среднее время {avg_time:.2f} сек"
        assert max_time < 3.0, f"Максимальное время {max_time:.2f} сек"

        print(f"Многопользовательский режим: avg={avg_time:.3f}, max={max_time:.3f} сек")