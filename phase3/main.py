from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, Any
import mysql.connector
from mysql.connector import pooling
import os
from dotenv import load_dotenv

load_dotenv()

db_config = {
    "user": os.getenv("DB_USER", "app_user"),
    "password": os.getenv("DB_PASS", "app-pass"),
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "arena"),
}

connection_pool = pooling.MySQLConnectionPool(
    pool_name="main_pool",
    pool_size=5,
    **db_config
)

def get_db():
    conn = connection_pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


class ConnectionManager:
    def __init__(self):
        # uid -> websocket
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, uid: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[uid] = websocket

    def disconnect(self, uid: str):
        if uid in self.active_connections:
            del self.active_connections[uid]

    async def send_personal(self, uid: str, message: Any):
        ws = self.active_connections.get(uid)
        if ws:
            await ws.send_json(message)

    async def broadcast(self, message: Any):
        for connection in self.active_connections.values():
            await connection.send_json(message)


manager = ConnectionManager()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/users")
def get_online_users(db=Depends(get_db)):
    db.execute(
        "SELECT uid, name, elo_rating FROM users WHERE is_online = TRUE"
    )
    return db.fetchall()


@app.websocket("/ws/{uid}")
async def websocket_endpoint(websocket: WebSocket, uid: str):
    await manager.connect(uid, websocket)

    conn = connection_pool.get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # check uid exists
        cursor.execute("SELECT uid FROM users WHERE uid = %s", (uid,))
        if cursor.fetchone() is None:
            await websocket.close()
            return

        cursor.execute(
            "UPDATE users SET is_online = TRUE WHERE uid = %s",
            (uid,)
        )
        conn.commit()

        cursor.execute(
            "SELECT uid, name, elo_rating FROM users WHERE is_online = TRUE"
        )
        users = cursor.fetchall()

        await manager.broadcast({
            "type": "users",
            "data": users
        })

        while True:
            data = await websocket.receive_json()

            # -------- challenge request --------
            if data["type"] == "challenge":
                target = data["to"]

                await manager.send_personal(
                    target,
                    {
                        "type": "challenge",
                        "from": uid
                    }
                )

            # -------- challenge accepted --------
            elif data["type"] == "challenge_accept":
                target = data["to"]

                await manager.send_personal(
                    target,
                    {
                        "type": "challenge_accepted",
                        "by": uid
                    }
                )

            # -------- challenge declined --------
            elif data["type"] == "challenge_decline":
                target = data["to"]

                await manager.send_personal(
                    target,
                    {
                        "type": "challenge_declined",
                        "by": uid
                    }
                )

    except WebSocketDisconnect:
        manager.disconnect(uid)

        cursor.execute(
            "UPDATE users SET is_online = FALSE WHERE uid = %s",
            (uid,)
        )
        conn.commit()

        cursor.execute(
            "SELECT uid, name, elo_rating FROM users WHERE is_online = TRUE"
        )
        users = cursor.fetchall()

        await manager.broadcast({
            "type": "users",
            "data": users
        })

    finally:
        cursor.close()
        conn.close() 