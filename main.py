import telebot
import os
from telebot import types

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Не найден BOT_TOKEN")

bot = telebot.TeleBot(TOKEN)


@bot.message_handler(commands=['start'])
def start(message):
    keyboard = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )

    keyboard.add(
        types.KeyboardButton("🏢 Управление ПВЗ"),
        types.KeyboardButton("💰 Расчёт мотивации"),
        types.KeyboardButton("👥 Помощь сотрудникам"),
        types.KeyboardButton("📊 Советы владельцу")
    )

    bot.send_message(
        message.chat.id,
        "Привет! Я WBPVZbot 🤖\n\n"
        "Помощник владельца Wildberries ПВЗ.\n\n"
        "Выбери нужный раздел:",
        reply_markup=keyboard
    )


@bot.message_handler(func=lambda message: True)
def answer(message):

    sections = {
        "🏢 Управление ПВЗ":
            "🏢 Управление ПВЗ\n\n"
            "Здесь будут инструменты управления пунктом выдачи.",

        "💰 Расчёт мотивации":
            "💰 Расчёт мотивации\n\n"
            "Здесь будет автоматический расчёт мотивации сотрудников.",

        "👥 Помощь сотрудникам":
            "👥 Помощь сотрудникам\n\n"
            "Здесь будут инструкции и ответы на вопросы сотрудников.",

        "📊 Советы владельцу":
            "📊 Советы владельцу\n\n"
            "Здесь будут полезные советы и анализ работы ПВЗ."
    }

    response = sections.get(
        message.text,
        "Выбери раздел с помощью кнопок меню 👇"
    )

    bot.send_message(message.chat.id, response)


bot.infinity_polling()
