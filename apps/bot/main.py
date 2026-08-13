import asyncio
import os
from threading import Thread

import httpx
from aiogram import Bot, Dispatcher, types
from flask import Flask

# Настройки (берутся из переменных окружения на Render)
TOKEN = os.getenv("BOT_TOKEN")
# URL API. Задайте API_URL в переменных окружения Render для бота,
# либо оставьте значение по умолчанию, если оно совпадает с адресом API.
API_URL = os.getenv("API_URL", "https://telegram-support-api.onrender.com")

bot = Bot(token=TOKEN)
dp = Dispatcher()
http_client = httpx.AsyncClient(timeout=10)

# 1. Создаем мини-сайт для Render, чтобы он не отключал сервис
app = Flask("")


@app.route("/")
def home():
    return "Bot is alive and running!"


def run_web():
    port = int(os.environ.get("PORT", 10000))  # Render автоматически передает порт в эту переменную
    app.run(host="0.0.0.0", port=port)


# Входящее сообщение пользователя -> пересылаем в API, чтобы оно появилось в miniapp
@dp.message()
async def handle_message(message: types.Message):
    try:
        await http_client.post(
            f"{API_URL}/messages",
            json={
                "chat_id": message.chat.id,
                "sender_type": "user",
                "text": message.text or "",
            },
        )
        await message.answer("Сообщение получено. Оператор скоро ответит.")
    except Exception as e:
        print(f"[bot] Не удалось отправить сообщение в API: {e}")
        await message.answer("Не удалось связаться с сервером поддержки, попробуйте позже.")


# Фоновая задача: забираем ответы операторов из API и шлём их пользователям в Telegram
async def poll_operator_answers():
    while True:
        try:
            resp = await http_client.get(f"{API_URL}/bot/pop-answers")
            resp.raise_for_status()
            for answer in resp.json():
                await bot.send_message(answer["chat_id"], answer["text"])
        except Exception as e:
            print(f"[bot] Ошибка при опросе ответов оператора: {e}")
        await asyncio.sleep(2)


async def main():
    # Запускаем веб-сервер в фоновом потоке (daemon, чтобы не мешал завершению процесса)
    Thread(target=run_web, daemon=True).start()

    # Запускаем опрос ответов операторов параллельно с ботом
    asyncio.create_task(poll_operator_answers())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
