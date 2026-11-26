
import telebot
import os

TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "Привет! Я WBPVZbot 🤖\n\n"
        "Помощник владельца Wildberries ПВЗ.\n\n"
        "Выбери, чем я могу помочь:\n"
        "1. Управление ПВЗ\n"
        "2. Расчёт мотивации\n"
        "3. Помощь сотрудникам\n"
        "4. Советы владельцу ПВЗ"
    )

@bot.message_handler(func=lambda message: True)
def answer(message):
    bot.reply_to(
        message,
        "Раздел находится в разработке. Скоро здесь появятся полезные инструменты!"
    )

bot.infinity_polling()
