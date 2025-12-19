import asyncio
import json
import random
import os
import re
import sqlite3
import threading
import hashlib
import aiohttp
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

bot = Bot(token="")
dp = Dispatcher()

DB_NAME = "users_stats.db"


def init_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            total_tasks INTEGER DEFAULT 0,
            correct_answers INTEGER DEFAULT 0,
            current_level INTEGER DEFAULT 1,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS topic_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            topic TEXT,
            tasks_solved INTEGER DEFAULT 0,
            correct_rate REAL DEFAULT 0,
            last_solved TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
            UNIQUE(user_id, topic)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_id TEXT,
            task_topic TEXT,  -- ВАЖНО: эта колонка должна быть здесь!
            correct BOOLEAN,
            difficulty INTEGER,
            time_spent INTEGER DEFAULT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS materialized_views (
            view_name TEXT PRIMARY KEY,
            last_refresh TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            description TEXT,
            checksum TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_user_time 
        ON task_history(user_id, timestamp DESC)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_user_correct_time 
        ON task_history(user_id, correct, timestamp)
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_user_task_time 
        ON task_history(user_id, task_id, timestamp DESC)
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_correct_true 
        ON task_history(user_id, timestamp) 
        WHERE correct = 1
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_correct_false 
        ON task_history(user_id, timestamp, task_topic) 
        WHERE correct = 0
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_difficulty 
        ON task_history(user_id, difficulty) 
        WHERE difficulty BETWEEN 3 AND 5
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_task_history_time_spent 
        ON task_history(time_spent) 
        WHERE time_spent IS NOT NULL
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_users_last_activity 
        ON users(last_activity DESC)
    ''')

    cursor.execute('DROP VIEW IF EXISTS v_daily_stats')
    cursor.execute('''
        CREATE VIEW v_daily_stats AS
        SELECT 
            user_id,
            DATE(timestamp) as day,
            COUNT(*) as tasks_per_day,
            SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct_per_day,
            AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) as daily_accuracy,
            AVG(time_spent) as avg_time_spent
        FROM task_history
        WHERE timestamp IS NOT NULL
        GROUP BY user_id, DATE(timestamp)
        ORDER BY day DESC;
    ''')

    cursor.execute('DROP VIEW IF EXISTS v_user_topics')
    cursor.execute('''
        CREATE VIEW v_user_topics AS
        SELECT 
            user_id,
            task_topic as topic,
            COUNT(*) as total_tasks,
            SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct_tasks,
            AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) as success_rate,
            MIN(timestamp) as first_solved,
            MAX(timestamp) as last_solved
        FROM task_history
        WHERE task_topic IS NOT NULL
        GROUP BY user_id, task_topic;
    ''')

    conn.commit()
    conn.close()
    create_triggers()


def create_triggers():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DROP TRIGGER IF EXISTS update_user_level_after_insert")
    cursor.execute("DROP TRIGGER IF EXISTS update_topic_progress_after_insert")
    cursor.execute("DROP TRIGGER IF EXISTS update_user_activity")
    cursor.execute('''
        CREATE TRIGGER update_user_level_after_insert
        AFTER INSERT ON task_history
        FOR EACH ROW
        BEGIN
            UPDATE users 
            SET current_level = (
                SELECT 
                    CASE 
                        WHEN CAST(correct_answers AS REAL) / NULLIF(total_tasks, 0) >= 0.85 THEN 5
                        WHEN CAST(correct_answers AS REAL) / NULLIF(total_tasks, 0) >= 0.75 THEN 4
                        WHEN CAST(correct_answers AS REAL) / NULLIF(total_tasks, 0) >= 0.60 THEN 3
                        WHEN CAST(correct_answers AS REAL) / NULLIF(total_tasks, 0) >= 0.40 THEN 2
                        ELSE 1
                    END
                FROM users 
                WHERE user_id = NEW.user_id
            )
            WHERE user_id = NEW.user_id 
            AND (
                -- Обновляем уровень каждые 5 заданий
                (SELECT total_tasks FROM users WHERE user_id = NEW.user_id) % 5 = 0
                OR 
                -- Или если пользователь не был активен последний час
                (SELECT COUNT(*) FROM task_history 
                 WHERE user_id = NEW.user_id 
                 AND timestamp > datetime('now', '-1 hour')) = 0
            );
        END;
    ''')

    cursor.execute('''
        CREATE TRIGGER update_topic_progress_after_insert
        AFTER INSERT ON task_history
        FOR EACH ROW
        BEGIN
            INSERT OR REPLACE INTO topic_progress (user_id, topic, tasks_solved, correct_rate, last_solved)
            VALUES (
                NEW.user_id,
                NEW.task_topic,
                COALESCE((SELECT tasks_solved + 1 FROM topic_progress 
                         WHERE user_id = NEW.user_id AND topic = NEW.task_topic), 1),
                COALESCE((SELECT (correct_rate * tasks_solved + CASE WHEN NEW.correct THEN 1.0 ELSE 0.0 END) / (tasks_solved + 1)
                         FROM topic_progress 
                         WHERE user_id = NEW.user_id AND topic = NEW.task_topic),
                         CASE WHEN NEW.correct THEN 1.0 ELSE 0.0 END),
                NEW.timestamp
            );
        END;
    ''')

    cursor.execute('''
        CREATE TRIGGER update_user_activity
        AFTER INSERT ON task_history
        FOR EACH ROW
        BEGIN
            UPDATE users 
            SET last_activity = CURRENT_TIMESTAMP
            WHERE user_id = NEW.user_id;
        END;
    ''')

    conn.commit()
    conn.close()

def refresh_materialized_views(cursor=None):
    close_conn = False
    if cursor is None:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        close_conn = True

    try:
        cursor.execute('''
            SELECT 
                user_id,
                DATE(timestamp) as day,
                COUNT(*) as tasks_per_day,
                SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct_per_day,
                AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) as daily_accuracy
            FROM task_history
            WHERE timestamp >= date('now', '-30 days')
            GROUP BY user_id, DATE(timestamp)
        ''')

        daily_stats = []
        for row in cursor.fetchall():
            daily_stats.append({
                'user_id': row[0],
                'day': row[1],
                'tasks_per_day': row[2],
                'correct_per_day': row[3],
                'daily_accuracy': row[4]
            })

        cursor.execute('''
            INSERT OR REPLACE INTO materialized_views (view_name, data, last_refresh)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', ('user_stats_daily', json.dumps(daily_stats)))

        cursor.execute('''
            SELECT 
                u.user_id,
                u.username,
                u.total_tasks,
                u.correct_answers,
                u.current_level,
                COUNT(DISTINCT DATE(th.timestamp)) as active_days
            FROM users u
            LEFT JOIN task_history th ON u.user_id = th.user_id
            WHERE th.timestamp >= date('now', '-7 days')
            GROUP BY u.user_id
            ORDER BY total_tasks DESC
            LIMIT 50
        ''')

        top_users = []
        for row in cursor.fetchall():
            top_users.append({
                'user_id': row[0],
                'username': row[1],
                'total_tasks': row[2],
                'correct_answers': row[3],
                'current_level': row[4],
                'active_days': row[5]
            })

        cursor.execute('''
            INSERT OR REPLACE INTO materialized_views (view_name, data, last_refresh)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', ('top_users_weekly', json.dumps(top_users)))

        cursor.execute('''
            SELECT 
                task_topic,
                COUNT(*) as total_attempts,
                SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct_attempts,
                AVG(CASE WHEN correct THEN 1.0 ELSE 0.0 END) as global_success_rate,
                AVG(difficulty) as avg_difficulty
            FROM task_history
            WHERE timestamp >= date('now', '-90 days')
            GROUP BY task_topic
        ''')

        topic_stats = []
        for row in cursor.fetchall():
            topic_stats.append({
                'topic': row[0],
                'total_attempts': row[1],
                'correct_attempts': row[2],
                'global_success_rate': row[3],
                'avg_difficulty': row[4]
            })

        cursor.execute('''
            INSERT OR REPLACE INTO materialized_views (view_name, data, last_refresh)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', ('global_topic_stats', json.dumps(topic_stats)))

        if close_conn:
            conn.commit()
            conn.close()

        print("Материализованные представления обновлены")

    except Exception as e:
        print(f"Ошибка обновления материализованных ghtlcnfdktybq: {e}")
        if close_conn:
            conn.rollback()
            conn.close()


def get_materialized_view(view_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT data, last_refresh 
        FROM materialized_views 
        WHERE view_name = ?
    ''', (view_name,))

    row = cursor.fetchone()
    conn.close()

    if row:
        data_json, last_refresh = row
        refresh_time = datetime.fromisoformat(last_refresh)
        if (datetime.now() - refresh_time).seconds > 3600:
            threading.Thread(target=refresh_materialized_views, daemon=True).start()

        return json.loads(data_json)
    else:
        refresh_materialized_views()
        return get_materialized_view(view_name)  # Рекурсивный вызов


def update_user_stats(user_id, task_id, is_correct, time_spent=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    tasks = load_tasks()
    task = next((t for t in tasks if t['id'] == task_id), None)
    if not task:
        print(f"Задание {task_id} не найдено в базе")
        conn.close()
        return None
    task_topic = task['topic']
    difficulty = task.get('difficulty', 1)
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, current_level) 
            VALUES (?, 1)
        ''', (user_id,))
        cursor.execute('''
            INSERT INTO task_history 
            (user_id, task_id, task_topic, correct, difficulty, time_spent)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, task_id, task_topic, is_correct, difficulty, time_spent))
        if is_correct:
            cursor.execute('''
                UPDATE users 
                SET total_tasks = total_tasks + 1,
                    correct_answers = correct_answers + 1
                WHERE user_id = ?
            ''', (user_id,))
        else:
            cursor.execute('''
                UPDATE users 
                SET total_tasks = total_tasks + 1
                WHERE user_id = ?
            ''', (user_id,))

        conn.commit()

    except Exception as e:
        print(f"Ошибка обновления статистики: {e}")
        conn.rollback()
    finally:
        conn.close()
    try:
        refresh_materialized_views()
    except Exception as e:
        print(f"Ошибка обновления представлений: {e}")
    return get_user_stats(user_id)

def get_schema_version():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='schema_migrations'
    ''')
    if not cursor.fetchone():
        conn.close()
        return "0.0.0"
    cursor.execute('SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1')
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "0.0.0"

def apply_migration(version, sql_commands):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                description TEXT,
                checksum TEXT,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('SELECT version FROM schema_migrations WHERE version = ?', (version,))
        if cursor.fetchone():
            print(f"Миграция {version} уже применена")
            conn.close()
            return False
        for sql in sql_commands:
            cursor.execute(sql)
        checksum = hashlib.md5("\n".join(sql_commands).encode()).hexdigest()
        cursor.execute('''
            INSERT INTO schema_migrations (version, description, checksum)
            VALUES (?, ?, ?)
        ''', (version, f"Миграция к версии {version}", checksum))

        conn.commit()
        print(f"Миграция {version} успешно применена")
        return True

    except Exception as e:
        conn.rollback()
        print(f"Ошибка применения миграции {version}: {e}")
        return False
    finally:
        conn.close()

MIGRATIONS = {
    "1.1.0": [
        "ALTER TABLE users ADD COLUMN last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "CREATE INDEX idx_users_last_activity ON users(last_activity DESC)",
    ],
    "1.2.0": [
        "ALTER TABLE task_history ADD COLUMN time_spent INTEGER DEFAULT NULL",
        "CREATE INDEX idx_task_history_time_spent ON task_history(time_spent) WHERE time_spent IS NOT NULL",
    ],
}


def run_migrations():
    current_version = get_schema_version()
    print(f"Текущая версия схемы: {current_version}")
    for version, sql_commands in sorted(MIGRATIONS.items()):
        if version > current_version:
            print(f"Применяем миграцию {version}...")
            apply_migration(version, sql_commands)
init_database()
run_migrations()

class AnswerState(StatesGroup):
    waiting_for_answer = State()


class RasaClient:
    def __init__(self, url="http://localhost:5005"):
        self.url = f"{url}/webhooks/rest/webhook"

    async def send_message(self, user_id, text):
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "sender": str(user_id),
                    "message": text
                }
                async with session.post(self.url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data and len(data) > 0:
                            return data[0].get('text', '')
            return None
        except Exception as e:
            print(f"Ошибка подключения к Rasa: {e}")
            return None


rasa_client = RasaClient()

DIFFICULTY_LEVELS = {
    1: {"name": "Новичок", "correct_rate_threshold": 0.0, "max_tasks": 10},
    2: {"name": "Начинающий", "correct_rate_threshold": 0.4, "max_tasks": 20},
    3: {"name": "Средний", "correct_rate_threshold": 0.6, "max_tasks": 30},
    4: {"name": "Продвинутый", "correct_rate_threshold": 0.75, "max_tasks": 40},
    5: {"name": "Эксперт", "correct_rate_threshold": 0.85, "max_tasks": 50}
}

ADAPTIVE_WEIGHTS = {
    'level': 0.4,
    'topic': 0.3,
    'novelty': 0.2,
    'time': 0.1
}

w1, w2, w3, w4 = ADAPTIVE_WEIGHTS['level'], ADAPTIVE_WEIGHTS['topic'], ADAPTIVE_WEIGHTS['novelty'], ADAPTIVE_WEIGHTS['time']

user_last_tasks = {}


def load_tasks():
    try:
        possible_paths = [
            'data/database.json',
            'database.json',
            '../data/database.json'
        ]

        for path in possible_paths:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    tasks = json.load(f)
                    for task in tasks:
                        if 'difficulty' not in task:
                            task['difficulty'] = estimate_difficulty(task)
                    return tasks

        print("Файл с заданиями не найден!")
        return []

    except Exception as e:
        print(f"Ошибка загрузки заданий: {e}")
        return []


def estimate_difficulty(task):
    topic = task.get('topic', '').lower()
    text = task.get('task_text', '').lower()
    easy_keywords = ['кодирование', 'декодирование', 'единицы измерения']
    medium_keywords = ['логика', 'графы', 'таблицы']
    hard_keywords = ['программирование', 'алгоритмы', 'рекурсия', 'динамическое программирование']
    if any(keyword in topic for keyword in easy_keywords):
        return 1
    elif any(keyword in topic for keyword in hard_keywords):
        return random.randint(3, 5)
    elif any(keyword in topic for keyword in medium_keywords):
        return random.randint(2, 3)
    else:
        return random.randint(1, 2)


def get_user_stats(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT total_tasks, correct_answers, current_level 
        FROM users WHERE user_id = ?
    ''', (user_id,))

    result = cursor.fetchone()

    if result:
        total_tasks, correct_answers, current_level = result

        correct_rate = correct_answers / total_tasks if total_tasks > 0 else 0

        stats = {
            "total_tasks": total_tasks,
            "correct_answers": correct_answers,
            "correct_rate": correct_rate,
            "current_level": current_level,
            "level_name": DIFFICULTY_LEVELS.get(current_level, {}).get("name", "Новичок")
        }
    else:
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, current_level) 
            VALUES (?, 1)
        ''', (user_id,))
        conn.commit()

        stats = {
            "total_tasks": 0,
            "correct_answers": 0,
            "correct_rate": 0,
            "current_level": 1,
            "level_name": "Новичок"
        }

    conn.close()
    return stats

def get_topic_stats(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT topic, correct_rate, tasks_solved 
        FROM topic_progress 
        WHERE user_id = ?
    ''', (user_id,))

    topics = {}
    for row in cursor.fetchall():
        topic, correct_rate, tasks_solved = row
        topics[topic] = {
            'correct_rate': correct_rate,
            'tasks_solved': tasks_solved,
            'weakness_score': 1.0 - correct_rate
        }

    conn.close()
    return topics


def get_adaptive_task(user_id):
    tasks = load_tasks()
    if not tasks:
        return None

    stats = get_user_stats(user_id)
    user_level = stats["current_level"]
    topic_stats = get_topic_stats(user_id)

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT task_id, timestamp, correct 
        FROM task_history 
        WHERE user_id = ? 
        ORDER BY timestamp DESC
    ''', (user_id,))

    history = cursor.fetchall()
    conn.close()
    task_frequency = {}
    for task_id, timestamp, correct in history:
        task_frequency[task_id] = task_frequency.get(task_id, 0) + 1

    task_ratings = []

    for task in tasks:
        task_difficulty = int(task['difficulty']) if isinstance(task['difficulty'], (int, float, str)) else 1
        level_diff = abs(int(task['difficulty']) - user_level)
        s_level = max(0, 1.0 - level_diff * 0.3)
        topic = task['topic']
        if topic in topic_stats:
            weakness = topic_stats[topic]['weakness_score']
            s_topic = 0.5 + weakness * 0.5
        else:
            s_topic = 1.0
        freq = task_frequency.get(task['id'], 0)
        s_novelty = 1.0 / (1.0 + freq * 0.5)
        last_solved = None
        for task_id, timestamp, correct in history:
            if task_id == task['id']:
                last_solved = timestamp
                break

        if last_solved:
            last_days = (datetime.now() - datetime.fromisoformat(last_solved)).days
            s_time = min(1.0, last_days / 30.0)
        else:
            s_time = 1.0
        w1, w2, w3, w4 = 0.4, 0.3, 0.2, 0.1

        rating = (
                w1 * s_level +
                w2 * s_topic +
                w3 * s_novelty +
                w4 * s_time
        )

        task_ratings.append((rating, task))

    task_ratings.sort(key=lambda x: x[0], reverse=True)
    top_n = min(3, len(task_ratings))
    if top_n == 0:
        return None

    top_tasks = [task for _, task in task_ratings[:top_n]]
    return random.choice(top_tasks)


def normalize_answer(answer):
    if isinstance(answer, str):
        return re.sub(r'\s+', '', answer).lower()
    return str(answer)


def check_answer(user_answer, correct_answer):
    return normalize_answer(user_answer) == normalize_answer(correct_answer)


async def send_task_to_user(message, task, user_id, state):
    user_last_tasks[user_id] = task
    stats = get_user_stats(user_id)

    try:
        level_info = f"Уровень: {stats['level_name']} ({stats['current_level']}/5)"

        difficulty = int(task['difficulty']) if isinstance(task['difficulty'], (int, float, str)) else 1
        difficulty_stars = "★" * difficulty + "☆" * (5 - difficulty)

        task_text = (
            f"{level_info}\n"
            f"Сложность: {difficulty_stars}\n\n"
            f"Тема: {task['topic']}\n\n"
            f"Задание: {task['task_text']}\n\n"
            f"Отправь мне свой ответ!"
        )

        if task.get('image'):
            image_paths = [
                task['image'],
                f"data/{task['image']}",
                f"images/{task['image']}",
                f"../{task['image']}"
            ]

            for image_path in image_paths:
                if os.path.exists(image_path):
                    photo = FSInputFile(image_path)
                    await message.answer_photo(
                        photo=photo,
                        caption=task_text,
                        parse_mode="Markdown"
                    )
                    break
            else:
                await message.answer(task_text, parse_mode="Markdown")
        else:
            await message.answer(task_text, parse_mode="Markdown")

        await state.set_state(AnswerState.waiting_for_answer)

    except Exception as e:
        print(f"Ошибка при отправке задания: {e}")
        level_info = f"Уровень: {stats['level_name']} ({stats['current_level']}/5)"
        difficulty = 1
        difficulty_stars = "★" * difficulty + "☆" * (5 - difficulty)

        task_text = (
            f"{level_info}\n"
            f"Сложность: {difficulty_stars}\n\n"
            f"Тема: {task['topic']}\n\n"
            f"Задание: {task['task_text']}\n\n"
            f"Отправь мне свой ответ!"
        )

        await message.answer(task_text, parse_mode="Markdown")
        await state.set_state(AnswerState.waiting_for_answer)



@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    stats = get_user_stats(user_id)

    welcome_text = (
        f"Привет, {message.from_user.first_name}!\n\n"
        f"Я - бот-репетитор для подготовки к ЕГЭ по информатике.\n\n"
        f"Твой текущий уровень: **{stats['level_name']}**\n"
        f"Правильных ответов: {stats['correct_answers']}/{stats['total_tasks']}\n\n"
        f"Что я умею:\n"
        f"• Давать задания по твоему уровню\n"
        f"• Давать подсказки\n"
        f"• Показывать решения\n"
        f"• Анализировать твой прогресс\n\n"
        f"Просто напиши:\n"
        f"• 'Дай задание' или /task\n"
        f"• 'Подскажи' или /hint\n"
        f"• 'Покажи решение' или /solution\n"
        f"• 'Моя статистика' или /stats"
    )
    await message.answer(welcome_text, parse_mode="Markdown")


@dp.message(Command("task"))
async def cmd_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    task = get_adaptive_task(user_id)

    if not task:
        await message.answer("Задания временно недоступны. Попробуйте позже.")
        return

    await send_task_to_user(message, task, user_id, state)


@dp.message(Command("hint"))
async def cmd_hint(message: types.Message):
    user_id = message.from_user.id
    task = user_last_tasks.get(user_id)

    if task and task.get('hint'):
        await message.answer(f"Подсказка:\n\n{task['hint']}")
    else:
        await message.answer("Сначала получите задание!")


@dp.message(Command("solution"))
async def cmd_solution(message: types.Message):
    user_id = message.from_user.id
    task = user_last_tasks.get(user_id)

    if task and task.get('solution'):
        solution = task['solution']

        if "```" in solution or "print(" in solution:
            await message.answer(f"Решение:\n\n{solution}")
        else:
            await message.answer(f"Решение:\n\n{solution}")
    else:
        await message.answer("Сначала получите задание!")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    stats = get_user_stats(user_id)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT correct FROM task_history 
        WHERE user_id = ? 
        ORDER BY timestamp DESC LIMIT 10
    ''', (user_id,))

    recent_results = cursor.fetchall()
    conn.close()

    recent_chart = ""
    for result in recent_results:
        recent_chart += "✅" if result[0] else "❌"

    stats_text = (
        f"Твоя статистика\n\n"
        f"Уровень: {stats['level_name']}** ({stats['current_level']}/5)\n"
        f"Всего заданий: {stats['total_tasks']}\n"
        f"Правильных: {stats['correct_answers']}\n"
        f"Точность: {stats['correct_rate'] * 100:.1f}%\n\n"
        f"Последние ответы:\n{recent_chart}\n\n"
        f"Система уровней:\n"
        f"★ Новичок (0%+)\n"
        f"★★ Начинающий (40%+)\n"
        f"★★★ Средний (60%+)\n"
        f"★★★★ Продвинутый (75%+)\n"
        f"★★★★★ Эксперт (85%+)"
    )

    await message.answer(stats_text, parse_mode="Markdown")


@dp.message(Command("leaderboard"))
async def cmd_leaderboard(message: types.Message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT user_id, correct_answers, total_tasks, current_level 
        FROM users 
        WHERE total_tasks > 0 
        ORDER BY correct_answers DESC 
        LIMIT 10
    ''')

    leaders = cursor.fetchall()
    conn.close()

    if not leaders:
        await message.answer("Таблица лидеров пока пуста!")
        return

    leaderboard_text = "Топ 10 пользователей:\n\n"

    for i, (user_id, correct, total, level) in enumerate(leaders, 1):
        try:
            user = await bot.get_chat(user_id)
            username = user.first_name or f"Пользователь {user_id}"
        except:
            username = f"Пользователь {user_id}"

        accuracy = (correct / total * 100) if total > 0 else 0
        stars = "★" * level

        leaderboard_text += (
            f"{i}. **{username}**\n"
            f"   {stars} | {correct}/{total} ({accuracy:.1f}%)\n"
        )

    await message.answer(leaderboard_text, parse_mode="Markdown")


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Текущее задание отменено.")


@dp.message(AnswerState.waiting_for_answer)
async def handle_answer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    task = user_last_tasks.get(user_id)

    if not task:
        await message.answer("Сначала получите задание!")
        await state.clear()
        return

    user_answer = message.text
    is_correct = check_answer(user_answer, task['correct_answer'])

    old_level = get_user_stats(user_id)["current_level"]
    new_level = update_user_stats(user_id, task['id'], is_correct)

    if is_correct:
        response = (
            f"Правильно!\n\n"
            f"Верный ответ: {task['correct_answer']}\n\n"
        )

        if new_level > old_level:
            response += f"Поздравляем! Ты перешел на уровень {new_level}!\n\n"

        response += "Отличная работа! Хочешь еще задание?"
    else:
        response = (
            f"Пока неправильно.\n\n"
            f"Твой ответ: {user_answer}\n"
            f"Правильный ответ: {task['correct_answer']}\n\n"
            f"Не расстраивайся! Попробуй еще:"
        )

    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

    if is_correct:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Еще задание"), KeyboardButton(text="Моя статистика")],
                [KeyboardButton(text="Топ игроков")]
            ],
            resize_keyboard=True
        )
    else:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Подсказка"), KeyboardButton(text="Решение")],
                [KeyboardButton(text="Новое задание"), KeyboardButton(text="Статистика")]
            ],
            resize_keyboard=True
        )

    await message.answer(response, reply_markup=keyboard)
    await state.clear()


@dp.message(F.text == "Еще задание")
@dp.message(F.text == "Новое задание")
async def handle_new_task_button(message: types.Message, state: FSMContext):
    await cmd_task(message, state)


@dp.message(F.text == "Подсказка")
async def handle_hint_button(message: types.Message):
    await cmd_hint(message)


@dp.message(F.text == "Решение")
async def handle_solution_button(message: types.Message):
    await cmd_solution(message)


@dp.message(F.text == "Моя статистика")
@dp.message(F.text == "Статистика")
async def handle_stats_button(message: types.Message):
    await cmd_stats(message)


@dp.message(F.text == "Топ игроков")
async def handle_leaderboard_button(message: types.Message):
    await cmd_leaderboard(message)


@dp.message()
async def handle_natural_language(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.lower()

    current_state = await state.get_state()
    if current_state == AnswerState.waiting_for_answer.state:
        return

    if any(word in text for word in ["привет", "здравствуй"]):
        await cmd_start(message)

    elif any(word in text for word in ["задани", "задач", "упражнен", "пример", "дай задание"]):
        await cmd_task(message, state)

    elif any(word in text for word in ["подсказ", "помоги", "не знаю", "не понимаю", "как решать"]):
        task = user_last_tasks.get(user_id)
        if task and task.get('hint'):
            await message.answer(f"Подсказка:\n\n{task['hint']}")
        else:
            await message.answer("Сначала получите задание!")

    elif any(word in text for word in ["решен", "ответ", "объясни", "покажи решение", "как правильно"]):
        task = user_last_tasks.get(user_id)
        if task and task.get('solution'):
            await message.answer(f"Решение:\n\n{task['solution']}")
        else:
            await message.answer("Сначала получите задание!")

    elif any(word in text for word in ["статистик", "прогресс", "уровень", "сколько решил"]):
        await cmd_stats(message)

    elif any(word in text for word in ["топ", "лидер", "лучши"]):
        await cmd_leaderboard(message)

    elif any(word in text for word in ["спасибо", "благодар"]):
        await message.answer("Пожалуйста! Рад был помочь!")

    elif any(word in text for word in ["пока", "до свидани"]):
        await message.answer("До свидания! Возвращайся за новыми заданиями!")

    else:
        response = await rasa_client.send_message(user_id, message.text)
        if response:
            await message.answer(response)
        else:
            help_text = (
                "Я не совсем понял твой вопрос.\n\n"
                "Попробуй:\n"
                "• 'Дай задание' - получить задание\n"
                "• 'Подскажи' - получить подсказку\n"
                "• 'Покажи решение' - увидеть решение\n"
                "• 'Моя статистика' - посмотреть прогресс\n"
                "• Или используй команды: /task, /hint, /solution, /stats"
            )
            await message.answer(help_text)


async def main():
    print("Запущено")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())