from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Хранилище в памяти для быстрого старта без сломанных БД
CHATS = {}      # {chat_id: {"client_id": int, "messages": []}}
PENDING_ANSWERS = [] # Очередь ответов для бота

class MessageSchema(BaseModel):
    chat_id: int
    sender_type: str # 'user' или 'operator'
    text: str

@app.get("/chats")
async def get_chats():
    return [{"chat_id": cid, "last_msg": data["messages"][-1]["text"] if data["messages"] else ""} 
            for cid, data in CHATS.items()]

@app.get("/chats/{chat_id}/messages")
async def get_messages(chat_id: int):
    if chat_id not in CHATS:
        return []
    return CHATS[chat_id]["messages"]

@app.post("/messages")
async def send_message(msg: MessageSchema):
    if msg.chat_id not in CHATS:
        CHATS[msg.chat_id] = {"client_id": msg.chat_id, "messages": []}
    
    item = {"sender_type": msg.sender_type, "text": msg.text}
    CHATS[msg.chat_id]["messages"].append(item)
    
    # Если ответил оператор — кладем в очередь для отправки в Telegram
    if msg.sender_type == "operator":
        PENDING_ANSWERS.append({"chat_id": msg.chat_id, "text": msg.text})
        
    return {"status": "ok"}

@app.get("/bot/pop-answers")
async def pop_answers():
    global PENDING_ANSWERS
    answers = PENDING_ANSWERS.copy()
    PENDING_ANSWERS = []
    return answers