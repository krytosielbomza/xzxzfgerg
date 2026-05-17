import os
import asyncio
import logging
import random
import sqlite3
import re
import threading
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
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS users 
                           (user_id INTEGER PRIMARY KEY, name TEXT, score INTEGER DEFAULT 0)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS inventory 
                           (user_id INTEGER, item_id TEXT, count INTEGER DEFAULT 0,
                            PRIMARY KEY (user_id, item_id))''')

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

    def get_score(self, user_id):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            res = conn.execute("SELECT score FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return res[0] if res else 0

    def get_top(self):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            return conn.execute("SELECT name, score FROM users ORDER BY score DESC LIMIT 5").fetchall()

    def get_item_count(self, user_id, item_id):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            res = conn.execute("SELECT count FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id)).fetchone()
            return res[0] if res else 0

    def add_item(self, user_id, item_id, amount=1):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE inventory SET count = count + ? WHERE user_id = ? AND item_id = ?", (amount, user_id, item_id))
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO inventory (user_id, item_id, count) VALUES (?, ?, ?)", (user_id, item_id, amount))
            conn.commit()

    def consume_item(self, user_id, item_id):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id))
            res = cursor.fetchone()
            if not res or res[0] <= 0:
                return False
            if res[0] == 1:
                cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (user_id, item_id))
            else:
                cursor.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND item_id = ?", (user_id, item_id))
            conn.commit()
            return True

    def buy_item(self, user_id, name, item_id, price):
        current_score = self.get_score(user_id)
        if current_score < price:
            return False, "Недостаточно очков для покупки!"
        
        self.update_score(user_id, name, -price)
        self.add_item(user_id, item_id, 1)
        return True, "Успешная покупка!"

    def get_inventory(self, user_id):
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            return conn.execute("SELECT item_id, count FROM inventory WHERE user_id = ? AND count > 0", (user_id,)).fetchall()
        

    def transfer_item(self, from_user_id, to_user_id, item_id, amount=1):
        if amount <= 0:
            return False, "Количество должно быть больше 0!"
            
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            cursor = conn.cursor()
            # Проверяем, есть ли у отправителя этот предмет и в каком количестве
            cursor.execute("SELECT count FROM inventory WHERE user_id = ? AND item_id = ?", (from_user_id, item_id))
            res = cursor.fetchone()
            
            if not res or res[0] < amount:
                return False, "У тебя нет этого предмета или его недостаточно!"
                
            # Списываем у отправителя
            if res[0] == amount:
                cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_id = ?", (from_user_id, item_id))
            else:
                cursor.execute("UPDATE inventory SET count = count - ? WHERE user_id = ? AND item_id = ?", (amount, from_user_id, item_id))
                
            # Добавляем получателю
            cursor.execute("UPDATE inventory SET count = count + ? WHERE user_id = ? AND item_id = ?", (amount, to_user_id, item_id))
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO inventory (user_id, item_id, count) VALUES (?, ?, ?)", (to_user_id, item_id, amount))
                
            conn.commit()
            return True, "Успешный перевод предметов!"

db = Database()

# --- ДАННЫЕ ВИКТОРИНЫ И МАГАЗИНА ---
QUESTIONS = [
    {"q": "сосал?", "opts": ["да", "нет", "сосал"], "a": "нет"},
    {"q": "натурал?", "opts": ["да", "нет", "не натурал"], "a": "да"}
]

SHOP_ITEMS = {
    "mullvad": {"name": "🧱 Mullvad VPN", "price": 35, "desc": "Необходим для сваттинга"}
}

# --- СПИСКИ ДЛЯ СЛУЧАЙНЫХ ДАННЫХ ---
RANDOM_LAST_NAMES = ["Бутылкин", "Алкашов", "Пивозавров", "Заливайко", "Чекушкин", "Закусонов", "Подзаборный", "Опохмелов", "Шмурдяк", "Дрожжевой", "Сушняков", "Тунеядцев"]
RANDOM_FIRST_NAMES = ["Гена", "Вася", "Петя", "Толик", "Серега", "Антон", "Димон", "Вован", "Славик", "Валера", "Гоша", "Эдик", "Михалыч", "Пашок", "Кирюха"]
RANDOM_MIDDLE_NAMES = ["Петрович", "Васильевич", "Анатольевич", "Сергеевич", "Владимирович", "Игоревич", "Валерьевич", "Михайлович", "Александрович", "Дмитриевич"]

RANDOM_CITIES = ["г. Мухосранск", "г. Запивонск", "г. Бутырск", "ПГТ Колдыри", "г. Трезвоневидальск", "д. Гадюкино", "г. Нижние Обрыганы", "г. Верхние Грязи"]
RANDOM_STREETS = ["ул. Пивных героев", "пер. Разливной", "проезд Хмельной", "ул. Трезвости (нечетная сторона)", "ул. Подзаборная", "тупик Опохмелочный", "бульвар Закусочный", "ул. Полторашная"]

# --- ЛОГИКА ОТРИСОВКИ МЕМА ---
def create_meme(image_path, text, output_path):
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            w, h = img.size
            fs = int(h * 0.12)
            
            try: 
                font = ImageFont.truetype("arial.ttf", fs)
                while fs > 10:
                    bbox = draw.textbbox((0, 0), text, font=font)
                    text_w = bbox[2] - bbox[0]
                    if text_w < int(w * 0.9):
                        break
                    fs -= 2
                    font = ImageFont.truetype("arial.ttf", fs)
            except: 
                try: font = ImageFont.load_default(size=int(h * 0.08))
                except: font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            
            tx = (w - text_w) / 2
            ty = h - text_h - int(h * 0.08)
            stroke_w = max(2, int(fs * 0.08))
            
            draw.text((tx, ty), text, font=font, fill="white", stroke_width=stroke_w, stroke_fill="black")
            img.save(output_path, "JPEG")
        return True
    except Exception as e:
        logger.error(f"Ошибка создания мема: {e}")
        return False

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот нарикнат. Пиши /menu")

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [
        [InlineKeyboardButton("Создать мэрин", callback_data="create_marina")],
        [InlineKeyboardButton("🏆 Топ алкашей", callback_data="top"), InlineKeyboardButton("🧠 Викторина", callback_data="quiz")],
        [InlineKeyboardButton("🛒 Магазин", callback_data="shop_menu"), InlineKeyboardButton("🎒 Мой инвентарь", callback_data="inventory_menu")],
        [InlineKeyboardButton("Пойти нахер", callback_data="naherpoyti")]
    ]
    await update.message.reply_text("📋 **Главное меню бота**\nВыбирай нужный раздел ниже:", 
                                   parse_mode="Markdown",
                                   reply_markup=InlineKeyboardMarkup(kbd))

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_name = query.from_user.first_name
    
    if query.data == "create_marina":
        context.user_data["wait_for_photo"] = True
        await query.edit_message_text("Скинь фото для мема")
    
    elif query.data == "top":
        top = db.get_top()
        text = "🏆 Топ алкашей:\n\n" + "\n".join([f"{i+1}. {n} - {s}" for i, (n, s) in enumerate(top)]) if top else "Все лошки"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]]))

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
            db.update_score(user_id, user_name, 2)
            await query.edit_message_text(f"Правильно! +2 очка. Ты не опозорен.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Главное меню", callback_data="back_to_main")]]))
        else:
            await query.edit_message_text(f"Неверно! Правильный ответ: {correct}. Опозорен.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Главное меню", callback_data="back_to_main")]]))

    elif query.data == "shop_menu":
        balance = db.get_score(user_id)
        text = f"🛒 **Магазин предметов**\nТвой баланс: `{balance}` очков викторины\n\nАссортимент:\n"
        kbd = []
        for item_id, info in SHOP_ITEMS.items():
            text += f"• **{info['name']}** — Цена: {info['price']} очков\n_{info['desc']}_\n\n"
            kbd.append([InlineKeyboardButton(f"Купить {info['name']}", callback_data=f"buy_{item_id}")])
        kbd.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kbd))

    elif query.data.startswith("buy_"):
        item_id = query.data.replace("buy_", "")
        if item_id in SHOP_ITEMS:
            price = SHOP_ITEMS[item_id]["price"]
            success, msg = db.buy_item(user_id, user_name, item_id, price)
            if success:
                await query.edit_message_text(f"✅ Ты успешно приобрёл **{SHOP_ITEMS[item_id]['name']}** за {price} очков!", 
                                               parse_mode="Markdown", 
                                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ В магазин", callback_data="shop_menu")]]))
            else:
                await query.edit_message_text(f"❌ Ошибка: {msg}", 
                                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ В магазин", callback_data="shop_menu")]]))

    elif query.data == "inventory_menu":
        inv = db.get_inventory(user_id)
        if not inv:
            text = "🎒 **Твой инвентарь пуст.**\nИди нахер"
        else:
            text = "🎒 **Твой инвентарь:**\n\n"
            for item_id, count in inv:
                name = SHOP_ITEMS[item_id]["name"] if item_id in SHOP_ITEMS else item_id
                text += f"• {name} — {count} шт.\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]]))

    elif query.data == "back_to_main":
        kbd = [
            [InlineKeyboardButton("Создать мэрин", callback_data="create_marina")],
            [InlineKeyboardButton("🏆 Топ алкашей", callback_data="top"), InlineKeyboardButton("🧠 Викторина", callback_data="quiz")],
            [InlineKeyboardButton("🛒 Магазин", callback_data="shop_menu"), InlineKeyboardButton("🎒 Мой инвентарь", callback_data="inventory_menu")],
            [InlineKeyboardButton("Пойти нахер", callback_data="naherpoyti")]
        ]
        await query.edit_message_text("📋 **Главное меню бота**\nВыбирай нужный раздел ниже:", 
                                       parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(kbd))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    text = update.message.text or ""
    chat_type = update.effective_chat.type
    sender_id = update.effective_user.id
    sender_name = update.effective_user.first_name

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
    
    # --- ФУНКЦИЯ: ДОНОС ---
    if low_text.startswith("сват"):
        # ПРОВЕРКА: Наказание, если доносчик БЕЗ VPN
        if db.get_item_count(sender_id, "mullvad") <= 0:
            await update.message.reply_text(
                f"🚨 **ГЛУПЫЙ СВАТЕР ОБНАРУЖЕН!**\n\n"
                f"Гражданин **{sender_name}** попытался совершить сваттинг без использования Mullvad VPN! "
                f"Ваш IP-адрес мгновенно перехвачен майором. К вам уже выехал усиленный наряд полиции! 🚔💨", 
                parse_mode="Markdown"
            )
            await asyncio.sleep(5)
            await update.message.reply_text(
                f"💥🪓 **БАМ-БАМ-БАМ!**\n\n"
                f"Гражданину **{sender_name}** прямо сейчас успешно выломали входную дверь! "
                f"Проводится задержание за статью 207 УК РФ"
            )
            return

        # Если VPN есть, выполняем штатный донос
        target_mention = ""
        target_name = "кого-то"
        
        if update.message.reply_to_message:
            user = update.message.reply_to_message.from_user
            target_name = user.first_name
            target_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
        else:
            match = re.search(r'@(\w+)', text)
            if match: 
                target_mention = f"@{match.group(1)}"
                target_name = target_mention
            else:
                await update.message.reply_text("⚠️ Отметь человека через @ или ответь на его сообщение, чтобы отправить донос.")
                return

        # Списываем предмет за использование
        db.consume_item(sender_id, "mullvad")

        await update.message.reply_text(f"🕵️‍♂️ Анонимный звонок принят. На {target_mention} составлена жалоба...")
        await asyncio.sleep(4)
        
        reasons = [
            "подозрения в терроризме",
            "заложники в доме",
            "незаконный оборот огнестрельного оружися",
            "лжеминирование"
        ]
        
        response_text = (
            f"🚓💨 **ВНИМАНИЕ, ОПЕРАТИВНЫЕ МЕРОПРИЯТИЯ!**\n\n"
            f"К гражданину(ке) **{target_name}** экстренно выехал наряд полиции для проведения жесткой проверки!\n"
            f"📋 **Причина выезда:** {random.choice(reasons)}.\n"
            f"🛡️ _Доносчик успешно скрылся под защитой зашифрованного туннеля Mullvad VPN._"
        )
        await update.message.reply_text(response_text, parse_mode="Markdown")
        return

    # --- ФУНКЦИЯ: ПРОВЕРКА (ДЕАНОН ФИО) ---
    if low_text.startswith("докс") or low_text.startswith("проверка"):
        target_mention = ""
        target_name = "кого-то"
        
        if update.message.reply_to_message:
            user = update.message.reply_to_message.from_user
            target_name = user.first_name
            target_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
        else:
            match = re.search(r'@(\w+)', text)
            if match: 
                target_mention = f"@{match.group(1)}"
                target_name = target_mention
            else:
                await update.message.reply_text("⚠️ Отметь человека через @ или ответь на его сообщение.")
                return
        
        await update.message.reply_text(f"🚨 {target_mention}, вас задеанонили...", parse_mode="Markdown")
        await asyncio.sleep(3)
        
        fake_last_name = random.choice(RANDOM_LAST_NAMES)
        fake_name = random.choice(RANDOM_FIRST_NAMES)
        fake_middle_name = random.choice(RANDOM_MIDDLE_NAMES)
        
        fake_city = random.choice(RANDOM_CITIES)
        fake_street = random.choice(RANDOM_STREETS)
        fake_house = random.randint(1, 150)
        fake_flat = random.randint(1, 350)
        
        response_text = (
            f"🔍 **Результаты слива базы данных для {target_name}:**\n\n"
            f"🪪 Настоящее ФИО: **{fake_last_name} {fake_name} {fake_middle_name}**\n"
            f"🏠 Адрес прописки: `{fake_city}, {fake_street}, д. {fake_house}, кв. {fake_flat}`"
        )
        await update.message.reply_text(response_text, parse_mode="Markdown")
        return

    # Команда "ударить"
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
        await update.message.reply_text("напиши текст")

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

async def transfer_item_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    
    # Проверяем аргументы
    if not context.args:
        await update.message.reply_text("⚠️ Юзай: `/give [id_предмета] [кол-во]` (ответом на сообщение) или `/give [id_предмета] [кол-во] [ID_юзера]`", parse_mode="Markdown")
        return

    item_id = context.args[0].lower()
    
    # Проверяем количество (по дефолту 1, если не указано)
    amount = 1
    if len(context.args) > 1:
        try:
            amount = int(context.args[1])
        except ValueError:
            await update.message.reply_text("❌ Количество должно быть числом.")
            return

    # Определяем получателя
    target_id = None
    target_name = "Пользователь"
    
    if update.message.reply_to_message:
        # Если команда отправлена ответом на сообщение
        target_user = update.message.reply_to_message.from_user
        target_id = target_user.id
        target_name = target_user.first_name
    elif len(context.args) > 2:
        # Если ID получателя передан третьим аргументом
        try:
            target_id = int(context.args[2])
            target_name = f"Юзер {target_id}"
        except ValueError:
            await update.message.reply_text("❌ Неверный ID получателя.")
            return
            
    if not target_id:
        await update.message.reply_text("⚠️ Ответь командой на сообщение получателя или укажи его ID третьим аргументом.")
        return

    if target_id == sender_id:
        await update.message.reply_text("🤡 Передавать предметы самому себе? Сильно.")
        return

    # Название предмета для красивого вывода
    item_name = SHOP_ITEMS[item_id]["name"] if item_id in SHOP_ITEMS else item_id

    # Проводим операцию в БД
    success, message = db.transfer_item(sender_id, target_id, item_id, amount)
    
    if success:
        await update.message.reply_text(
            f"🎒 **Передача предметов**\n\n"
            f"Вы успешно передали **{item_name}** ({amount} шт.) пользователю **{target_name}**!", 
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ Ошибка: {message}")

# --- WEB СЕРВЕР (FLASK) ---
app = Flask(__name__)
application = None
loop = None

@app.route('/')
def index():
    return "Статус: OK", 200

@app.route('/ping')
def ping():
    return "PONG! Я живой!", 200

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/' + (BOT_TOKEN or ''), methods=['POST'])
def webhook():
    try:
        update_data = request.get_json(force=True)
        if not update_data:
            return 'No data', 400
            
        update = Update.de_json(update_data, application.bot)
        if loop and application:
            loop.call_soon_threadsafe(asyncio.create_task, application.process_update(update))
        return 'OK', 200
    except Exception as e:
        logger.error(f"Ошибка во время обработки вебхука: {e}")
        return 'Error', 500

def run_flask():
    logger.info(f"Flask стартует на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

async def start_bot():
    global application, loop
    loop = asyncio.get_running_loop()
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Измененные и добавленные хендлеры команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("add_points", add_points))         
    application.add_handler(CommandHandler("remove_points", remove_points_command)) 
    application.add_handler(CommandHandler("give", transfer_item_command))
    application.add_handler(CommandHandler("top", lambda u, c: handle_buttons(u, c)))
    application.add_handler(CallbackQueryHandler(handle_buttons))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    await application.start()

    if WEBHOOK_URL:
        url = WEBHOOK_URL.replace("http://", "https://").rstrip('/')
        await application.bot.set_webhook(url=f"{url}/{BOT_TOKEN}", drop_pending_updates=True)
        logger.info(f"✅ Вебхук установлен: {url}/{BOT_TOKEN}")
        
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        while True:
            await asyncio.sleep(3600)
    else:
        logger.info("✅ Запуск в режиме Polling")
        await application.updater.start_polling()
        while True:
            await asyncio.sleep(1)

if __name__ == '__main__':
    os.makedirs("temp", exist_ok=True)
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)