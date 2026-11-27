import os
import re
import json
import uuid
import html
import sqlite3
from datetime import datetime, date

import telebot
from telebot import types

# ============================================================
# WBPVZbot — помощник владельцев и менеджеров ПВЗ Wildberries
# Python 3.10+
# requirements.txt: pyTelegramBotAPI==4.29.1
#
# Обязательные переменные Dada Cloud:
# BOT_TOKEN = токен бота от @BotFather
# ADMIN_IDS = Telegram ID администраторов через запятую
#
# Цифровые услуги внутри Telegram продаются за Telegram Stars:
# 1 бесплатный тикет на весь аккаунт, затем 5 Stars за тикет.
# ============================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("Не найдена переменная окружения BOT_TOKEN")

ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_NAME = os.getenv("DB_NAME", "wpbpvz.db")
TICKET_PRICE_STARS = 5

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
STATE = {}  # Незавершённые анкеты; оплаченные заказы и история хранятся в SQLite.

TICKET_TYPES = {
    "barcode": ("Зависший ШК", "проверку зависшего штрихкода"),
    "defect": ("Брак товара", "рассмотрение товара с выявленным браком"),
    "substitution": ("Подмена товара", "проверку предполагаемой подмены товара"),
}
INCOME_CATEGORIES = ["Выдача", "Приёмка", "Переупаковка", "Прочие доходы"]
EXPENSE_CATEGORIES = ["Аренда", "Коммунальные услуги", "Зарплаты", "Налоги", "Интернет", "Хозяйственные расходы", "Ремонт", "Прочие расходы"]


# ----------------------------- DB -----------------------------

def connect():
    con = sqlite3.connect(DB_NAME, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_period():
    return date.today().strftime("%Y-%m")


def init_db():
    with connect() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            role TEXT DEFAULT 'manager',
            free_ticket_used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pvz (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            area REAL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS finance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pvz_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT DEFAULT '',
            period TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pvz_id INTEGER,
            kind TEXT NOT NULL,
            details TEXT NOT NULL,
            generated_text TEXT NOT NULL,
            payment_type TEXT NOT NULL,
            stars INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL,
            pvz_id INTEGER,
            kind TEXT NOT NULL,
            details TEXT NOT NULL,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            charge_id TEXT UNIQUE,
            created_at TEXT NOT NULL,
            paid_at TEXT
        );

        CREATE TABLE IF NOT EXISTS support_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            service TEXT NOT NULL,
            details TEXT DEFAULT '',
            status TEXT DEFAULT 'Новая',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS motivation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            pvz_id INTEGER,
            period TEXT NOT NULL,
            timely_amount REAL NOT NULL,
            late_amount REAL NOT NULL DEFAULT 0,
            rate REAL NOT NULL,
            result REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        """)

        # Мягкая миграция существующей базы предыдущей версии.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
        if "free_ticket_used" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN free_ticket_used INTEGER NOT NULL DEFAULT 0")
        if "role" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'manager'")
        pcols = {r["name"] for r in con.execute("PRAGMA table_info(pvz)")}
        if "owner_id" not in pcols:
            con.execute("ALTER TABLE pvz ADD COLUMN owner_id INTEGER")
            if "user_id" in pcols:
                con.execute("UPDATE pvz SET owner_id=user_id WHERE owner_id IS NULL")
        con.execute("UPDATE pvz SET owner_id=user_id WHERE owner_id IS NULL") if "user_id" in pcols else None
        if "archived" not in pcols:
            con.execute("ALTER TABLE pvz ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")


def register_user(user):
    full_name = " ".join(x for x in [user.first_name, user.last_name] if x)
    with connect() as con:
        con.execute("""
            INSERT INTO users(user_id, username, full_name, created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username, full_name=excluded.full_name
        """, (user.id, user.username or "", full_name, now()))


def get_user(user_id):
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def is_admin(user_id):
    return user_id in ADMIN_IDS


def money(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def set_state(user_id, action, data=None):
    STATE[user_id] = {"action": action, "data": data or {}}


def get_state(user_id):
    return STATE.get(user_id)


def clear_state(user_id):
    STATE.pop(user_id, None)


# --------------------------- KEYBOARDS ------------------------

def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🧾 Помощник по тикетам", "🏢 Мои ПВЗ")
    kb.row("📊 Финансы", "💰 Мотивация")
    kb.row("📚 База знаний", "🚀 Открыть ПВЗ")
    kb.row("🆘 Оператор", "👤 Мой профиль")
    return kb


def back_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⬅️ Главное меню", "❌ Отмена")
    return kb


def inline_pvz_list(user_id, prefix="choosepvz", include_archived=False):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM pvz WHERE owner_id=? AND archived=? ORDER BY id DESC",
            (user_id, 1 if include_archived else 0)
        ).fetchall()
    kb = types.InlineKeyboardMarkup()
    for p in rows:
        kb.add(types.InlineKeyboardButton(
            p["name"], callback_data=f"{prefix}:{p['id']}"
        ))
    return kb, rows


def get_pvz(user_id, pvz_id, include_archived=False):
    with connect() as con:
        return con.execute(
            "SELECT * FROM pvz WHERE id=? AND owner_id=? AND archived=?",
            (pvz_id, user_id, 1 if include_archived else 0)
        ).fetchone()


def notify_admins(message_text):
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, message_text)
        except Exception:
            pass


def safe_send(chat_id, text, **kwargs):
    bot.send_message(chat_id, text, **kwargs)


# ------------------------------ START -------------------------

@bot.message_handler(commands=["start"])
def start(message):
    register_user(message.from_user)
    clear_state(message.from_user.id)
    payload = (message.text or "").partition(" ")[2].strip()
    # Реферальные ссылки будут включены отдельным этапом.
    welcome = (
        "Привет! Я <b>WBPVZbot</b> — помощник для собственников и менеджеров "
        "пунктов выдачи Wildberries.\n\n"
        "Помогаю подготовить обращения в поддержку, вести данные по ПВЗ, "
        "считать мотивацию и получать инструкции.\n\n"
        "🎁 <b>Первый тикет — бесплатно.</b>\n"
        "Следующие тикеты — <b>5 Telegram Stars</b> каждый."
    )
    send_home(message.chat.id, welcome)


@bot.message_handler(commands=["cancel"])
def cancel(message):
    clear_state(message.from_user.id)
    send_home(message.chat.id, "Действие отменено.")


@bot.message_handler(commands=["paysupport"])
def pay_support(message):
    register_user(message.from_user)
    set_state(message.from_user.id, "payment_support")
    bot.send_message(
        message.chat.id,
        "Опиши проблему с оплатой: дата, сумма в Stars и что произошло. "
        "Не отправляй пароли и коды подтверждения.",
        reply_markup=back_menu()
    )


@bot.message_handler(commands=["admin"])
def admin_command(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "Нет доступа к панели администратора.")
        return
    admin_menu(message.chat.id)


def send_home(chat_id, text="Выбери нужный раздел:"):
    bot.send_message(chat_id, text, reply_markup=main_menu())


# ------------------------------ PVZ ---------------------------

def pvz_menu(chat_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить ПВЗ", callback_data="pvz:add"))
    kb.add(types.InlineKeyboardButton("📋 Мои активные ПВЗ", callback_data="pvz:list"))
    kb.add(types.InlineKeyboardButton("🗄 Архивировать ПВЗ", callback_data="pvz:archive"))
    kb.add(types.InlineKeyboardButton("♻️ Восстановить ПВЗ", callback_data="pvz:restore"))
    bot.send_message(chat_id, "🏢 <b>Мои ПВЗ</b>", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pvz:"))
def pvz_actions(call):
    action = call.data.split(":")[1]
    bot.answer_callback_query(call.id)
    uid = call.from_user.id

    if action == "add":
        set_state(uid, "pvz_name")
        bot.send_message(call.message.chat.id, "Введи название или номер ПВЗ:", reply_markup=back_menu())
    elif action == "list":
        kb, rows = inline_pvz_list(uid)
        if not rows:
            bot.send_message(call.message.chat.id, "Пока нет активных ПВЗ.")
        else:
            for p in rows:
                bot.send_message(call.message.chat.id,
                    f"🏢 <b>{html.escape(p['name'])}</b>\n"
                    f"Адрес: {html.escape(p['address'])}\nПлощадь: {p['area']} м²")
    elif action in ("archive", "restore"):
        restoring = action == "restore"
        kb, rows = inline_pvz_list(uid, "restorepvz" if restoring else "archivepvz", restoring)
        if not rows:
            bot.send_message(call.message.chat.id, "Нет ПВЗ для этой операции.")
        else:
            bot.send_message(call.message.chat.id,
                "Выбери ПВЗ для восстановления:" if restoring else "Выбери ПВЗ для архивирования:",
                reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith(("archivepvz:", "restorepvz:")))
def pvz_archive_restore(call):
    prefix, raw_id = call.data.split(":")
    pvz_id = int(raw_id)
    archived = 0 if prefix == "restorepvz" else 1
    with connect() as con:
        cur = con.execute(
            "UPDATE pvz SET archived=? WHERE id=? AND owner_id=?",
            (archived, pvz_id, call.from_user.id)
        )
    bot.answer_callback_query(call.id, "Готово" if cur.rowcount else "ПВЗ не найден")
    bot.send_message(call.message.chat.id,
        "ПВЗ восстановлен." if archived == 0 else "ПВЗ помещён в архив. История не удалена.")


# --------------------------- TICKET FLOW ----------------------

def ticket_menu(chat_id):
    kb = types.InlineKeyboardMarkup()
    for key, (label, _) in TICKET_TYPES.items():
        kb.add(types.InlineKeyboardButton(label, callback_data=f"ticket:{key}"))
    bot.send_message(
        chat_id,
        "🧾 <b>Помощник по обращениям Wildberries</b>\n\n"
        "Выбери ситуацию. Первый тикет на аккаунт бесплатный, "
        "после него каждый тикет стоит 5 Telegram Stars.",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("ticket:"))
def ticket_type_selected(call):
    kind = call.data.split(":")[1]
    if kind not in TICKET_TYPES:
        bot.answer_callback_query(call.id, "Неизвестный тип обращения.")
        return
    bot.answer_callback_query(call.id)
    kb, rows = inline_pvz_list(call.from_user.id, "choosepvz")
    if not rows:
        set_state(call.from_user.id, "ticket_details", {"kind": kind, "pvz_id": None})
        bot.send_message(
            call.message.chat.id,
            "У тебя ещё нет добавленных ПВЗ. Напиши название и адрес ПВЗ одним сообщением, "
            "затем опиши ситуацию. Или сначала добавь ПВЗ в разделе «Мои ПВЗ»."
        )
        set_state(call.from_user.id, "ticket_no_pvz", {"kind": kind})
        return
    bot.send_message(call.message.chat.id, "Выбери ПВЗ, по которому создаём обращение:", reply_markup=kb)
    set_state(call.from_user.id, "ticket_choose_pvz", {"kind": kind})


@bot.callback_query_handler(func=lambda c: c.data.startswith("choosepvz:"))
def ticket_pvz_selected(call):
    pvz_id = int(call.data.split(":")[1])
    p = get_pvz(call.from_user.id, pvz_id)
    if not p:
        bot.answer_callback_query(call.id, "ПВЗ не найден.")
        return
    state = get_state(call.from_user.id)
    if not state or state["action"] != "ticket_choose_pvz":
        bot.answer_callback_query(call.id, "Начни создание тикета заново.")
        return
    data = state["data"]
    data["pvz_id"] = pvz_id
    data["pvz_name"] = p["name"]
    set_state(call.from_user.id, "ticket_details", data)
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "Опиши ситуацию подробно. По возможности укажи:\n"
        "• дату и время;\n• номер ШК или артикул;\n• что именно произошло;\n"
        "• какие действия уже предпринимались.\n\n"
        "Не отправляй ФИО, телефоны и другие персональные данные покупателей.",
        reply_markup=back_menu()
    )


def make_ticket_text(kind, details, pvz_name):
    label, purpose = TICKET_TYPES[kind]
    intro = {
        "barcode": "Просим проверить ситуацию с зависшим штрихкодом (ШК) и помочь корректно завершить операцию.",
        "defect": "Просим рассмотреть ситуацию с товаром, у которого обнаружены признаки брака, и разъяснить порядок дальнейших действий.",
        "substitution": "Просим провести проверку по факту предполагаемой подмены товара и разъяснить порядок дальнейших действий."
    }[kind]
    return (
        f"Тема: {label}\n"
        f"ПВЗ: {pvz_name}\n\n"
        f"Здравствуйте!\n{intro}\n\n"
        f"Описание ситуации:\n{details}\n\n"
        "Просим проверить информацию, предоставить разъяснение и сообщить дальнейший порядок действий.\n"
        "Спасибо."
    )


def deliver_ticket(user_id, chat_id, pvz_id, kind, details, payment_type, stars=0, pvz_name_override=None):
    if pvz_name_override:
        pvz_name = pvz_name_override
    elif pvz_id:
        p = get_pvz(user_id, pvz_id)
        pvz_name = p["name"] if p else "Не указан"
    else:
        pvz_name = "Не указан"
    generated = make_ticket_text(kind, details, pvz_name)

    with connect() as con:
        con.execute("""
            INSERT INTO tickets(user_id,pvz_id,kind,details,generated_text,payment_type,stars,created_at)
            VALUES(?,?,?,?,?,?,?,?)
        """, (user_id, pvz_id, kind, details, generated, payment_type, stars, now()))

    bot.send_message(chat_id, "✅ <b>Текст обращения подготовлен.</b>\nПроверь данные и при необходимости дополни их перед отправкой в поддержку WB.")
    bot.send_message(chat_id, f"<pre>{html.escape(generated)}</pre>")
    bot.send_message(chat_id, "История обращения сохранена в боте.", reply_markup=main_menu())


def process_ticket_request(user_id, chat_id, data, details):
    # Атомарно проверяем и используем единственную бесплатную попытку.
    # BEGIN IMMEDIATE не позволяет двум одновременным запросам забрать её дважды.
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        user = con.execute("SELECT free_ticket_used FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            con.execute(
                "INSERT INTO users(user_id,created_at) VALUES(?,?)",
                (user_id, now())
            )
            free_used = 0
        else:
            free_used = user["free_ticket_used"]

        if not free_used:
            con.execute("UPDATE users SET free_ticket_used=1 WHERE user_id=?", (user_id,))
            con.commit()
            deliver_ticket(user_id, chat_id, data.get("pvz_id"), data["kind"], details, "free", 0, data.get("pvz_name"))
            return

        con.commit()

    # Бесплатная попытка уже использована — создаём уникальный платёжный заказ.
    payload = uuid.uuid4().hex
    with connect() as con:
        con.execute("""
            INSERT INTO orders(payload,user_id,pvz_id,kind,details,amount,currency,status,created_at)
            VALUES(?,?,?,?,?,?,?,'pending',?)
        """, (payload, user_id, data.get("pvz_id"), data["kind"], details,
              TICKET_PRICE_STARS, "XTR", now()))

    label = TICKET_TYPES[data["kind"]][0]
    try:
        bot.send_invoice(
            chat_id=chat_id,
            title=f"Подготовка тикета: {label}",
            description="Автоматическая подготовка текста обращения для ПВЗ Wildberries.",
            invoice_payload=payload,
            provider_token="",
            currency="XTR",
            prices=[types.LabeledPrice(label="Один тикет", amount=TICKET_PRICE_STARS)],
            start_parameter=f"ticket_{payload[:12]}"
        )
        bot.send_message(chat_id, "После подтверждения оплаты бот автоматически подготовит и пришлёт тикет.")
    except Exception as exc:
        with connect() as con:
            con.execute("UPDATE orders SET status='invoice_error' WHERE payload=?", (payload,))
        bot.send_message(chat_id, "Не удалось выставить счёт. Бесплатная попытка не затронута. Попробуй ещё раз позже.")
        print("Invoice error:", repr(exc))


@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout_handler(query):
    # Telegram требует ответить на pre_checkout_query не позднее 10 секунд.
    with connect() as con:
        order = con.execute(
            "SELECT * FROM orders WHERE payload=? AND user_id=? AND status='pending'",
            (query.invoice_payload, query.from_user.id)
        ).fetchone()

    if (not order or query.currency != "XTR" or
            query.total_amount != TICKET_PRICE_STARS or
            order["amount"] != TICKET_PRICE_STARS):
        bot.answer_pre_checkout_query(
            query.id, ok=False,
            error_message="Счёт устарел или не совпадает с заказом. Вернись в бот и создай новый тикет."
        )
        return

    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def successful_payment_handler(message):
    payment = message.successful_payment
    payload = payment.invoice_payload
    charge_id = payment.telegram_payment_charge_id

    if payment.currency != "XTR" or payment.total_amount != TICKET_PRICE_STARS:
        bot.send_message(message.chat.id, "Платёж получен с неожиданными параметрами. Обратись к оператору: /paysupport")
        notify_admins(f"⚠️ Необычные параметры платежа от {message.from_user.id}: {payment.currency}, {payment.total_amount}")
        return

    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        order = con.execute(
            "SELECT * FROM orders WHERE payload=? AND user_id=?",
            (payload, message.from_user.id)
        ).fetchone()

        if not order:
            con.rollback()
            bot.send_message(message.chat.id, "Платёж получен, но заказ не найден. Напиши /paysupport.")
            notify_admins(f"⚠️ Оплата без заказа. Пользователь {message.from_user.id}, charge {charge_id}")
            return

        if order["status"] == "paid":
            con.commit()
            bot.send_message(message.chat.id, "Этот платёж уже обработан. Повторный тикет не создавался.")
            return

        con.execute(
            "UPDATE orders SET status='paid', charge_id=?, paid_at=? WHERE payload=?",
            (charge_id, now(), payload)
        )
        con.commit()

    deliver_ticket(
        message.from_user.id, message.chat.id, order["pvz_id"],
        order["kind"], order["details"], "stars", payment.total_amount
    )
    notify_admins(
        f"⭐ <b>Новая оплата WBPVZbot</b>\n"
        f"Пользователь: {message.from_user.id}\n"
        f"Услуга: {TICKET_TYPES[order['kind']][0]}\n"
        f"Сумма: {payment.total_amount} Stars\n"
        f"Telegram charge ID: <code>{charge_id}</code>"
    )


# ------------------------- SUPPORT / OPERATOR ----------------

def support_menu(chat_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🚀 Хочу открыть ПВЗ", callback_data="support:opening"))
    kb.add(types.InlineKeyboardButton("💬 Связаться с оператором", callback_data="support:operator"))
    bot.send_message(
        chat_id,
        "🆘 <b>Помощь и сопровождение</b>\n"
        "Оставь заявку. Оператор свяжется с тобой и согласует условия до начала платной услуги.",
        reply_markup=kb
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("support:"))
def support_start(call):
    service = "Сопровождение открытия ПВЗ" if call.data.endswith("opening") else "Связь с оператором"
    set_state(call.from_user.id, "support_details", {"service": service})
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"Услуга: <b>{service}</b>\n\n"
        "Напиши город и кратко опиши, какая помощь нужна. "
        "Не отправляй пароли, коды и данные банковских карт.",
        reply_markup=back_menu()
    )


def save_support_request(message, service, details):
    user = message.from_user
    full_name = " ".join(x for x in [user.first_name, user.last_name] if x)
    with connect() as con:
        cur = con.execute("""
            INSERT INTO support_requests(user_id,username,full_name,service,details,created_at)
            VALUES(?,?,?,?,?,?)
        """, (user.id, user.username or "", full_name, service, details, now()))
        request_id = cur.lastrowid
    notify_admins(
        f"🆕 <b>Заявка #{request_id}</b>\n"
        f"Услуга: {service}\nКлиент: {html.escape(full_name)} (@{user.username or 'нет'})\n"
        f"Telegram ID: <code>{user.id}</code>\nОписание: {html.escape(details)}\n\n"
        "Открой /admin для обработки."
    )
    bot.send_message(message.chat.id,
        f"✅ Заявка #{request_id} принята. Оператор свяжется с тобой в Telegram.",
        reply_markup=main_menu())


# ------------------------------ FINANCE -----------------------

def finance_menu(chat_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить доход/расход", callback_data="finance:add"))
    kb.add(types.InlineKeyboardButton("📊 Отчёт за текущий месяц", callback_data="finance:report"))
    bot.send_message(chat_id, "📊 <b>Финансы ПВЗ</b>", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("finance:"))
def finance_actions(call):
    action = call.data.split(":")[1]
    bot.answer_callback_query(call.id)
    kb, rows = inline_pvz_list(call.from_user.id, "financepvz")
    if not rows:
        bot.send_message(call.message.chat.id, "Сначала добавь ПВЗ в разделе «Мои ПВЗ».")
        return
    if action == "report":
        set_state(call.from_user.id, "finance_report_choose")
        bot.send_message(call.message.chat.id, "Выбери ПВЗ для отчёта:", reply_markup=kb)
    elif action == "add":
        set_state(call.from_user.id, "finance_choose_pvz")
        bot.send_message(call.message.chat.id, "Выбери ПВЗ:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("financepvz:"))
def finance_pvz_callback(call):
    pvz_id = int(call.data.split(":")[1])
    p = get_pvz(call.from_user.id, pvz_id)
    state = get_state(call.from_user.id)
    if not p or not state:
        bot.answer_callback_query(call.id, "ПВЗ не найден.")
        return
    bot.answer_callback_query(call.id)
    data = {"pvz_id": pvz_id}
    if state["action"] == "finance_report_choose":
        clear_state(call.from_user.id)
        send_finance_report(call.message.chat.id, call.from_user.id, pvz_id)
    else:
        set_state(call.from_user.id, "finance_kind", data)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("➕ Доход", callback_data="fin:income"))
        kb.add(types.InlineKeyboardButton("➖ Расход", callback_data="fin:expense"))
        bot.send_message(call.message.chat.id, "Выбери тип записи:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fin:"))
def finance_kind_callback(call):
    state = get_state(call.from_user.id)
    if not state:
        bot.answer_callback_query(call.id, "Начни заново.")
        return
    kind = call.data.split(":")[1]
    categories = INCOME_CATEGORIES if kind == "income" else EXPENSE_CATEGORIES
    set_state(call.from_user.id, "finance_category", {"pvz_id": state["data"]["pvz_id"], "kind": kind})
    kb = types.InlineKeyboardMarkup()
    for i, cat in enumerate(categories):
        kb.add(types.InlineKeyboardButton(cat, callback_data=f"fcat:{i}"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Выбери категорию:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("fcat:"))
def finance_category_callback(call):
    state = get_state(call.from_user.id)
    if not state:
        bot.answer_callback_query(call.id, "Начни заново.")
        return
    kind = state["data"]["kind"]
    categories = INCOME_CATEGORIES if kind == "income" else EXPENSE_CATEGORIES
    category = categories[int(call.data.split(":")[1])]
    data = dict(state["data"])
    data["category"] = category
    set_state(call.from_user.id, "finance_amount", data)
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Введи сумму в рублях:", reply_markup=back_menu())


def send_finance_report(chat_id, user_id, pvz_id):
    period = current_period()
    with connect() as con:
        rows = con.execute("""
            SELECT kind, SUM(amount) total FROM finance
            WHERE user_id=? AND pvz_id=? AND period=? GROUP BY kind
        """, (user_id, pvz_id, period)).fetchall()
        details = con.execute("""
            SELECT kind,category,SUM(amount) total FROM finance
            WHERE user_id=? AND pvz_id=? AND period=?
            GROUP BY kind,category ORDER BY kind,category
        """, (user_id, pvz_id, period)).fetchall()
    totals = {r["kind"]: r["total"] or 0 for r in rows}
    inc, exp = totals.get("income", 0), totals.get("expense", 0)
    p = get_pvz(user_id, pvz_id)
    out = f"📊 <b>Отчёт за {period}</b>\nПВЗ: {html.escape(p['name'] if p else '')}\n\n"
    out += f"Доходы: {money(inc)}\nРасходы: {money(exp)}\n<b>Разница: {money(inc-exp)}</b>\n"
    if details:
        out += "\n<b>По категориям:</b>\n"
        for r in details:
            label = "Доход" if r["kind"] == "income" else "Расход"
            out += f"• {label}, {r['category']}: {money(r['total'])}\n"
    bot.send_message(chat_id, out)


# ---------------------------- MOTIVATION ----------------------

def motivation_start(chat_id, user_id):
    kb, rows = inline_pvz_list(user_id, "motpvz")
    if not rows:
        bot.send_message(chat_id, "Сначала добавь ПВЗ.")
        return
    set_state(user_id, "motivation_period", {})
    bot.send_message(chat_id, "Выбери ПВЗ:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("motpvz:"))
def motivation_pvz_callback(call):
    pvz_id = int(call.data.split(":")[1])
    if not get_pvz(call.from_user.id, pvz_id):
        bot.answer_callback_query(call.id, "ПВЗ не найден.")
        return
    bot.answer_callback_query(call.id)
    set_state(call.from_user.id, "motivation_period", {"pvz_id": pvz_id})
    bot.send_message(call.message.chat.id, "Введи период в формате ГГГГ-ММ, например 2026-11:", reply_markup=back_menu())


# ------------------------------ KNOWLEDGE ---------------------

def knowledge_menu(chat_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📦 Приёмка и зависшие ШК", callback_data="know:barcode"))
    kb.add(types.InlineKeyboardButton("🛠 Брак и подмены", callback_data="know:defect"))
    kb.add(types.InlineKeyboardButton("👥 Работа с клиентами", callback_data="know:clients"))
    kb.add(types.InlineKeyboardButton("⭐ Отзывы и рейтинг", callback_data="know:reviews"))
    bot.send_message(chat_id, "📚 <b>База знаний ПВЗ</b>\nВыбери тему:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("know:"))
def knowledge_callback(call):
    content = {
        "barcode": "При проблеме с зависшим ШК зафиксируй дату и время, номер ШК, статус операции и действия, которые уже предпринимались. Не закрывай обращение без проверки результата.",
        "defect": "При обнаружении брака зафиксируй состояние товара и упаковки в соответствии с правилами WB. Не обещай покупателю решение, которое не зависит от ПВЗ. Используй официальный порядок оформления.",
        "clients": "Общайся спокойно и нейтрально. Не спорь с покупателем, не раскрывай персональные данные и не обещай исключения из правил площадки.",
        "reviews": "Отвечай по существу, без персональных данных и обвинений. Если отзыв содержит недостоверные сведения, собери факты и используй предусмотренный WB способ обжалования."
    }
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, content.get(call.data.split(":")[1], "Материал не найден."))


# ------------------------------ ADMIN -------------------------

def admin_menu(chat_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📨 Заявки оператору", callback_data="admin:requests"))
    kb.add(types.InlineKeyboardButton("👥 Количество пользователей", callback_data="admin:users"))
    kb.add(types.InlineKeyboardButton("🏢 Все ПВЗ", callback_data="admin:pvz"))
    kb.add(types.InlineKeyboardButton("⭐ Статистика тикетов и Stars", callback_data="admin:stats"))
    bot.send_message(chat_id, "🔐 <b>Административная панель WBPVZbot</b>", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin:"))
def admin_actions(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Нет доступа.")
        return
    action = call.data.split(":")[1]
    bot.answer_callback_query(call.id)

    with connect() as con:
        if action == "users":
            count = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            bot.send_message(call.message.chat.id, f"Зарегистрировано пользователей: <b>{count}</b>")
        elif action == "pvz":
            rows = con.execute("""
                SELECT p.*,u.full_name,u.username FROM pvz p
                LEFT JOIN users u ON u.user_id=p.owner_id ORDER BY p.id DESC LIMIT 50
            """).fetchall()
            if not rows:
                bot.send_message(call.message.chat.id, "ПВЗ пока нет.")
            for p in rows:
                bot.send_message(call.message.chat.id,
                    f"ПВЗ #{p['id']}: {html.escape(p['name'])}\n{html.escape(p['address'])}\n"
                    f"Владелец: {html.escape(p['full_name'] or '')} (@{p['username'] or ''})")
        elif action == "requests":
            rows = con.execute("SELECT * FROM support_requests ORDER BY id DESC LIMIT 30").fetchall()
            if not rows:
                bot.send_message(call.message.chat.id, "Заявок пока нет.")
            for r in rows:
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✉️ Ответить", callback_data=f"adminreply:{r['id']}"))
                bot.send_message(call.message.chat.id,
                    f"Заявка #{r['id']} — <b>{r['status']}</b>\n"
                    f"Услуга: {html.escape(r['service'])}\n"
                    f"Клиент: {html.escape(r['full_name'])} (@{r['username']})\n"
                    f"ID: <code>{r['user_id']}</code>\nОписание: {html.escape(r['details'])}",
                    reply_markup=kb)
        elif action == "stats":
            tickets = con.execute("SELECT COUNT(*) n FROM tickets").fetchone()["n"]
            free = con.execute("SELECT COUNT(*) n FROM tickets WHERE payment_type='free'").fetchone()["n"]
            paid = con.execute("SELECT COUNT(*) n FROM tickets WHERE payment_type='stars'").fetchone()["n"]
            stars = con.execute("SELECT COALESCE(SUM(stars),0) n FROM tickets WHERE payment_type='stars'").fetchone()["n"]
            pending = con.execute("SELECT COUNT(*) n FROM orders WHERE status='pending'").fetchone()["n"]
            bot.send_message(call.message.chat.id,
                f"📊 <b>Статистика</b>\nВсего тикетов: {tickets}\nБесплатных: {free}\n"
                f"Оплаченных: {paid}\nПолучено Stars: {stars}\nНезавершённых счетов: {pending}")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adminreply:"))
def admin_reply_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Нет доступа.")
        return
    request_id = int(call.data.split(":")[1])
    set_state(call.from_user.id, "admin_reply", {"request_id": request_id})
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, f"Напиши ответ пользователю по заявке #{request_id}:")


# ---------------------------- PROFILE -------------------------

def profile(chat_id, user_id):
    user = get_user(user_id)
    with connect() as con:
        pvz_count = con.execute("SELECT COUNT(*) n FROM pvz WHERE owner_id=? AND archived=0", (user_id,)).fetchone()["n"]
        ticket_count = con.execute("SELECT COUNT(*) n FROM tickets WHERE user_id=?", (user_id,)).fetchone()["n"]
        paid_count = con.execute("SELECT COUNT(*) n FROM tickets WHERE user_id=? AND payment_type='stars'", (user_id,)).fetchone()["n"]
    free_status = "уже использован" if user and user["free_ticket_used"] else "доступен"
    bot.send_message(chat_id,
        f"👤 <b>Мой профиль</b>\n"
        f"Роль: {user['role'] if user else 'пользователь'}\n"
        f"Активных ПВЗ: {pvz_count}\n"
        f"Подготовлено тикетов: {ticket_count}\n"
        f"Из них оплачено: {paid_count}\n"
        f"Бесплатный тикет: {free_status}\n"
        f"Цена следующего тикета: {TICKET_PRICE_STARS} Stars")


# ------------------------------ TEXT INPUT --------------------

@bot.message_handler(content_types=["text"])
def text_router(message):
    uid = message.from_user.id
    text = (message.text or "").strip()
    register_user(message.from_user)

    if text == "❌ Отмена":
        clear_state(uid)
        send_home(message.chat.id, "Действие отменено.")
        return
    if text == "⬅️ Главное меню":
        clear_state(uid)
        send_home(message.chat.id)
        return

    if text == "🧾 Помощник по тикетам":
        clear_state(uid)
        ticket_menu(message.chat.id)
        return
    if text == "🏢 Мои ПВЗ":
        clear_state(uid)
        pvz_menu(message.chat.id)
        return
    if text == "📊 Финансы":
        clear_state(uid)
        finance_menu(message.chat.id)
        return
    if text == "💰 Мотивация":
        clear_state(uid)
        motivation_start(message.chat.id, uid)
        return
    if text == "📚 База знаний":
        clear_state(uid)
        knowledge_menu(message.chat.id)
        return
    if text == "🚀 Открыть ПВЗ":
        clear_state(uid)
        support_menu(message.chat.id)
        return
    if text == "🆘 Оператор":
        clear_state(uid)
        support_menu(message.chat.id)
        return
    if text == "👤 Мой профиль":
        clear_state(uid)
        profile(message.chat.id, uid)
        return

    state = get_state(uid)
    if not state:
        send_home(message.chat.id, "Выбери раздел в меню.")
        return

    action = state["action"]
    data = state["data"]

    if action == "pvz_name":
        data["name"] = text
        set_state(uid, "pvz_address", data)
        bot.send_message(message.chat.id, "Введи адрес ПВЗ:", reply_markup=back_menu())

    elif action == "pvz_address":
        data["address"] = text
        set_state(uid, "pvz_area", data)
        bot.send_message(message.chat.id, "Введи площадь в м². Если не знаешь — введи 0:", reply_markup=back_menu())

    elif action == "pvz_area":
        try:
            area = float(text.replace(",", "."))
            if area < 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введи число, например 45 или 45,5.")
            return
        with connect() as con:
            con.execute(
                "INSERT INTO pvz(owner_id,name,address,area,created_at) VALUES(?,?,?,?,?)",
                (uid, data["name"], data["address"], area, now())
            )
        clear_state(uid)
        send_home(message.chat.id, f"✅ ПВЗ <b>{html.escape(data['name'])}</b> добавлен.")

    elif action == "ticket_details":
        if len(text) < 5:
            bot.send_message(message.chat.id, "Добавь немного подробностей, чтобы обращение получилось содержательным.")
            return
        clear_state(uid)
        process_ticket_request(uid, message.chat.id, data, text)

    elif action == "ticket_no_pvz":
        data["pvz_name"] = text
        set_state(uid, "ticket_no_pvz_details", data)
        bot.send_message(message.chat.id, "Теперь опиши саму ситуацию, укажи дату и номер ШК/артикула, если есть.")

    elif action == "ticket_no_pvz_details":
        clear_state(uid)
        data["pvz_id"] = None
        data["pvz_name"] = data.get("pvz_name", "Не указан")
        process_ticket_request(uid, message.chat.id, data, text)

    elif action == "finance_amount":
        try:
            amount = float(text.replace(" ", "").replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введи положительную сумму числом.")
            return
        data["amount"] = amount
        set_state(uid, "finance_note", data)
        bot.send_message(message.chat.id, "Комментарий к записи или «-», если не нужен:", reply_markup=back_menu())

    elif action == "finance_note":
        note = "" if text == "-" else text
        with connect() as con:
            con.execute("""
                INSERT INTO finance(user_id,pvz_id,kind,category,amount,note,period,created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (uid, data["pvz_id"], data["kind"], data["category"], data["amount"], note, current_period(), now()))
        clear_state(uid)
        send_home(message.chat.id, f"✅ Запись сохранена: {data['category']} — {money(data['amount'])}.")

    elif action == "motivation_period":
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
            bot.send_message(message.chat.id, "Формат периода: ГГГГ-ММ, например 2026-11.")
            return
        data["period"] = text
        set_state(uid, "motivation_timely", data)
        bot.send_message(message.chat.id, "Введи сумму товаров, принятых ВОВРЕМЯ (St), в рублях:")

    elif action == "motivation_timely":
        try:
            data["timely"] = float(text.replace(" ", "").replace(",", "."))
            if data["timely"] < 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введи неотрицательную сумму.")
            return
        set_state(uid, "motivation_late", data)
        bot.send_message(message.chat.id, "Введи сумму товаров, принятых ПОЗДНО. Если нет — 0:")

    elif action == "motivation_late":
        try:
            data["late"] = float(text.replace(" ", "").replace(",", "."))
            if data["late"] < 0:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введи неотрицательную сумму.")
            return
        set_state(uid, "motivation_rate", data)
        bot.send_message(message.chat.id, "Введи коэффициент k в процентах, например 1 для 1%:")

    elif action == "motivation_rate":
        try:
            rate = float(text.replace(",", "."))
            if rate < 0 or rate > 100:
                raise ValueError
        except ValueError:
            bot.send_message(message.chat.id, "Введи процент от 0 до 100.")
            return
        result = data["timely"] * rate / 100
        with connect() as con:
            con.execute("""
                INSERT INTO motivation(user_id,pvz_id,period,timely_amount,late_amount,rate,result,created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (uid, data["pvz_id"], data["period"], data["timely"], data["late"], rate, result, now()))
        clear_state(uid)
        bot.send_message(message.chat.id,
            f"💰 <b>Расчёт мотивации за {data['period']}</b>\n\n"
            f"Вовремя (St): {money(data['timely'])}\n"
            f"Поздно: {money(data['late'])}\n"
            f"Коэффициент: {rate:g}%\n"
            f"Формула: Вк = St × k\n"
            f"<b>Расчётная сумма: {money(result)}</b>\n\n"
            "Это расчёт по введённым данным, а не подтверждение начисления Wildberries.")

    elif action == "support_details":
        clear_state(uid)
        save_support_request(message, data["service"], text)

    elif action == "payment_support":
        clear_state(uid)
        save_support_request(message, "Вопрос по оплате Telegram Stars", text)

    elif action == "admin_reply":
        if not is_admin(uid):
            clear_state(uid)
            bot.send_message(message.chat.id, "Нет доступа.")
            return
        request_id = data["request_id"]
        with connect() as con:
            req = con.execute("SELECT * FROM support_requests WHERE id=?", (request_id,)).fetchone()
            if req:
                con.execute("UPDATE support_requests SET status='Ответ отправлен' WHERE id=?", (request_id,))
        clear_state(uid)
        if req:
            try:
                bot.send_message(req["user_id"], f"💬 <b>Ответ оператора по заявке #{request_id}:</b>\n\n{html.escape(text)}")
                bot.send_message(message.chat.id, "Ответ отправлен.")
            except Exception:
                bot.send_message(message.chat.id, "Не удалось доставить ответ пользователю.")
        else:
            bot.send_message(message.chat.id, "Заявка не найдена.")

    else:
        clear_state(uid)
        send_home(message.chat.id, "Сценарий сброшен. Выбери раздел.")


# ------------------------------ RUN ----------------------------

init_db()
print("WBPVZbot started. Telegram Stars price:", TICKET_PRICE_STARS)
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
