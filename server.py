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


@app.errorhandler(400)
def bad_request(error):
    """Keep API errors JSON so mobile clients never receive an HTML error page."""
    return jsonify({"code": 400, "status": "error", "message": "Bad request"}), 400


@app.errorhandler(404)
def not_found(error):
    return jsonify({"code": 404, "status": "not_found", "message": "Endpoint not found"}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"code": 405, "status": "error", "message": "Method not allowed"}), 405


@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Unhandled API error", exc_info=error)
    return jsonify({"code": 500, "status": "error", "message": "Internal server error"}), 500

GAMES = {}
USERS = {}
SESSIONS = {}

FAKE_PLAYERS = [
    {"username": "GrandmasterFlex", "rating": 1540, "country": "US"},
    {"username": "KnightRider99", "rating": 1210, "country": "IN"},
    {"username": "RookAndRoll", "rating": 1380, "country": "DE"},
    {"username": "PawnStar_2026", "rating": 1100, "country": "BR"},
    {"username": "CheckmatePro", "rating": 1650, "country": "FR"},
]

ONLINE_PLAYERS = [
    {"id": "online_1001", "username": "ChessRanger", "rating": 1180, "country": "IN", "status": "online"},
    {"id": "online_1002", "username": "KnightRider99", "rating": 1210, "country": "IN", "status": "online"},
    {"id": "online_1003", "username": "RookAndRoll", "rating": 1380, "country": "DE", "status": "online"},
    {"id": "online_1004", "username": "PawnStar_2026", "rating": 1100, "country": "BR", "status": "online"},
]

# -------------------------------------------------------------
# 1. ROOT & HEALTH CHECK
# -------------------------------------------------------------

@app.route("/", methods=["GET", "HEAD"])
def health_check():
    return jsonify({"status": "online", "mode": "headless_api"}), 200

# -------------------------------------------------------------
# 2. USER AUTHENTICATION & REGISTRATION
# -------------------------------------------------------------

@app.route("/v1/users/validate-username/<username>", methods=["GET"])
def validate_username(username):
    username = username.strip()
    valid = 3 <= len(username) <= 25 and username.replace("_", "").isalnum()
    available = valid
    payload = {
        "valid": valid,
        "available": available,
        "username": username,
        "code": 0 if available else -1,
        "message": "Username available" if available else "Username is not available"
    }
    return jsonify({**payload, "data": payload}), 200


def _request_data():
    """Accept JSON, form data, and the data wrapper used by some clients."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return {}
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


def _auth_payload(user, token):
    user_payload = {
        "id": user["id"],
        "user_id": user["id"],
        "uuid": user["id"],
        "username": user["username"],
        "email": user["email"],
        "avatar": user.get("avatar", ""),
        "is_premium": False,
        "enabled": True,
    }
    return {
        "code": 0,
        "status": "success",
        "success": True,
        "message": "Success",
        "token": token,
        "session_id": token,
        "user_id": user["id"],
        "username": user["username"],
        "user": user_payload,
        "data": {
            "token": token,
            "session_id": token,
            "user_id": user["id"],
            "username": user["username"],
            "user": user_payload,
        },
    }


def _create_user(data, guest=False):
    username = str(data.get("username") or data.get("userName") or "").strip()
    
    # Check if a session already exists for this client to prevent guest loop retries
    token_header = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    token_header = token_header or request.headers.get("X-Session-Token", "").strip()
    if token_header and token_header in SESSIONS:
        existing_user_id = SESSIONS[token_header]
        existing_user = next((u for u in USERS.values() if u["id"] == existing_user_id), None)
        if existing_user:
            return jsonify(_auth_payload(existing_user, token_header)), 200

    if guest:
        username = username or f"Guest_{uuid.uuid4().hex[:6]}"
    if not username:
        username = f"Player_{uuid.uuid4().hex[:6]}"
    key = username.lower()
    if key in USERS and not guest:
        user = USERS[key]
        token = f"session_{uuid.uuid4().hex}"
        SESSIONS[token] = user["id"]
        return jsonify(_auth_payload(user, token)), 200
    user = {
        "id": str(uuid.uuid4()),
        "username": username,
        "email": str(data.get("email") or f"{username}@example.com"),
        "avatar": str(data.get("avatar") or ""),
    }
    token = f"session_{uuid.uuid4().hex}"
    USERS[key] = user
    SESSIONS[token] = user["id"]
    return jsonify(_auth_payload(user, token)), 200


@app.route("/v1/users/register", methods=["POST"])
@app.route("/v1/auth/register", methods=["POST"])
@app.route("/v1/signup", methods=["POST"])
def register_alias():
    return _create_user(_request_data())


@app.route("/v1/users/login", methods=["POST"])
@app.route("/v1/auth/login", methods=["POST"])
@app.route("/v1/login", methods=["POST"])
def login_user():
    data = _request_data()
    username = str(data.get("username") or data.get("userName") or "").strip().lower()
    user = USERS.get(username)
    if not user:
        return _create_user(data)
    token = f"session_{uuid.uuid4().hex}"
    SESSIONS[token] = user["id"]
    return jsonify(_auth_payload(user, token)), 200


@app.route("/v1/users", methods=["POST"])
def register_user():
    return _create_user(_request_data())


@app.route("/v1/users/guest-login", methods=["POST"])
def guest_login():
    return _create_user(_request_data(), guest=True)


def _current_user():
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    token = token or request.headers.get("X-Session-Token", "").strip()
    user_id = SESSIONS.get(token)
    if user_id:
        return next((u for u in USERS.values() if u["id"] == user_id), None)
    return next(iter(USERS.values()), None)


@app.route("/v1/users/me", methods=["GET"])
@app.route("/v1/me", methods=["GET"])
@app.route("/v1/users/profile", methods=["GET"])
def current_profile():
    user = _current_user()
    if not user:
        return jsonify({"code": 9, "status": "error", "message": "Resource not found.",
                        "more_info": "https://api.chess.com/codes#9"}), 404
    return jsonify({"code": 0, "status": "success", "success": True,
                    "user": user, "data": {"user": user}}), 200


@app.route("/v1/users/<username>", methods=["GET"])
def user_profile(username):
    user = USERS.get(username.strip().lower())
    if not user:
        return jsonify({"code": 9, "status": "error", "message": "Resource not found.",
                        "more_info": "https://api.chess.com/codes#9"}), 404
    return jsonify({"code": 0, "status": "success", "success": True,
                    "user": user, "data": {"user": user}}), 200

# -------------------------------------------------------------
# 3. APP CONFIGURATION & HOME FEED
# -------------------------------------------------------------

@app.route("/v1/config", methods=["GET"])
def app_config():
    # Render terminates TLS before forwarding to Gunicorn.
    scheme = "wss" if request.headers.get("X-Forwarded-Proto", "").lower() == "https" or request.is_secure else "ws"
    ws_host = request.host
    return jsonify({
        "status": "ok",
        "endpoints": {
            "websocket": f"{scheme}://{ws_host}/ws/live",
            "websocket_template": f"{scheme}://{ws_host}/ws/live/{{game_id}}"
        },
        "features": []
    }), 200

@app.route("/v1/home", methods=["GET"])
@app.route("/v1/feed", methods=["GET"])
def home_feed():
    return jsonify({
        "status": "ok",
        "data": {
            "sections": [],
            "items": []
        }
    }), 200

@app.route("/v1/computer/bot-personalities", methods=["GET"])
def bot_personalities():
    personalities = [
        {"id": "bot_easy", "name": "Novice Bot", "rating": 400, "avatarUrl": ""},
        {"id": "bot_medium", "name": "Intermediate Bot", "rating": 1200, "avatarUrl": ""},
        {"id": "bot_hard", "name": "Grandmaster Bot", "rating": 2200, "avatarUrl": ""}
    ]
    # The Android client deserializes this endpoint as an object, not a root array.
    return jsonify({
        "status": "ok",
        "code": 0,
        "success": True,
        "personalities": personalities,
        "bots": personalities,
        "data": {"personalities": personalities, "bots": personalities}
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
# 4. MATCHMAKING & COMETD EMULATION
# -------------------------------------------------------------

def create_fake_game():
    game_id = str(uuid.uuid4())[:8]
    opponent = random.choice(ONLINE_PLAYERS)
    player_color = random.choice(["white", "black"])
    GAMES[game_id] = {
        "board": chess.Board(),
        "opponent": opponent,
        "created_at": time.time(),
        "status": "active",
        "color": player_color,
        "time_control": "10+0",
    }
    return game_id, opponent, player_color


@app.route("/v1/players/online", methods=["GET"])
@app.route("/v1/users/online", methods=["GET"])
@app.route("/v1/presence", methods=["GET"])
def online_players():
    return jsonify({
        "status": "ok",
        "data": {"players": ONLINE_PLAYERS, "count": len(ONLINE_PLAYERS)},
        "players": ONLINE_PLAYERS,
        "count": len(ONLINE_PLAYERS),
    }), 200


@app.route("/v1/matchmaking", methods=["POST", "GET"])
@app.route("/v1/matchmaking/start", methods=["POST", "GET"])
@app.route("/v1/matchmaking/quick", methods=["POST", "GET"])
def start_matchmaking():
    return _match_response()

@app.route("/v1/matchmaking/find", methods=["POST", "GET"])
@app.route("/v1/game/quickpair", methods=["POST", "GET"])
def find_match():
    return _match_response()


def _match_response():
    game_id, opponent, player_color = create_fake_game()
    return jsonify({
        "code": 0,
        "status": "matched",
        "success": True,
        "game_id": game_id,
        "gameId": game_id,
        "id": game_id,
        "opponent": {
            "id": opponent["id"],
            "username": opponent["username"],
            "rating": opponent["rating"],
            "country": opponent["country"],
            "avatar": "",
            "online": True,
            "status": "online",
        },
        "player": {"color": player_color},
        "color": player_color,
        "time_control": "10+0",
        "timeControl": "10+0",
        "initial_fen": chess.Board().fen(),
        "fen": chess.Board().fen(),
    }), 200


@app.route("/v1/matchmaking/status/<game_id>", methods=["GET"])
@app.route("/v1/game/<game_id>", methods=["GET"])
def game_status(game_id):
    game = GAMES.get(game_id)
    if not game:
        return jsonify({"code": 404, "status": "not_found", "message": "Game not found"}), 404
    return jsonify({
        "code": 0,
        "status": game.get("status", "active"),
        "game_id": game_id,
        "gameId": game_id,
        "fen": game["board"].fen(),
        "opponent": game["opponent"],
        "color": game["color"],
        "time_control": game["time_control"],
    }), 200


@app.route("/v1/game/<game_id>/move", methods=["POST"])
@app.route("/v1/matchmaking/<game_id>/move", methods=["POST"])
def play_move(game_id):
    game = GAMES.get(game_id)
    if not game:
        return jsonify({"code": 404, "status": "not_found", "message": "Game not found"}), 404

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"code": 400, "status": "error", "message": "A JSON object is required"}), 400
    move_uci = payload.get("move") or payload.get("uci") or payload.get("move_uci")
    if not move_uci:
        return jsonify({"code": 400, "status": "error", "message": "A UCI move is required"}), 400
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return jsonify({"code": 400, "status": "error", "message": "Invalid UCI move"}), 400
    board = game["board"]
    if move not in board.legal_moves:
        return jsonify({"code": 400, "status": "error", "message": "Illegal move"}), 400

    board.push(move)
    return jsonify({
        "code": 0,
        "status": "ok",
        "game_id": game_id,
        "gameId": game_id,
        "move": move_uci,
        "fen": board.fen(),
        "last_move": move_uci,
    }), 200


@app.route("/v1/matchmaking/cancel", methods=["POST", "DELETE"])
def cancel_matchmaking():
    return jsonify({"code": 0, "status": "cancelled", "success": True}), 200


@app.route("/cometd", methods=["POST"])
@app.route("/cometd/", methods=["POST"])
@app.route("/cometd/handshake", methods=["POST"])
def cometd_engine():
    data = request.get_json(silent=True) or []
    is_single_object = False

    if isinstance(data, dict):
        data = [data]
        is_single_object = True
    if not isinstance(data, list) or not all(isinstance(msg, dict) for msg in data):
        return jsonify({"code": 400, "status": "error", "message": "CometD payload must be an object or array of objects"}), 400

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

    if is_single_object and len(response) > 0:
        return jsonify(response[0]), 200

    return jsonify(response), 200

# -------------------------------------------------------------
# 5. WEBSOCKET ENGINE
# -------------------------------------------------------------

@sock.route("/ws/live")
@sock.route("/ws/live/<game_id>")
def live_websocket(ws, game_id=None):
    game_id = game_id or str(uuid.uuid4())[:8]
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
    return jsonify({
        "status": "error",
        "code": "not_found",
        "message": f"Endpoint not found: /{path}"
    }), 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
