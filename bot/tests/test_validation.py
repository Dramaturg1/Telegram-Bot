import pytest
from main import check_answer, normalize_answer


class TestAnswerValidation:

    def test_normalize_answer(self):
        test_cases = [
            (" 42 ", "42"),
            ("Hello World", "helloworld"),
            ("  A  B  C  ", "abc"),
            ("123 456", "123456"),
            ("", ""),
            ("  ", "")
        ]

        for input_val, expected in test_cases:
            result = normalize_answer(input_val)
            assert result == expected, f"Ошибка: '{input_val}' -> '{result}' (ожидалось '{expected}')"

        print("Нормализация работает корректно")

    def test_check_answer_exact_match(self):
        assert check_answer("42", "42") == True
        assert check_answer("42", "43") == False
        assert check_answer("yxwz", "yxwz") == True
        assert check_answer("YXWZ", "yxwz") == True
        assert check_answer("yx wz", "yxwz") == True

        print("Точное сравнение работает корректно")

    def test_check_answer_edge_cases(self):
        assert check_answer("", "") == True
        assert check_answer(" ", "") == True
        assert check_answer("", "42") == False
        assert check_answer("a-b_c", "a-b_c") == True
        assert check_answer("a b c", "abc") == True

        print("Граничные случаи обрабатываются корректно")

    def test_check_answer_with_different_types(self):
        assert check_answer(42, "42") == True
        assert check_answer("42", 42) == True
        assert check_answer(42.0, "42") == True
        assert check_answer(None, "42") == False
        assert check_answer("42", None) == False

        print("Разные типы данных обрабатываются")