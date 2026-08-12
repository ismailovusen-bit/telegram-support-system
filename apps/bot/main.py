import asyncio
import os
from threading import Thread
from aiogram import Bot, Dispatcher, types
from flask import Flask

# Настройки токена (берется из переменных окружения на Render)
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# 1. Создаем мини-сайт для Render, чтобы он не отключал сервис
app = Flask("")


@app.route("/")
def home():
  return "Bot is alive and running!"


def run_web():
  port = int(
      os.environ.get("PORT", 10000)
  )  // Render автоматически передает порт в эту переменную
  app.run(host="0.0.0.0", port=port)


# Обработчик сообщений в боте
@dp.message()
async def echo_message(message: types.Message):
  # Здесь твоя логика ответа клиентам или пересылки
  await message.answer("Сообщение получено оператором.")


async def main():
  # Запускаем веб-сервер в фоновом потоке
  Thread(target=run_web).daemon = True
  Thread(target=run_web).start()

  # Запускаем бота
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
