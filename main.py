
import os
import sqlite3
from datetime import date
import telebot
from telebot import types

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не найден BOT_TOKEN")

bot = telebot.TeleBot(TOKEN)
DB_NAME = "wpbpvz.db"

INCOMES = [
    "Выдача заказов",
    "Приёмка товаров",
    "Переупаковка",
    "Рейтинг ПВЗ",
    "Рекламные экраны",
    "Другие доходы"
]

EXPENSES = [
    "Аренда",
    "Коммунальные услуги",
    "Зарплата",
    "Налоги и взносы",
    "Интернет",
    "Хозяйственные расходы",
    "Ремонт",
    "Прочие расходы"
]

selected_pvz = {}


def db():
    return sqlite3.connect(DB_NAME)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pvz (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                address TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pvz_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                month TEXT NOT NULL
            )
        """)


init_db()


def main_menu(message):
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    keyboard.add(
        "🏢 Мои ПВЗ",
        "➕ Добавить ПВЗ",
        "📊 Общий отчёт",
        "❓ Помощь"
    )
    bot.send_message(
        message.chat.id,
        "🤖 WBPVZbot\n\n"
        "Финансовый помощник собственника ПВЗ.\n"
        "Выбери действие:",
        reply_markup=keyboard
    )


def pvz_menu(chat_id, user_id):
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    keyboard.add(
        "💰 Добавить доход",
        "💸 Добавить расход",
        "📈 Отчёт ПВЗ",
        "🔄 Выбрать другой ПВЗ",
        "🏠 Главное меню"
    )
    bot.send_message(
        chat_id,
        "🏢 Управление выбранным ПВЗ\n\n"
        "Выбери нужное действие:",
        reply_markup=keyboard
    )


def get_pvz(user_id):
    with db() as conn:
        return conn.execute(
            "SELECT id, name, address FROM pvz WHERE user_id=?",
            (user_id,)
        ).fetchall()


def choose_pvz(message):
    user_id = message.from_user.id
    items = get_pvz(user_id)

    if not items:
        bot.send_message(
            message.chat.id,
            "У тебя пока нет добавленных ПВЗ.\n"
            "Нажми «➕ Добавить ПВЗ»."
        )
        return

    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=1
    )

    for item in items:
        keyboard.add(types.KeyboardButton(
            f"ПВЗ #{item[0]} — {item[1]}"
        ))

    keyboard.add("🏠 Главное меню")

    bot.send_message(
        message.chat.id,
        "Выбери пункт выдачи:",
        reply_markup=keyboard
    )


def add_pvz_name(message):
    if message.text == "❌ Отмена":
        main_menu(message)
        return

    name = message.text.strip()
    if not name:
        bot.send_message(message.chat.id, "Введи название ПВЗ.")
        bot.register_next_step_handler(message, add_pvz_name)
        return

    bot.register_next_step_handler(
        message,
        lambda msg: add_pvz_address(msg, name)
    )
    bot.send_message(
        message.chat.id,
        "Теперь введи адрес ПВЗ:"
    )


def add_pvz_address(message, name):
    if message.text == "❌ Отмена":
        main_menu(message)
        return

    address = message.text.strip()

    with db() as conn:
        conn.execute(
            "INSERT INTO pvz (user_id, name, address) VALUES (?, ?, ?)",
            (message.from_user.id, name, address)
        )

    bot.send_message(
        message.chat.id,
        f"✅ ПВЗ «{name}» успешно добавлен!"
    )
    main_menu(message)


def start_operation(message, kind):
    user_id = message.from_user.id
    pvz_id = selected_pvz.get(user_id)

    if not pvz_id:
        bot.send_message(
            message.chat.id,
            "Сначала выбери ПВЗ в разделе «Мои ПВЗ»."
        )
        return

    categories = INCOMES if kind == "income" else EXPENSES

    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )

    for category in categories:
        keyboard.add(types.KeyboardButton(category))

    keyboard.add("❌ Отмена")

    bot.send_message(
        message.chat.id,
        "Выбери категорию:",
        reply_markup=keyboard
    )

    bot.register_next_step_handler(
        message,
        lambda msg: operation_category(msg, kind)
    )


def operation_category(message, kind):
    if message.text == "❌ Отмена":
        main_menu(message)
        return

    categories = INCOMES if kind == "income" else EXPENSES

    if message.text not in categories:
        bot.send_message(message.chat.id, "Выбери категорию кнопкой.")
        bot.register_next_step_handler(
            message,
            lambda msg: operation_category(msg, kind)
        )
        return

    bot.register_next_step_handler(
        message,
        lambda msg: operation_amount(msg, kind, message.text)
    )

    bot.send_message(
        message.chat.id,
        f"Введи сумму в рублях за категорию «{message.text}»:"
    )


def operation_amount(message, kind, category):
    if message.text == "❌ Отмена":
        main_menu(message)
        return

    try:
        amount = float(
            message.text.replace(" ", "").replace(",", ".")
        )
        if amount <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❗ Введи корректную сумму, например 15000 или 15000.50."
        )
        bot.register_next_step_handler(
            message,
            lambda msg: operation_amount(msg, kind, category)
        )
        return

    user_id = message.from_user.id
    pvz_id = selected_pvz.get(user_id)

    if not pvz_id:
        bot.send_message(message.chat.id, "Сначала выбери ПВЗ заново.")
        main_menu(message)
        return

    with db() as conn:
        conn.execute("""
            INSERT INTO operations
            (user_id, pvz_id, kind, category, amount, month)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            pvz_id,
            kind,
            category,
            amount,
            date.today().strftime("%Y-%m")
        ))

    label = "доход" if kind == "income" else "расход"

    bot.send_message(
        message.chat.id,
        f"✅ {label.capitalize()} сохранён!\n\n"
        f"Категория: {category}\n"
        f"Сумма: {amount:,.2f} ₽"
    )
    pvz_menu(message.chat.id, user_id)


def report(user_id, chat_id, pvz_id=None):
    month = date.today().strftime("%Y-%m")

    with db() as conn:
        if pvz_id is None:
            rows = conn.execute("""
                SELECT kind, category, SUM(amount)
                FROM operations
                WHERE user_id=? AND month=?
                GROUP BY kind, category
            """, (user_id, month)).fetchall()
        else:
            rows = conn.execute("""
                SELECT kind, category, SUM(amount)
                FROM operations
                WHERE user_id=? AND pvz_id=? AND month=?
                GROUP BY kind, category
            """, (user_id, pvz_id, month)).fetchall()

    income = 0
    expense = 0
    income_lines = []
    expense_lines = []

    for kind, category, amount in rows:
        if kind == "income":
            income += amount
            income_lines.append(
                f"• {category}: {amount:,.2f} ₽"
            )
        else:
            expense += amount
            expense_lines.append(
                f"• {category}: {amount:,.2f} ₽"
            )

    profit = income - expense

    text = (
        f"📊 ФИНАНСОВЫЙ ОТЧЁТ\n"
        f"Период: {month}\n\n"
        f"💰 ДОХОДЫ:\n"
        + ("\n".join(income_lines) if income_lines else "Пока нет данных")
        + f"\n\nВсего доходов: {income:,.2f} ₽\n\n"
        f"💸 РАСХОДЫ:\n"
        + ("\n".join(expense_lines) if expense_lines else "Пока нет данных")
        + f"\n\nВсего расходов: {expense:,.2f} ₽\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 ПРИБЫЛЬ: {profit:,.2f} ₽"
    )

    bot.send_message(chat_id, text)


@bot.message_handler(commands=["start"])
def start(message):
    main_menu(message)


@bot.message_handler(func=lambda message: True)
def answer(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    if text == "🏠 Главное меню":
        main_menu(message)

    elif text == "🏢 Мои ПВЗ":
        choose_pvz(message)

    elif text == "➕ Добавить ПВЗ":
        bot.send_message(
            chat_id,
            "Введи название или номер своего ПВЗ:"
        )
        bot.register_next_step_handler(message, add_pvz_name)

    elif text == "📊 Общий отчёт":
        report(user_id, chat_id)

    elif text == "❓ Помощь":
        bot.send_message(
            chat_id,
            "WBPVZbot помогает вести финансы ПВЗ.\n\n"
            "1. Добавь свои пункты выдачи.\n"
            "2. Выбери нужный ПВЗ.\n"
            "3. Вноси доходы и расходы.\n"
            "4. Получай финансовые отчёты."
        )

    elif text.startswith("ПВЗ #"):
        try:
            pvz_id = int(text.split("#")[1].split("—")[0].strip())
            items = get_pvz(user_id)
            valid_ids = [item[0] for item in items]

            if pvz_id not in valid_ids:
                bot.send_message(chat_id, "Этот ПВЗ не найден.")
                return

            selected_pvz[user_id] = pvz_id
            pvz_menu(chat_id, user_id)
        except (ValueError, IndexError):
            bot.send_message(chat_id, "Не удалось выбрать ПВЗ.")

    elif text == "💰 Добавить доход":
        start_operation(message, "income")

    elif text == "💸 Добавить расход":
        start_operation(message, "expense")

    elif text == "📈 Отчёт ПВЗ":
        pvz_id = selected_pvz.get(user_id)
        if pvz_id:
            report(user_id, chat_id, pvz_id)
        else:
            choose_pvz(message)

    elif text == "🔄 Выбрать другой ПВЗ":
        choose_pvz(message)

    else:
        bot.send_message(
            chat_id,
            "Выбери действие с помощью кнопок меню 👇"
        )


print("WBPVZbot запущен!")
bot.infinity_polling()
