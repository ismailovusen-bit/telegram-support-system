import asyncio
import logging
import httpx
from aiogram import Bot, Dispatcher, types

# Вставь сюда свежий токен из BotFather
RAW_TOKEN = "8645806834:AAFifGe729wTs2ZIUNp9vIXOvQCCqiqD1do"
API_URL = "http://127.0.0.1:8000"

dp = Dispatcher()

@dp.message()
async def handle_user_message(message: types.Message):
    async with httpx.AsyncClient() as client:
        await client.post(f"{API_URL}/messages", json={
            "chat_id": message.from_user.id,
            "sender_type": "user",
            "text": message.text
        })

async def poll_operator_answers(bot: Bot):
    while True:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{API_URL}/bot/pop-answers")
                if res.status_code == 200:
                    for ans in res.json():
                        await bot.send_message(
                            chat_id=ans["chat_id"], 
                            text=f"👨‍💻 Ответ оператора:\n{ans['text']}"
                        )
        except Exception:
            pass
        await asyncio.sleep(2)

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Очистка токена от пробелов и кавычек
    clean_token = RAW_TOKEN.strip().strip("'").strip('"')
    bot = Bot(token=clean_token)
    
    asyncio.create_task(poll_operator_answers(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())