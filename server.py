import os
import json
import uuid
import random
import time
import chess
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock

app = Flask(__name__)
CORS(app)
sock = Sock(app)

GAMES = {}

# Mock Pool of Fake Online Opponents
FAKE_PLAYERS = [
    {"username": "GrandmasterFlex", "rating": 1540, "country": "US"},
    {"username": "KnightRider99", "rating": 1210, "country": "IN"},
    {"username": "RookAndRoll", "rating": 1380, "country": "DE"},
    {"username": "PawnStar_2026", "rating": 1100, "country": "BR"},
    {"username": "CheckmatePro", "rating": 1650, "country": "FR"},
    {"username": "TacticalGenius", "rating": 1420, "country": "CA"}
]

# -------------------------------------------------------------
# 1. ROOT & HEALTH CHECK
# -------------------------------------------------------------

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "mode": "headless_api"}), 200

# -------------------------------------------------------------
# 2. USER AUTHENTICATION & REGISTRATION
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
    username = data.get("username", f"Player_{uuid.uuid4().hex[:4]}")
    user_id = str(uuid.uuid4())
    token = f"token_{uuid.uuid4().hex}"

    return jsonify({
        "code": 0,
        "message": "Success",
        "user_id": user_id,
        "username": username,
        "token": token,
        "session_id": token,
        "user": {
            "id": user_id,
            "username": username,
            "email": data.get("email", f"{username}@example.com"),
            "avatar": "",
            "is_premium": True
        }
    }), 200

@app.route("/v1/users/guest-login", methods=["POST"])
def guest_login():
    user_id = str(uuid.uuid4())
    username = f"Guest_{uuid.uuid4().hex[:4]}"
    token = "session_token_xyz_123"
    
    return jsonify({
        "code": 0,
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
# 3. PLAY ONLINE (FAKE MATCHMAKING)
# -------------------------------------------------------------

@app.route("/v1/matchmaking/find", methods=["POST", "GET"])
@app.route("/v1/game/quickpair", methods=["POST", "GET"])
def find_match():
    """Generates an immediate pairing with a fake online opponent"""
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

# -------------------------------------------------------------
# 4. APP CONFIGURATION & GAME CONTENT
# -------------------------------------------------------------

@app.route("/v1/config", methods=["GET"])
def app_config():
    return jsonify({
        "status": "ok",
        "endpoints": {
            "websocket": f"wss://{request.host}/ws/live"
        }
    }), 200

@app.route("/v1/computer/bot-personalities", methods=["GET"])
def bot_personalities():
    return jsonify([
        {
            "id": "bot_easy",
            "name": "Novice Bot",
            "rating": 400,
            "avatarUrl": ""
        },
        {
            "id": "bot_medium",
            "name": "Intermediate Bot",
            "rating": 1200,
            "avatarUrl": ""
        }
    ]), 200

@app.route("/v1/puzzles/daily/today", methods=["GET"])
def daily_puzzle():
    return jsonify({
        "id": "daily_puzzle_01",
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
        "rating": 1000
    }), 200

@app.route("/v1/tactics-batch", methods=["GET"])
def tactics_batch():
    return jsonify([]), 200

@app.route("/v1/tv/show", methods=["GET"])
@app.route("/v1/watch", methods=["GET"])
def watch_tv():
    return jsonify([]), 200

@app.route("/v1/mastery-lessons/<path:subpath>", methods=["GET"])
def mastery_lessons(subpath):
    return jsonify([]), 200

# -------------------------------------------------------------
# 5. COMETD EMULATION ROUTE
# -------------------------------------------------------------

@app.route("/cometd", methods=["POST"])
def cometd_handshake():
    data = request.get_json(silent=True) or []
    response = []
    
    for msg in data:
        channel = msg.get("channel")
        msg_id = msg.get("id", "1")
        
        if channel == "/meta/handshake":
            response.append({
                "id": msg_id,
                "channel": "/meta/handshake",
                "successful": True,
                "clientId": f"client_{uuid.uuid4().hex[:6]}",
                "supportedConnectionTypes": ["websocket", "long-polling"]
            })
        elif channel in ["/meta/connect", "/meta/subscribe"]:
            response.append({
                "id": msg_id,
                "channel": channel,
                "successful": True
            })
            
    return jsonify(response)

# -------------------------------------------------------------
# 6. WEBSOCKET ENGINE (Game Moves & Sync)
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
                move = chess.Move.from_uci(payload.get("move"))
                if move in board.legal_moves:
                    board.push(move)
                    ws.send(json.dumps({"event": "move_played", "fen": board.fen()}))
                else:
                    ws.send(json.dumps({"event": "error", "message": "Illegal move"}))
        except Exception as err:
            ws.send(json.dumps({"event": "error", "message": str(err)}))

# -------------------------------------------------------------
# 7. CATCH-ALL ROUTE
# -------------------------------------------------------------

@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(path):
    return jsonify({}), 200

# -------------------------------------------------------------
# RUNNER
# -------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
