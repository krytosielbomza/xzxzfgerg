import os
import asyncio
import logging
import random
import sqlite3
import re
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv('PORT', 10000))
ADMIN_ID = 7770044439

# --- БАЗА ДАННЫХ ---
class Database:
    def __init__(self, db_path="bot.db"):
        self.db_path = db_path
        # check_same_thread=False нужен для работы SQLite в веб-приложениях
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS users 
                           (user_id INTEGER PRIMARY KEY, name TEXT, score INTEGER DEFAULT 0)''')

    def update_score(self, user_id, name, points):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET score = score + ?, name = ? WHERE user_id = ?", (points, name, user_id))
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO users (user_id, name, score) VALUES (?, ?, ?)", (user_id, name, points))
            conn.commit()

    def remove_points(self, user_id, points):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT score FROM users WHERE user_id = ?", (user_id,))
            res = cursor.fetchone()
            if not res: 
                return False, "Пользователь не найден в базе."
            new_score = max(0, res[0] - points)
            cursor.execute("UPDATE users SET score = ? WHERE user_id = ?", (new_score, user_id))
            conn.commit()
            return True, f"Очки удалены. Было: {res[0]}, стало: {new_score}"

    def get_top(self):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            return conn.execute("SELECT name, score FROM users ORDER BY score DESC LIMIT 5").fetchall()

db = Database()

# --- ДАННЫЕ ВИКТОРИНЫ ---
QUESTIONS = [
    {"q": "сосал?", "opts": ["да", "нет", "сосал"], "a": "нет"},
    {"q": "натурал?", "opts": ["да", "нет", "не натурал"], "a": "да"}
]

# --- ЛОГИКА ОТРИСОВКИ МЕМА ---
def create_meme(image_path, text, output_path):
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            w, h = img.size
            fs = int(h * 0.1)
            try: font = ImageFont.truetype("arial.ttf", fs)
            except: font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), text, font=font)
            tx, ty = (w - (bbox[2]-bbox[0])) / 2, h - (bbox[3]-bbox[1]) - 50
            draw.text((tx, ty), text, font=font, fill="white", stroke_width=3, stroke_fill="black")
            img.save(output_path, "JPEG")
        return True
    except Exception as e:
        logger.error(f"Ошибка создания мема: {e}")
        return False

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [
        [InlineKeyboardButton("Создать мэрин", callback_data="create_marina")],
        [InlineKeyboardButton("Топ алкашей", callback_data="top"), InlineKeyboardButton("Викторина", callback_data="quiz")],
        [InlineKeyboardButton("Пойти нахер", callback_data="naherpoyti")]
    ]
    await update.message.reply_text("Привет! Я бот нарикнат. Выбирай кнопку или пиши команды.", 
                                   reply_markup=InlineKeyboardMarkup(kbd))

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "create_marina":
        context.user_data["wait_for_photo"] = True
        await query.edit_message_text("Скинь фото для мема")
    
    elif query.data == "top":
        top = db.get_top()
        text = "🏆 Топ алкашей:\n\n" + "\n".join([f"{i+1}. {n} - {s}" for i, (n, s) in enumerate(top)]) if top else "Все лошки"
        await query.edit_message_text(text)

    elif query.data == "naherpoyti":
        await query.edit_message_text("иди нахер мразота")

    elif query.data == "quiz":
        q = random.choice(QUESTIONS)
        context.user_data.update({"correct": q["a"], "q_text": q["q"]})
        btns = [[InlineKeyboardButton(o, callback_data=f"ans_{o}")] for o in q["opts"]]
        await query.edit_message_text(q["q"], reply_markup=InlineKeyboardMarkup(btns))

    elif query.data.startswith("ans_"):
        selected = query.data.replace("ans_", "")
        correct = context.user_data.get("correct")
        if selected == correct:
            db.update_score(query.from_user.id, query.from_user.first_name, 10)
            await query.edit_message_text(f"Правильно! +10 очков. Ты не опозорен.")
        else:
            await query.edit_message_text(f"Неверно! Правильный ответ: {correct}. Опозорен.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    text = update.message.text or ""
    chat_type = update.effective_chat.type

    if ud.get("wait_for_text"):
        os.makedirs("temp", exist_ok=True)
        out = f"temp/out_{update.effective_user.id}.jpg"
        if create_meme(ud["image_path"], text, out):
            with open(out, "rb") as f:
                await update.message.reply_photo(f, caption="Мэрин готов")
        else:
            await update.message.reply_text("Ошибка при создании мема.")
        
        if os.path.exists(ud.get("image_path", "")): os.remove(ud["image_path"])
        if os.path.exists(out): os.remove(out)
        ud.clear()
        return

    low_text = text.lower()
    
    if low_text.startswith("ударить"):
        attacker = update.effective_user.first_name
        target = "кого-то"
        if update.message.reply_to_message:
            target = update.message.reply_to_message.from_user.first_name
        else:
            match = re.search(r'@(\w+)', text)
            if match: target = f"@{match.group(1)}"
        rx = random.choice(["🤛 ударил", "👊 дал леща", "💥 стукнул", "👋 дал пощёчину"])
        await update.message.reply_text(f"{attacker} {rx} {target}!")
        return

    elif low_text == "134":
        await update.message.reply_text("код 134 запущен.")
        return

    elif "умри" in low_text:
        await update.message.reply_text("не")
        return

    if chat_type == "private":
        await update.message.reply_text(text.lower())

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("wait_for_photo"):
        os.makedirs("temp", exist_ok=True)
        f = await update.message.photo[-1].get_file()
        path = f"temp/in_{update.effective_user.id}.jpg"
        await f.download_to_drive(path)
        context.user_data.update({"image_path": path, "wait_for_photo": False, "wait_for_text": True})
        await update.message.reply_text("Картинку принял. Пиши текст!")

async def add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = int(context.args[0]); points = int(context.args[1])
        try:
            chat = await context.bot.get_chat(user_id)
            name = chat.first_name or f"User_{user_id}"
        except: name = f"User_{user_id}"
        db.update_score(user_id, name, points)
        await update.message.reply_text(f"✅ Добавлено {points} очков пользователю {name}")
    except:
        await update.message.reply_text("Ошибка. Юзай: /add_points ID ОЧКИ")

async def remove_points_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        user_id = int(context.args[0]); points = int(context.args[1])
        success, message = db.remove_points(user_id, points)
        await update.message.reply_text(f"{'✅' if success else '❌'} {message}")
    except:
        await update.message.reply_text("Ошибка. Юзай: /remove_points ID ОЧКИ")

# --- SERVER & LAUNCH ---
app = Flask(__name__)
application = None

@app.route('/')
def index(): return "Бот работает", 200

@app.route('/' + (BOT_TOKEN or ''), methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    # Используем create_task, чтобы не блокировать ответ Telegram серверу
    asyncio.create_task(application.process_update(update))
    return 'OK', 200

async def start_bot():
    global application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_points", add_points))         
    application.add_handler(CommandHandler("remove_points", remove_points_command)) 
    application.add_handler(CommandHandler("top", lambda u, c: handle_buttons(u, c)))
    application.add_handler(CallbackQueryHandler(handle_buttons))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    await application.start()

    if WEBHOOK_URL:
        url = WEBHOOK_URL.replace("http://", "https://").rstrip('/')
        await application.bot.set_webhook(f"{url}/{BOT_TOKEN}", drop_pending_updates=True)
        logger.info(f"Вебхук запущен на {url}")
        
        # Правильный запуск Flask сервера внутри asyncio
        from werkzeug.serving import run_simple
        # Это позволит Flask работать в асинхронной среде Render
        run_simple('0.0.0.0', PORT, app, use_reloader=False)
    else:
        await application.updater.start_polling()
        logger.info("Polling запущен")
        while True: await asyncio.sleep(1)

if __name__ == '__main__':
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")