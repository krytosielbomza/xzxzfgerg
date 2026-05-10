from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
import random
import re
from telegram.ext import MessageHandler, filters
import sqlite3
from telegram.ext import MessageHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from PIL import Image, ImageDraw, ImageFont
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from flask import Flask


app = Flask(__name__)

@app.route('/', methods=['HEAD'])
def head_handler():
    return '', 200


load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = 7770044439



HIT_REACTIONS = [
    "🤛 ударил",
    "👊 дал леща",
    "💥 стукнул",
    "👋 дал пощёчину",
    "🥊 нанёс удар"
]


async def handle_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text

    # Проверяем, начинается ли сообщение со слова «ударить» (регистронезависимо)
    if not message_text or not message_text.lower().startswith('ударить'):
        return  # Не наша команда — выходим

    attacker_name = update.effective_user.first_name

    # Сценарий 1: ответ на сообщение
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        target_name = target_user.first_name
        reaction = random.choice(HIT_REACTIONS)
        response = f"{attacker_name} {reaction} {target_name}!"
        await update.message.reply_text(response)

    # Сценарий 2: username в тексте
    else:
        words = message_text.split()
        username = None

        # Ищем @username после слова «ударить»
        for word in words[1:]:
            if word.startswith('@'):
                username = word.lstrip('@')
                break

        if username:
            reaction = random.choice(HIT_REACTIONS)
            response = f"{attacker_name} {reaction} пользователя @{username}!"
            await update.message.reply_text(response)
        else:
            # Сценарий 3: неправильное использование
            await update.message.reply_text(
                "Использование:\n"
                "• Ответьте на сообщение и напишите «ударить»\n"
                "• Или напишите «ударить @username»"
            )


async def handle_134(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if user_message == '134':
        await update.message.reply_text('код 134 запущен.')


class Database:
    def __init__(self, db_path="bot.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                name TEXT,
                score INTEGER DEFAULT 0
            )
        ''')
        conn.commit()
        conn.close()

    def update_score(self, user_id, name, points):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()

        if result:
            cursor.execute("UPDATE users SET score = score + ?, name = ? WHERE user_id = ?",
                         (points, name, user_id))
        else:
            cursor.execute("INSERT INTO users (user_id, name, score) VALUES (?, ?, ?)",
                         (user_id, name, points))

        conn.commit()
        conn.close()

    def get_top_players(self, limit=5):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name, score FROM users ORDER BY score DESC LIMIT ?", (limit,))
        top = cursor.fetchall()
        conn.close()
        return top

    def remove_points(self, user_id, points):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT score FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()

        if not result:
            conn.close()
            return False, "Пользователь не найден"

        current_score = result[0]
        new_score = max(0, current_score - points)

        cursor.execute("UPDATE users SET score = ? WHERE user_id = ?", (new_score, user_id))
        conn.commit()
        conn.close()
        return True, f"Удалено {points} очков. Было: {current_score}, стало: {new_score}"

    def clear_points(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET score = 0 WHERE user_id = ?", (user_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        if affected > 0:
            return True, "Очки сброшены до 0"
        else:
            return False, "Пользователь не найден"

# Инициализация базы данных
db = Database()

questions = [
    {
        "question": "сосал?",
        "options": ["да", "нет", "сосал"],
        "answer": "нет"
    },
    {
        "question": "натурал?",
        "options": ["да", "нет", "не натурал"],
        "answer": "да"
    }
]

async def add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Использование: /add_points <user_id> <points>")
        return

    try:
        user_id = int(context.args[0])
        points = int(context.args[1])

        # Получаем реальное имя пользователя из Telegram
        user = await context.bot.get_chat(user_id)
        user_name = user.first_name or user.username or f"User_{user_id}"

        db.update_score(user_id, user_name, points)
        await update.message.reply_text(f"Добавлено {points} очков пользователю {user_name}.")
    except ValueError:
        await update.message.reply_text("Ошибка: user_id и points должны быть числами.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при получении информации о пользователе: {e}")

        
async def remove_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас нет прав для этой команды.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Использование: /remove_points <user_id> <points>")
        return

    try:
        user_id = int(context.args[0])
        points = int(context.args[1])

        if points <= 0:
            await update.message.reply_text("Количество очков должно быть положительным числом.")
            return

        success, message = db.remove_points(user_id, points)
        await update.message.reply_text(message)
    except ValueError:
        await update.message.reply_text("Ошибка: user_id и points должны быть числами.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот нарикнат")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Я могу:\n"
        "/start - поздороваться\n"
        "/naheridi - послать нахер\n"
        "Умри"
    )
    await update.message.reply_text(text)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_players = db.get_top_players()

    if not top_players:
        await update.message.reply_text("пока что все лошки")
        return
    
    text = "топ алкашей:\n\n"
    for i, (name, score) in enumerate(top_players, start=1):
        text += f"{i}. {name} - {score} очков\n"
    await update.message.reply_text(text)

async def naheridi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("иди нахер")

async def start_quiz_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = random.choice(questions)
    context.user_data["correct_answer"] = q["answer"]
    context.user_data["current_question"] = q["question"]

    buttons = [
        InlineKeyboardButton(opt, callback_data=f"answer_{opt}")
        for opt in q["options"]
    ]
    markup = InlineKeyboardMarkup.from_column(buttons)
    await update.message.reply_text(q["question"], reply_markup=markup)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.lower()
    if "умри" in user_text:
        await update.message.reply_text("не")
    else:
        await update.message.reply_text(user_text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_for_photo"):
        return

    # Сбрасываем флаг ожидания фото сразу
    context.user_data["wait_for_photo"] = False


    photo = update.message.photo[-1]
    file = await photo.get_file()

    os.makedirs("temp", exist_ok=True)


    # Используем фиксированное имя файла для фото
    image_path = "temp/meme.jpg"

    try:
        await file.download_to_drive(image_path)
        # Сохраняем путь к изображению в user_data
        context.user_data["image_path"] = image_path
        context.user_data["wait_for_text"] = True
        await update.message.reply_text("напиши говно какое-то")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при сохранении фото: {e}")
        # Сбрасываем все флаги при ошибке
        context.user_data.pop("wait_for_text", None)
        context.user_data.pop("image_path", None)

async def handle_meme_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_for_text"):
        return

    text = update.message.text
    image_path = context.user_data.get("image_path")


    if not image_path or not os.path.exists(image_path):
        await update.message.reply_text("❌ Ошибка: изображение не найдено. Начните заново.")
        context.user_data.pop("wait_for_text", None)
        context.user_data.pop("image_path", None)
        return

    output_path = "temp/final_meme.jpg"

    try:
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        width, height = img.size

        # Пробуем загрузить шрифт
        font_path = "font/arial.ttf"
        try:
            # Начинаем с большого размера шрифта
            font_size = 100
            font = ImageFont.truetype(font_path, size=font_size)
        except:
            font = ImageFont.load_default()
            font_size = 40

        # Параметры текста
        margin = 50
        max_text_width = width - 2 * margin
        max_text_height = height // 4  # Отводим четверть высоты под текст

        # Подбираем размер шрифта, чтобы текст поместился
        while font_size > 10:
            try:
                # Получаем размеры текста с текущим шрифтом
                text_bbox = draw.textbbox((0, 0), text, font=font)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]

                if text_width <= max_text_width and text_height <= max_text_height:
                    break  # Текст помещается — выходим из цикла

                font_size -= 5
                font = ImageFont.truetype(font_path, size=font_size)
            except:
                font_size -= 5

        # Позиция текста — внизу с отступом
        text_position = (margin, height - margin - max_text_height)

        # Обводка текста (чёрный контур)
        outline_range = max(1, font_size // 20)  # Толщина обводки зависит от размера шрифта
        for dx in range(-outline_range, outline_range + 1):
            for dy in range(-outline_range, outline_range + 1):
                draw.text((text_position[0] + dx, text_position[1] + dy), text, font=font, fill="black")

        # Основной текст (белый)
        draw.text(text_position, text, font=font, fill="white")

        img.save(output_path)

        with open(output_path, "rb") as photo_file:
            await update.message.reply_photo(photo=photo_file)

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при создании мема: {e}")
    finally:
        # Сбрасываем флаги
        context.user_data.pop("wait_for_text", None)
        context.user_data.pop("image_path", None)

        # Удаляем временные файлы
        for temp_file in [image_path, output_path]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as del_error:
                    await update.message.reply_text(f"⚠️ Не удалось удалить временный файл {temp_file}: {del_error}")

async def send_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    q = random.choice(questions)
    context.user_data["correct_answer"] = q["answer"]
    context.user_data["current_question"] = q["question"]

    buttons = [
        InlineKeyboardButton(opt, callback_data=f"answer_{opt}")
        for opt in q["options"]
    ]
    markup = InlineKeyboardMarkup.from_column(buttons)
    await query.edit_message_text(q["question"], reply_markup=markup)

async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("answer_"):
        selected = data.replace("answer_", "")
        correct = context.user_data.get("correct_answer")
        question = context.user_data.get("current_question")

        context.user_data.pop("correct_answer", None)
        context.user_data.pop("current_question", None)

        if selected == correct:
            user_id = query.from_user.id
            name = query.from_user.first_name
            db.update_score(user_id, name, 10)
            await query.edit_message_text(
                f"Вопрос был: {question}\n"
                f"Ты выбрал: {selected}. бааалин.\n"
                f"+10 очков!"
            )
        else:
            await query.edit_message_text(
                f"вопрос был: {question}\n"
                f"ты выбрал: {selected}, а надо было: {correct}. опозорен"
            )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "naherpoyti":
        await query.edit_message_text("иди нахер мразота")
    elif data == "create_marina":
        await query.edit_message_text("скинь писюн")
        context.user_data["wait_for_photo"] = True
    elif data == "quiz":
        await send_quiz(update, context)
    elif data == "top":
        top_players = db.get_top_players()
        if not top_players:
            await query.edit_message_text("пока что вы все лошки")
            return
        text = "топ алкашей:\n\n"
        for i, (name, score) in enumerate(top_players, start=1):
            text += f"{i}. {name} - {score} очков\n"
        await query.edit_message_text(text)
    elif data.startswith("answer_"):
        await check_answer(update, context)

keyboard = [
    [InlineKeyboardButton("пойти нахер", callback_data="naherpoyti"),
     InlineKeyboardButton("создать мэрин", callback_data="create_marina")],
    [InlineKeyboardButton("викторина", callback_data="quiz"),
     InlineKeyboardButton("топ нариков", callback_data="top")]
]

menu = InlineKeyboardMarkup(keyboard)

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выбери, что хочешь сделать:",
        reply_markup=menu
    )

# Инициализация базы данных
db = Database()

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help))
app.add_handler(CommandHandler("menu", menu_handler))
app.add_handler(CommandHandler("naheridi", naheridi))
app.add_handler(CommandHandler("top", top))
app.add_handler(CommandHandler("add_points", add_points))
app.add_handler(CommandHandler("remove_points", remove_points))


app.add_handler(MessageHandler(filters.PHOTO, handle_photo))


app.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(re.compile(r"викторина", re.IGNORECASE)),
        start_quiz_from_text
    )
)

app.add_handler(
    MessageHandler(
        filters.TEXT & filters.Regex(re.compile(r"^ударить", re.IGNORECASE)),
        handle_hit
    )
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_134
    )
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_meme_text
    )
)

app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, echo)
)

app.add_handler(CallbackQueryHandler(handle_buttons, pattern="^(naherpoyti|create_marina|top)$"))
app.add_handler(CallbackQueryHandler(send_quiz, pattern="^quiz$"))
app.add_handler(CallbackQueryHandler(check_answer, pattern="^answer_"))


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    # Получаем порт из переменной окружения (Render автоматически подставит 10000)
    # Локально используем 8000, если PORT не задан
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🔧 Health server запущен на порту {port}")
    server.serve_forever()

# Запускаем HTTP‑сервер в отдельном потоке (daemon=True — завершится при остановке бота)
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()


app.run_polling()
