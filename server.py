import json
import os
import random
import time
import uuid
import chess
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock

app = Flask(__name__)
CORS(app)
sock = Sock(app)

GAMES = {}

FAKE_PLAYERS = [
    {"username": "GrandmasterFlex", "rating": 1540, "country": "US"},
    {"username": "KnightRider99", "rating": 1210, "country": "IN"},
    {"username": "RookAndRoll", "rating": 1380, "country": "DE"},
    {"username": "PawnStar_2026", "rating": 1100, "country": "BR"},
    {"username": "CheckmatePro", "rating": 1650, "country": "FR"},
]

# -------------------------------------------------------------
# 1. ROOT & HEALTH CHECK
# -------------------------------------------------------------

@app.route("/", methods=["GET", "HEAD"])
def health_check():
    return jsonify({"status": "online", "mode": "headless_api"}), 200

# -------------------------------------------------------------
# 2. USER AUTHENTICATION & REGISTRATION (Fixes Image 1 Error -1)
# -------------------------------------------------------------

@app.route("/v1/users/validate-username/<username>", methods=["GET"])
def validate_username(username):
    return jsonify({
        "valid": True,
        "available": True,
        "username": username,
        "code": 0,
        "message": "Username available"
    }), 200

@app.route("/v1/users", methods=["POST"])
def register_user():
    data = request.get_json(silent=True) or {}
    username = data.get("username", f"TEST133")
    user_id = str(uuid.uuid4())
    token = f"token_{uuid.uuid4().hex}"

    # Complete response structure to satisfy Android client user parser
    return jsonify({
        "code": 0,
        "status": "success",
        "message": "Success",
        "user_id": user_id,
        "username": username,
        "token": token,
        "session_id": token,
        "user": {
            "id": user_id,
            "username": username,
            "uuid": user_id,
            "email": data.get("email", f"{username}@example.com"),
            "avatar": "",
            "is_premium": True,
            "enabled": True
        }
    }), 200

@app.route("/v1/users/guest-login", methods=["POST"])
def guest_login():
    user_id = str(uuid.uuid4())
    username = f"Guest_{uuid.uuid4().hex[:4]}"
    token = f"session_token_{uuid.uuid4().hex[:8]}"
    
    return jsonify({
        "code": 0,
        "status": "success",
        "message": "Success",
        "user_id": user_id,
        "username": username,
        "token": token,
        "session_id": token,
        "user": {
            "id": user_id,
            "username": username,
            "is_premium": True
        }
    }), 200

# -------------------------------------------------------------
# 3. APP CONFIGURATION & HOME FEED (Fixes Image 3 BEGIN_ARRAY Error)
# -------------------------------------------------------------

@app.route("/v1/config", methods=["GET"])
def app_config():
    scheme = "wss" if request.is_secure else "ws"
    ws_host = request.host
    # Return as list or expected format array wrapper if client queries root endpoint
    return jsonify({
        "status": "ok",
        "endpoints": {
            "websocket": f"{scheme}://{ws_host}/ws/live"
        },
        "features": []
    }), 200

@app.route("/v1/home", methods=["GET"])
@app.route("/v1/feed", methods=["GET"])
def home_feed():
    # The mobile home/feed response is an object.  Returning [] here causes
    # Gson/Moshi clients that deserialize a HomeResponse to fail with:
    # "Expected BEGIN_OBJECT but was BEGIN_ARRAY at path $".
    return jsonify({
        "status": "ok",
        "data": {
            "sections": [],
            "items": []
        }
    }), 200

@app.route("/v1/computer/bot-personalities", methods=["GET"])
def bot_personalities():
    return jsonify({
        "status": "ok",
        "data": {
            "bots": [
                {"id": "bot_easy", "name": "Novice Bot", "rating": 400, "avatarUrl": ""},
                {"id": "bot_medium", "name": "Intermediate Bot", "rating": 1200, "avatarUrl": ""},
                {"id": "bot_hard", "name": "Grandmaster Bot", "rating": 2200, "avatarUrl": ""}
            ]
        }
    }), 200

@app.route("/v1/puzzles/daily/today", methods=["GET"])
def daily_puzzle():
    return jsonify({
        "id": "daily_puzzle_01",
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
        "rating": 1000
    }), 200

@app.route("/v1/tactics-batch", methods=["GET"])
@app.route("/v1/tv/show", methods=["GET"])
@app.route("/v1/watch", methods=["GET"])
def array_endpoints():
    if request.path == "/v1/tactics-batch":
        payload = {"tactics": []}
    else:
        payload = {"live_games": []}
    return jsonify({"status": "ok", "data": payload}), 200

@app.route("/v1/mastery-lessons/<path:subpath>", methods=["GET"])
def mastery_lessons(subpath):
    return jsonify({
        "status": "ok",
        "data": {"courses": [], "levels": []}
    }), 200

# -------------------------------------------------------------
# 4. MATCHMAKING & COMETD EMULATION (Fixes Image 2 Searching Stuck)
# -------------------------------------------------------------

@app.route("/v1/matchmaking/find", methods=["POST", "GET"])
@app.route("/v1/game/quickpair", methods=["POST", "GET"])
def find_match():
    game_id = str(uuid.uuid4())[:8]
    opponent = random.choice(FAKE_PLAYERS)
    
    GAMES[game_id] = {
        "board": chess.Board(),
        "opponent": opponent,
        "created_at": time.time()
    }

    return jsonify({
        "code": 0,
        "game_id": game_id,
        "status": "matched",
        "opponent": {
            "id": str(uuid.uuid4()),
            "username": opponent["username"],
            "rating": opponent["rating"],
            "country": opponent["country"],
            "avatar": ""
        },
        "color": random.choice(["white", "black"]),
        "time_control": "10+0"
    }), 200

@app.route("/cometd", methods=["POST"])
@app.route("/cometd/", methods=["POST"])
@app.route("/cometd/handshake", methods=["POST"])
def cometd_engine():
    data = request.get_json(silent=True) or []
    if isinstance(data, dict):
        data = [data]

    response = []
    for msg in data:
        channel = msg.get("channel")
        msg_id = msg.get("id", "1")
        client_id = f"client_{uuid.uuid4().hex[:6]}"

        if channel == "/meta/handshake":
            response.append({
                "id": msg_id,
                "channel": "/meta/handshake",
                "successful": True,
                "clientId": client_id,
                "supportedConnectionTypes": ["websocket", "long-polling"],
                "version": "1.0"
            })
        elif channel == "/meta/connect":
            game_id = str(uuid.uuid4())[:8]
            opponent = random.choice(FAKE_PLAYERS)
            response.append({
                "id": msg_id,
                "channel": "/meta/connect",
                "successful": True,
                "advice": {"reconnect": "retry", "interval": 0},
                "data": {
                    "event": "game_start",
                    "game_id": game_id,
                    "opponent": opponent["username"]
                }
            })
        elif channel in ["/meta/subscribe", "/meta/unsubscribe"]:
            response.append({
                "id": msg_id,
                "channel": channel,
                "successful": True
            })
        else:
            response.append({
                "id": msg_id,
                "channel": channel,
                "successful": True,
                "data": {}
            })

    return jsonify(response)

# -------------------------------------------------------------
# 5. WEBSOCKET ENGINE
# -------------------------------------------------------------

@sock.route("/ws/live/<game_id>")
def live_websocket(ws, game_id):
    if game_id not in GAMES:
        GAMES[game_id] = {"board": chess.Board()}
    
    board = GAMES[game_id]["board"]
    ws.send(json.dumps({"event": "connected", "fen": board.fen()}))

    while True:
        raw_msg = ws.receive()
        if not raw_msg:
            break
        try:
            payload = json.loads(raw_msg)
            if payload.get("action") == "move":
                move_uci = payload.get("move")
                if move_uci:
                    move = chess.Move.from_uci(move_uci)
                    if move in board.legal_moves:
                        board.push(move)
                        ws.send(json.dumps({"event": "move_played", "fen": board.fen()}))
                    else:
                        ws.send(json.dumps({"event": "error", "message": "Illegal move"}))
        except Exception as err:
            ws.send(json.dumps({"event": "error", "message": str(err)}))

# -------------------------------------------------------------
# 6. CATCH-ALL ROUTE
# -------------------------------------------------------------

@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(path):
    # Never return an arbitrary array for an unknown endpoint.  The Android
    # client may try to deserialize this response as an object, which turns a
    # harmless 404 into a JSON parsing crash.
    return jsonify({
        "status": "error",
        "code": "not_found",
        "message": f"Endpoint not found: /{path}"
    }), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
