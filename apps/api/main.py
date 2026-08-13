import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import closing
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = os.getenv("DB_PATH", "support.db")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Список telegram_id операторов, кому разрешён доступ к miniapp. Задаётся в Environment на Render.
ALLOWED_OPERATORS = {x.strip() for x in os.getenv("OPERATOR_IDS", "").split(",") if x.strip()}
# DEV_MODE=1 позволяет тестировать miniapp локально в обычном браузере (без реальной подписи Telegram).
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with closing(get_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'open',
                assigned_operator_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                sender_type TEXT NOT NULL,
                text TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


init_db()


class IncomingMessage(BaseModel):
    chat_id: int
    sender_type: str  # всегда 'user' — сообщение от бота
    text: str


class OperatorMessage(BaseModel):
    text: str


def verify_operator(x_telegram_init_data: Optional[str] = Header(None)) -> int:
    """Проверяет initData Telegram WebApp, возвращает telegram_id оператора или кидает 401/403."""

    if DEV_MODE and x_telegram_init_data and x_telegram_init_data.startswith("dev:"):
        raw_id = x_telegram_init_data.split("dev:", 1)[1]
        if raw_id not in ALLOWED_OPERATORS:
            raise HTTPException(403, "Оператор не в списке доступа")
        return int(raw_id)

    if not x_telegram_init_data:
        raise HTTPException(401, "Нет данных авторизации Telegram (initData)")
    if not BOT_TOKEN:
        raise HTTPException(500, "На сервере не настроен BOT_TOKEN для проверки подписи")

    parsed = dict(parse_qsl(x_telegram_init_data, strict_parsing=False))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "Некорректные данные авторизации")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(401, "Подпись Telegram не совпадает")

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        raise HTTPException(401, "Сессия устарела, откройте miniapp заново из Telegram")

    user = json.loads(parsed.get("user", "{}"))
    operator_id = user.get("id")
    if operator_id is None:
        raise HTTPException(401, "Не удалось определить пользователя Telegram")

    if str(operator_id) not in ALLOWED_OPERATORS:
        raise HTTPException(403, "Этот Telegram-аккаунт не в списке операторов")

    return operator_id


# ---------- Эндпоинты бота (без operator-auth, используются только ботом) ----------


@app.post("/messages")
async def receive_user_message(msg: IncomingMessage):
    if msg.sender_type != "user":
        raise HTTPException(400, "Этот эндпоинт принимает только сообщения пользователя")
    with closing(get_db()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chats (chat_id, status) VALUES (?, 'open')",
            (msg.chat_id,),
        )
        # Новое сообщение от пользователя возвращает закрытый чат в очередь
        conn.execute(
            "UPDATE chats SET status = CASE WHEN status = 'closed' THEN 'open' ELSE status END, "
            "updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (msg.chat_id,),
        )
        conn.execute(
            "INSERT INTO messages (chat_id, sender_type, text, delivered) VALUES (?, 'user', ?, 1)",
            (msg.chat_id, msg.text),
        )
        conn.commit()
    return {"status": "ok"}


@app.get("/bot/pop-answers")
async def pop_answers():
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT id, chat_id, text FROM messages WHERE sender_type = 'operator' AND delivered = 0"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"UPDATE messages SET delivered = 1 WHERE id IN ({placeholders})", ids)
            conn.commit()
    return [{"chat_id": r["chat_id"], "text": r["text"]} for r in rows]


# ---------- Эндпоинты оператора (miniapp, требуют auth) ----------


@app.get("/chats")
async def list_chats(status: Optional[str] = None, operator_id: int = Depends(verify_operator)):
    query = "SELECT chat_id, status, assigned_operator_id FROM chats"
    params: tuple = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY updated_at DESC"

    with closing(get_db()) as conn:
        chats = conn.execute(query, params).fetchall()
        result = []
        for c in chats:
            last = conn.execute(
                "SELECT text FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                (c["chat_id"],),
            ).fetchone()
            result.append(
                {
                    "chat_id": c["chat_id"],
                    "status": c["status"],
                    "assigned_operator_id": c["assigned_operator_id"],
                    "is_mine": c["assigned_operator_id"] == operator_id,
                    "last_msg": last["text"] if last else "",
                }
            )
    return result


@app.get("/chats/{chat_id}/messages")
async def get_messages(chat_id: int, operator_id: int = Depends(verify_operator)):
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT sender_type, text, created_at FROM messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/chats/{chat_id}/claim")
async def claim_chat(chat_id: int, operator_id: int = Depends(verify_operator)):
    with closing(get_db()) as conn:
        cur = conn.execute(
            "UPDATE chats SET assigned_operator_id = ?, status = 'in_progress', updated_at = CURRENT_TIMESTAMP "
            "WHERE chat_id = ? AND assigned_operator_id IS NULL",
            (operator_id, chat_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            exists = conn.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
            if not exists:
                raise HTTPException(404, "Чат не найден")
            raise HTTPException(409, "Чат уже взят в работу другим оператором")
    return {"status": "claimed"}


@app.post("/chats/{chat_id}/release")
async def release_chat(chat_id: int, operator_id: int = Depends(verify_operator)):
    with closing(get_db()) as conn:
        cur = conn.execute(
            "UPDATE chats SET assigned_operator_id = NULL, status = 'open', updated_at = CURRENT_TIMESTAMP "
            "WHERE chat_id = ? AND assigned_operator_id = ?",
            (chat_id, operator_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "Чат закреплён не за вами")
    return {"status": "released"}


@app.post("/chats/{chat_id}/close")
async def close_chat(chat_id: int, operator_id: int = Depends(verify_operator)):
    with closing(get_db()) as conn:
        cur = conn.execute(
            "UPDATE chats SET status = 'closed', updated_at = CURRENT_TIMESTAMP "
            "WHERE chat_id = ? AND assigned_operator_id = ?",
            (chat_id, operator_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "Чат закреплён не за вами")
    return {"status": "closed"}


@app.post("/chats/{chat_id}/messages")
async def send_operator_message(
    chat_id: int, msg: OperatorMessage, operator_id: int = Depends(verify_operator)
):
    with closing(get_db()) as conn:
        chat = conn.execute(
            "SELECT assigned_operator_id, status FROM chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if chat is None:
            raise HTTPException(404, "Чат не найден")
        if chat["assigned_operator_id"] != operator_id:
            raise HTTPException(403, "Чат закреплён не за вами — сначала возьмите его в работу")
        if chat["status"] == "closed":
            raise HTTPException(409, "Чат закрыт")

        conn.execute(
            "INSERT INTO messages (chat_id, sender_type, text, delivered) VALUES (?, 'operator', ?, 0)",
            (chat_id, msg.text),
        )
        conn.execute("UPDATE chats SET updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?", (chat_id,))
        conn.commit()
    return {"status": "ok"}
