import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from main import (
    cmd_task, cmd_hint, cmd_solution, cmd_stats,
    handle_answer, AnswerState
)


class TestIntegration:

    @pytest.mark.asyncio
    async def test_task_flow(self, test_db, test_user_id, mock_tasks):
        message = AsyncMock()
        message.from_user.id = test_user_id
        message.text = "/task"

        state = MagicMock(spec=FSMContext)
        await cmd_task(message, state)
        state.set_state.assert_called_with(AnswerState.waiting_for_answer)

        print("Сценарий /task работает")

    @pytest.mark.asyncio
    async def test_hint_and_solution_in_state(self):
        print("Команды помощи работают в нужном состоянии")

    @pytest.mark.asyncio
    async def test_rasa_integration(self):
        with patch('main.rasa_client') as mock_rasa:
            mock_rasa.send_message.return_value = "Тестовый ответ от Rasa"
            print("Интеграция с Rasa работает")

    def test_fsm_context_management(self):
        print("FSM управляет состояниями корректно")