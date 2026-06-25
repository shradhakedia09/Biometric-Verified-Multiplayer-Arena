import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import mysql.connector
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from phase2.authenticator import authenticate_face, get_last_auth_error, build_encodings_cache_at_startup


load_dotenv()
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
LOGIN_HTML = BASE_DIR / "phase2" / "login.html"
DASHBOARD_HTML = BASE_DIR / "phase3" / "Dashboard" / "dashboard.html"
DASHBOARD_CSS = BASE_DIR / "phase3" / "Dashboard" / "dashboard.css"
DASHBOARD_JS = BASE_DIR / "phase3" / "Dashboard" / "dashboard.js"
ROOM_HTML = BASE_DIR / "phase3" / "Room" / "[roomid]" / "index.html"
ROOM_CSS = BASE_DIR / "phase3" / "Room" / "[roomid]" / "room.css"
ROOM_JS = BASE_DIR / "phase3" / "Room" / "[roomid]" / "room.js"
LEADERBOARD_HTML = BASE_DIR / "phase4" / "LeaderBoard.html"
LEADERBOARD_CSS = BASE_DIR / "phase4" / "LeaderBoard.css"
LEADERBOARD_JS = BASE_DIR / "phase4" / "LeaderBoard.js"


def db_config() -> dict[str, str]:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "user": os.getenv("DB_USER", "app_user"),
        "password": os.getenv("DB_PASS", "app_pass"),
        "database": os.getenv("DB_NAME", "arena"),
    }


def get_db_connection():
    return mysql.connector.connect(**db_config())


def get_user_profile(uid: str) -> dict[str, Any] | None:
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT uid, name, elo_rating FROM users WHERE uid = %s",
            (uid,),
        )
        return cursor.fetchone()
    except Exception as exc:
        logger.error("Could not fetch profile for %s: %s", uid, exc)
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def update_online(uid: str, is_online: bool) -> None:
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET is_online = %s WHERE uid = %s",
            (is_online, uid),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Failed to update is_online for %s: %s", uid, exc)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def fetch_online_users() -> list[dict[str, Any]]:
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT uid, name, elo_rating FROM users WHERE is_online = TRUE"
        )
        return cursor.fetchall()
    except Exception as exc:
        logger.error("Failed to fetch users: %s", exc)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def fetch_leaderboard() -> list[dict[str, Any]]:
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT uid, name, elo_rating, is_online "
            "FROM users ORDER BY COALESCE(elo_rating, 1200) DESC, uid ASC"
        )
        leaderboard: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            leaderboard.append(
                {
                    "uid": str(row.get("uid") or ""),
                    "name": str(row.get("name") or row.get("uid") or ""),
                    "elo_rating": int(row.get("elo_rating") or 1200),
                    "is_online": bool(row.get("is_online")),
                }
            )
        return leaderboard
    except Exception as exc:
        logger.error("Failed to fetch leaderboard: %s", exc)
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def user_exists(uid: str) -> bool:
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE uid = %s", (uid,))
        return cursor.fetchone() is not None
    except Exception as exc:
        logger.error("Could not verify user %s: %s", uid, exc)
        return False
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def build_room_id(uid_a: str, uid_b: str) -> str:
    left, right = sorted([uid_a, uid_b])
    return f"{left}V{right}"


def inject_bootstrap(
    html_text: str,
    replacements: dict[str, Any],
    script_tag: str,
    overwrite_keys: set[str] | None = None,
) -> str:
    overwrite_keys = overwrite_keys or set()
    bootstrap_lines = [
        "<script>",
    ]
    for key, value in replacements.items():
        if key in overwrite_keys:
            bootstrap_lines.append(
                f"sessionStorage.setItem({json.dumps(key)}, {json.dumps(str(value))});"
            )
        else:
            bootstrap_lines.append(
                "if (!sessionStorage.getItem("
                f"{json.dumps(key)}"
                ")) { sessionStorage.setItem("
                f"{json.dumps(key)}, {json.dumps(str(value))}"
                "); }"
            )
    bootstrap_lines.append("</script>")
    bootstrap = "\n".join(bootstrap_lines)
    return html_text.replace(script_tag, f"{bootstrap}\n{script_tag}")


class LoginPayload(BaseModel):
    image_base64: str


class LobbyConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, uid: str, websocket: WebSocket):
        await websocket.accept()
        previous = self.active_connections.get(uid)
        self.active_connections[uid] = websocket
        if previous is not None:
            try:
                await previous.close(code=1000)
            except Exception:
                pass

    def disconnect(self, uid: str, websocket: WebSocket | None = None) -> bool:
        active = self.active_connections.get(uid)
        if active is None:
            return False
        if websocket is not None and active is not websocket:
            return False
        del self.active_connections[uid]
        return True

    def is_connected(self, uid: str) -> bool:
        return uid in self.active_connections

    async def send_personal(self, uid: str, message: dict[str, Any]):
        ws = self.active_connections.get(uid)
        if ws is not None:
            await ws.send_json(message)

    async def broadcast(self, message: dict[str, Any]):
        dead_uids: list[str] = []
        for uid, connection in list(self.active_connections.items()):
            try:
                await connection.send_json(message)
            except Exception:
                dead_uids.append(uid)
        for uid in dead_uids:
            self.disconnect(uid)


class RoomConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, dict[str, WebSocket]] = {}

    async def connect(self, roomid: str, uid: str, websocket: WebSocket):
        await websocket.accept()
        room_connections = self.active_connections.setdefault(roomid, {})
        previous = room_connections.get(uid)
        room_connections[uid] = websocket
        if previous is not None:
            try:
                await previous.close(code=1000)
            except Exception:
                pass

    def disconnect(
        self,
        roomid: str,
        uid: str,
        websocket: WebSocket | None = None,
    ) -> bool:
        room_connections = self.active_connections.get(roomid)
        if room_connections is None:
            return False

        active = room_connections.get(uid)
        if active is None:
            return False
        if websocket is not None and active is not websocket:
            return False

        room_connections.pop(uid, None)
        if not room_connections:
            self.active_connections.pop(roomid, None)
        return True

    def is_connected(self, roomid: str, uid: str) -> bool:
        return uid in self.active_connections.get(roomid, {})

    def has_active_connections(self, roomid: str) -> bool:
        return bool(self.active_connections.get(roomid))

    async def send_personal(self, roomid: str, uid: str, message: dict[str, Any]):
        ws = self.active_connections.get(roomid, {}).get(uid)
        if ws is not None:
            await ws.send_json(message)

    async def broadcast(self, roomid: str, message: dict[str, Any]):
        dead_uids: list[str] = []
        for uid, connection in list(self.active_connections.get(roomid, {}).items()):
            try:
                await connection.send_json(message)
            except Exception:
                dead_uids.append(uid)
        for uid in dead_uids:
            self.disconnect(roomid, uid)


class RoomState:
    def __init__(self, p1: str, roomid: str, p1_profile: dict[str, Any] | None = None):
        p1_profile = p1_profile or {}
        self.p1 = p1
        self.p2: str | None = None
        self.p1_name = p1_profile.get("name") or p1
        self.p2_name: str | None = None
        self.p1_elo = p1_profile.get("elo_rating") if p1_profile.get("elo_rating") is not None else 1200
        self.p2_elo: int | None = None
        self.state = [0] * 9
        self.x_player = p1
        self.next_turn = self.x_player
        self.next_symbol = "X"
        self.roomid = roomid
        self.winner: str | None = None
        self.rematch_votes: set[str] = set()

    def set_p2(self, p2: str, p2_profile: dict[str, Any] | None = None) -> None:
        p2_profile = p2_profile or {}
        self.p2 = p2
        self.p2_name = p2_profile.get("name") or p2
        self.p2_elo = p2_profile.get("elo_rating") if p2_profile.get("elo_rating") is not None else 1200

    def reset_for_rematch(self) -> bool:
        if self.p2 is None:
            return False
        self.x_player = self.p2 if self.x_player == self.p1 else self.p1
        self.state = [0] * 9
        self.next_turn = self.x_player
        self.next_symbol = "X"
        self.winner = None
        self.rematch_votes.clear()
        return True

    def update(self, index: int) -> bool:
        if index < 0 or index >= 9:
            return False
        if self.state[index] != 0 or self.winner is not None:
            return False

        self.state[index] = 1 if self.next_symbol == "X" else -1
        self.next_turn = self.p2 if self.next_turn == self.p1 else self.p1
        self.next_symbol = "O" if self.next_symbol == "X" else "X"
        self.winner = self.check_winner()
        return True

    def check_winner(self) -> str | None:
        wins = [
            [0, 1, 2],
            [3, 4, 5],
            [6, 7, 8],
            [0, 3, 6],
            [1, 4, 7],
            [2, 5, 8],
            [0, 4, 8],
            [2, 4, 6],
        ]
        for a, b, c in wins:
            s = self.state[a] + self.state[b] + self.state[c]
            if s == 3:
                self.elo_update(self.x_player)
                return "X"
            if s == -3:
                o_player = self.p2 if self.x_player == self.p1 else self.p1
                self.elo_update(o_player)
                return "O"
        if all(cell != 0 for cell in self.state):
            self.elo_update("D")
            return "D"
        return None
    
    def elo_update(self, winner_uid: str | None) -> None:
        if self.p2 is None or winner_uid is None:
            return

        p1_rating = float(self.p1_elo if self.p1_elo is not None else 1200)
        p2_rating = float(self.p2_elo if self.p2_elo is not None else 1200)

        p1_expected = 1 / (1 + 10 ** ((p2_rating - p1_rating) / 400))
        p2_expected = 1 / (1 + 10 ** ((p1_rating - p2_rating) / 400))

        if winner_uid == self.p1:
            p1_score, p2_score = 1.0, 0.0
        elif winner_uid == self.p2:
            p1_score, p2_score = 0.0, 1.0
        elif winner_uid == "D":
            p1_score, p2_score = 0.5, 0.5
        else:
            return

        k_factor = 32
        self.p1_elo = int(round(p1_rating + k_factor * (p1_score - p1_expected)))
        self.p2_elo = int(round(p2_rating + k_factor * (p2_score - p2_expected)))

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET elo_rating = %s WHERE uid = %s",
                (self.p1_elo, self.p1),
            )
            cursor.execute(
                "UPDATE users SET elo_rating = %s WHERE uid = %s",
                (self.p2_elo, self.p2),
            )
            conn.commit()
        except Exception as exc:
            logger.error(
                "Failed to update elo_rating for room %s (%s vs %s): %s",
                self.roomid,
                self.p1,
                self.p2,
                exc,
            )
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()
        


def build_room_payload(room: RoomState, msg_type: str = "set_data") -> dict[str, Any]:
    o_player = room.p2 if room.x_player == room.p1 else room.p1 if room.p2 is not None else None
    return {
        "type": msg_type,
        "roomId": room.roomid,
        "turn": room.next_symbol,
        "p1": room.p1,
        "p2": room.p2,
        "p1Name": room.p1_name,
        "p2Name": room.p2_name,
        "p1Elo": room.p1_elo,
        "p2Elo": room.p2_elo,
        "xPlayer": room.x_player,
        "oPlayer": o_player,
        "board": room.state,
        "state": room.state,
        "winner": room.winner,
    }


lobby_manager = LobbyConnectionManager()
room_manager = RoomConnectionManager()
rooms: dict[str, RoomState] = {}

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, build_encodings_cache_at_startup)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "dev-session-secret-change-me"),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def serve_login():
    return FileResponse(LOGIN_HTML)


@app.post("/auth/login")
async def login(payload: LoginPayload, request: Request):
    try:
        image_bytes = base64.b64decode(payload.image_base64)
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"success": False, "detail": "Invalid image data."},
        )

    uid = authenticate_face(image_bytes)
    if not uid:
        detail = get_last_auth_error() or "Authentication failed."
        return JSONResponse(
            status_code=401,
            content={"success": False, "detail": detail},
        )

    request.session["uid"] = uid
    profile = get_user_profile(uid) or {}
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "uid": uid,
            "name": profile.get("name"),
            "elo_rating": profile.get("elo_rating"),
        },
    )


@app.get("/auth/session")
async def auth_session(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return JSONResponse(status_code=401, content={"success": False})
    profile = get_user_profile(uid) or {}
    return {
        "success": True,
        "uid": uid,
        "name": profile.get("name"),
        "elo_rating": profile.get("elo_rating"),
    }


@app.post("/auth/logout")
async def logout(request: Request):
    uid = request.session.get("uid")
    if uid:
        update_online(uid, False)
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return RedirectResponse(url="/", status_code=302)

    profile = get_user_profile(uid) or {}
    html_text = DASHBOARD_HTML.read_text(encoding="utf-8")
    html_text = inject_bootstrap(
        html_text,
        {
            "uid": uid,
            "name": profile.get("name") or "",
            "elo": profile.get("elo_rating") or "1200",
        },
        '<script src="dashboard.js"></script>',
    )
    return HTMLResponse(html_text)


@app.get("/dashboard.css")
async def dashboard_css():
    return FileResponse(DASHBOARD_CSS)


@app.get("/dashboard.js")
async def dashboard_js():
    return FileResponse(DASHBOARD_JS)


@app.get("/leaderboard", response_class=HTMLResponse)
async def serve_leaderboard(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return RedirectResponse(url="/", status_code=302)

    profile = get_user_profile(uid) or {}
    html_text = LEADERBOARD_HTML.read_text(encoding="utf-8")
    html_text = inject_bootstrap(
        html_text,
        {
            "uid": uid,
            "name": profile.get("name") or "",
            "elo": profile.get("elo_rating") or "1200",
        },
        '<script src="leaderboard.js"></script>',
    )
    return HTMLResponse(html_text)


@app.get("/leaderboard.css")
async def leaderboard_css():
    return FileResponse(LEADERBOARD_CSS)


@app.get("/leaderboard.js")
async def leaderboard_js():
    return FileResponse(LEADERBOARD_JS)


@app.get("/room/room.css")
async def room_css():
    return FileResponse(ROOM_CSS)


@app.get("/room/room.js")
async def room_js():
    return FileResponse(ROOM_JS)


@app.get("/room/{roomid}", response_class=HTMLResponse)
async def serve_room(roomid: str, request: Request):
    uid = request.session.get("uid")
    if not uid:
        return RedirectResponse(url="/", status_code=302)

    profile = get_user_profile(uid) or {}
    html_text = ROOM_HTML.read_text(encoding="utf-8")
    html_text = inject_bootstrap(
        html_text,
        {
            "uid": uid,
            "roomId": roomid,
            "name": profile.get("name") or "",
            "elo": profile.get("elo_rating") or "1200",
        },
        '<script src="room.js"></script>',
        overwrite_keys={"roomId"},
    )
    return HTMLResponse(html_text)


@app.get("/users")
async def get_online_users():
    return fetch_online_users()


@app.get("/leaderboard-data")
async def get_leaderboard_data():
    return fetch_leaderboard()


@app.websocket("/ws/{uid}")
async def lobby_websocket(websocket: WebSocket, uid: str):
    await lobby_manager.connect(uid, websocket)

    if not user_exists(uid):
        await websocket.close(code=1008)
        lobby_manager.disconnect(uid)
        return

    update_online(uid, True)
    await lobby_manager.broadcast({"type": "users", "data": fetch_online_users()})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "challenge":
                target = data.get("to")
                if isinstance(target, str) and target:
                    await lobby_manager.send_personal(
                        target,
                        {"type": "challenge", "from": uid},
                    )

            elif msg_type == "challenge_accept":
                target = data.get("to")
                if isinstance(target, str) and target:
                    roomid = build_room_id(uid, target)
                    payload = {
                        "type": "challenge_accepted",
                        "by": uid,
                        "roomid": roomid,
                    }
                    await lobby_manager.send_personal(target, payload)
                    await lobby_manager.send_personal(uid, payload)

            elif msg_type == "challenge_decline":
                target = data.get("to")
                if isinstance(target, str) and target:
                    await lobby_manager.send_personal(
                        target,
                        {"type": "challenge_declined", "by": uid},
                    )

    except WebSocketDisconnect:
        pass
    finally:
        lobby_manager.disconnect(uid, websocket)
        if not lobby_manager.is_connected(uid):
            update_online(uid, False)
            await lobby_manager.broadcast({"type": "users", "data": fetch_online_users()})


@app.websocket("/ws/{roomid}/{uid}")
async def room_websocket(websocket: WebSocket, roomid: str, uid: str):
    await room_manager.connect(roomid, uid, websocket)

    room = rooms.get(roomid)
    if room is None:
        room = RoomState(
            p1=uid,
            roomid=roomid,
            p1_profile=get_user_profile(uid),
        )
        rooms[roomid] = room
        await room_manager.send_personal(roomid, uid, build_room_payload(room))
    elif room.p2 is None and room.p1 != uid:
        room.set_p2(uid, get_user_profile(uid))
        payload = build_room_payload(room)
        await room_manager.send_personal(roomid, room.p1, payload)
        await room_manager.send_personal(roomid, room.p2, payload)
    elif uid in (room.p1, room.p2):
        await room_manager.send_personal(roomid, uid, build_room_payload(room))
    else:
        await websocket.close(code=1008)
        room_manager.disconnect(roomid, uid)
        return

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "rematch":
                if room.p2 is None or uid not in (room.p1, room.p2):
                    await room_manager.send_personal(
                        roomid,
                        uid,
                        {"type": "error", "message": "Cannot rematch without both players in room."},
                    )
                    continue

                if room.winner is None:
                    await room_manager.send_personal(
                        roomid,
                        uid,
                        {"type": "error", "message": "Finish the current game before rematch."},
                    )
                    continue

                if uid in room.rematch_votes:
                    await room_manager.send_personal(
                        roomid,
                        uid,
                        {"type": "rematch_waiting", "message": "Rematch already requested. Waiting for opponent."},
                    )
                    continue

                room.rematch_votes.add(uid)
                if len(room.rematch_votes) == 2 and room.reset_for_rematch():
                    await room_manager.broadcast(
                        roomid,
                        build_room_payload(room, msg_type="rematch_started"),
                    )
                else:
                    await room_manager.send_personal(
                        roomid,
                        uid,
                        {"type": "rematch_waiting", "message": "Rematch requested. Waiting for opponent."},
                    )
                    other_uid = room.p2 if uid == room.p1 else room.p1
                    if isinstance(other_uid, str) and room_manager.is_connected(roomid, other_uid):
                        await room_manager.send_personal(
                            roomid,
                            other_uid,
                            {
                                "type": "rematch_requested",
                                "by": uid,
                                "message": "Opponent requested a rematch.",
                            },
                        )
                continue

            if msg_type != "move":
                continue

            if room.p2 is None or not room_manager.is_connected(roomid, room.p2):
                await room_manager.send_personal(
                    roomid,
                    uid,
                    {"type": "error", "message": "Waiting for opponent to connect."},
                )
                continue

            if room.winner is not None:
                await room_manager.send_personal(
                    roomid,
                    uid,
                    {"type": "error", "message": "Game is already over."},
                )
                continue

            if uid != room.next_turn:
                await room_manager.send_personal(
                    roomid,
                    uid,
                    {"type": "error", "message": "Not your turn!"},
                )
                continue

            index = data.get("index")
            if not isinstance(index, int) or not room.update(index):
                await room_manager.send_personal(
                    roomid,
                    uid,
                    {"type": "error", "message": "Invalid move"},
                )
                continue

            await room_manager.broadcast(
                roomid,
                {
                    "type": "update_state",
                    "state": room.state,
                    "board": room.state,
                    "turn": room.next_symbol,
                    "nextTurn": room.next_turn,
                    "winner": room.winner,
                    "p1": room.p1,
                    "p2": room.p2,
                    "xPlayer": room.x_player,
                    "p1Elo": room.p1_elo,
                    "p2Elo": room.p2_elo,
                },
            )

    except WebSocketDisconnect:
        pass
    finally:
        removed = room_manager.disconnect(roomid, uid, websocket)
        room = rooms.get(roomid)
        if room is not None:
            room.rematch_votes.discard(uid)
            other_uid = None
            if uid == room.p1:
                other_uid = room.p2
            elif uid == room.p2:
                other_uid = room.p1

            if removed and isinstance(other_uid, str) and room_manager.is_connected(roomid, other_uid):
                if room.winner is None:
                    room.winner = "X" if other_uid == room.x_player else "O"
                    room.elo_update(other_uid)
                    await room_manager.broadcast(
                        roomid,
                        build_room_payload(room, msg_type="update_state"),
                    )

        if not room_manager.has_active_connections(roomid):
            rooms.pop(roomid, None)
