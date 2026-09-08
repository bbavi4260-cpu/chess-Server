import json
import os
import random
import sqlite3
import threading
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
USERS = {}
SESSIONS = {}
NEXT_USER_ID = 1000001
DB_PATH = os.environ.get("CHESS_DB_PATH", os.path.join(os.path.dirname(__file__), "chess.sqlite3"))
DB_LOCK = threading.RLock()
WS_CLIENTS = {}
GAME_LOCK = threading.RLock()
MATCH_LOCK = threading.RLock()

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


def _db_connect():
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _init_database():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with DB_LOCK, _db_connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            player_key TEXT PRIMARY KEY,
            user_id INTEGER,
            username TEXT NOT NULL,
            last_seen REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS games (
            game_id TEXT PRIMARY KEY,
            white_key TEXT NOT NULL,
            black_key TEXT,
            white_name TEXT NOT NULL,
            black_name TEXT,
            status TEXT NOT NULL,
            fen TEXT NOT NULL,
            moves TEXT NOT NULL DEFAULT '[]',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS games_waiting_idx
            ON games(status, created_at);
        """)


def _player_identity(data=None):
    """Return a stable player id; never use a fake bot as an opponent."""
    data = data or {}
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_id = SESSIONS.get(token)
    user = next((u for u in USERS.values() if u["id"] == user_id), None)
    key = (data.get("player_id") or data.get("user_id") or
           data.get("username") or data.get("userName") or
           (user["id"] if user else None))
    if key is None:
        # A client should send username/user_id. This fallback prevents two
        # requests from one device from creating two queue entries.
        key = request.headers.get("X-Player-Id") or request.remote_addr or "guest"
    key = str(key).strip() or "guest"
    name = str(data.get("username") or data.get("userName") or
               (user["username"] if user else key)).strip()
    return key, name


def _game_from_row(row):
    if not row:
        return None
    game = GAMES.get(row["game_id"])
    if game is None:
        game = {"board": chess.Board(row["fen"]), "created_at": row["created_at"]}
        GAMES[row["game_id"]] = game
    else:
        game["board"] = chess.Board(row["fen"])
    game.update({"game_id": row["game_id"], "status": row["status"],
                 "white_key": row["white_key"], "black_key": row["black_key"],
                 "white_name": row["white_name"], "black_name": row["black_name"]})
    return game


def _persist_game(game_id, board, status=None, moves=None):
    now = time.time()
    with DB_LOCK, _db_connect() as db:
        if moves is None:
            row = db.execute("SELECT moves FROM games WHERE game_id=?", (game_id,)).fetchone()
            moves = json.loads(row["moves"] if row else "[]")
        db.execute("UPDATE games SET fen=?, moves=?, updated_at=?, status=COALESCE(?, status) WHERE game_id=?",
                   (board.fen(), json.dumps(moves), now, status, game_id))


def _broadcast(game_id, payload):
    dead = []
    for client in list(WS_CLIENTS.get(game_id, set())):
        try:
            client.send(json.dumps(payload))
        except Exception:
            dead.append(client)
    for client in dead:
        WS_CLIENTS.get(game_id, set()).discard(client)


_init_database()

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
    username = username.strip()
    valid = 3 <= len(username) <= 25 and username.replace("_", "").isalnum()
    # Existing local users remain valid for a retried signup flow.  The APK
    # may validate again after a successful request whose response was lost.
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
            # Android parser expects the authenticated user id directly at
            # $.data.id, not only nested inside $.data.user.
            "id": user["id"],
            "token": token,
            "session_id": token,
            "user_id": user["id"],
            "username": user["username"],
            "user": user_payload,
        },
    }


def _create_user(data, guest=False):
    global NEXT_USER_ID
    username = str(data.get("username") or data.get("userName") or "").strip()
    if guest:
        username = username or f"Guest_{uuid.uuid4().hex[:6]}"
    if not username:
        username = f"Player_{uuid.uuid4().hex[:6]}"
    key = username.lower()
    if key in USERS and not guest:
        # Signup can be retried when the APK receives a response but fails to
        # parse it.  Treat the retry as idempotent instead of returning
        # "username already taken" and trapping the user on the signup page.
        user = USERS[key]
        token = f"session_{uuid.uuid4().hex}"
        SESSIONS[token] = user["id"]
        return jsonify(_auth_payload(user, token)), 200
    user = {
        # The Android model parses $.data.id as a Java/Kotlin Long. A UUID
        # string here causes: Expected a long but was <uuid> at $.data.id.
        "id": NEXT_USER_ID,
        "username": username,
        "email": str(data.get("email") or f"{username}@example.com"),
        "avatar": str(data.get("avatar") or ""),
        "password": str(data.get("password") or data.get("pass") or ""),
    }
    NEXT_USER_ID += 1
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
    email = str(data.get("email") or "").strip().lower()
    user = USERS.get(username)
    if not user and email:
        user = next((candidate for candidate in USERS.values()
                     if candidate.get("email", "").lower() == email), None)
    if not user:
        # This is a local test server: create a test account on first login.
        if email and not username:
            data = {**data, "username": email.split("@", 1)[0]}
        return _create_user(data)
    token = f"session_{uuid.uuid4().hex}"
    SESSIONS[token] = user["id"]
    return jsonify(_auth_payload(user, token)), 200


@app.route("/v1/users", methods=["GET", "POST"])
def register_user():
    if request.method == "GET":
        username = request.args.get("username", "").strip().lower()
        user = USERS.get(username) or _current_user()
        if not user:
            # Guest sessions may be recreated by the APK after a process
            # restart. Return a lightweight profile instead of failing the
            # home screen with HTTP 404.
            user = {
                "id": 1000000,
                "username": request.args.get("username", "Guest"),
                "email": "",
                "avatar": "",
                "password": "",
            }
        return jsonify(_profile_payload(user)), 200
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
@app.route("/v1/profile", methods=["GET"])
@app.route("/v1/account", methods=["GET"])
def current_profile():
    user = _current_user()
    if not user:
        return jsonify({"code": 9, "status": "error", "message": "Resource not found.",
                        "more_info": "https://api.chess.com/codes#9"}), 404
    return jsonify(_profile_payload(user)), 200


def _profile_payload(user):
    return {
        "code": 0,
        "status": "success",
        "success": True,
        "id": user["id"],
        "user_id": user["id"],
        "username": user["username"],
        "user": user,
        "data": {
            "id": user["id"],
            "user_id": user["id"],
            "username": user["username"],
            "user": user,
        },
    }


@app.route("/v1/users/<username>", methods=["GET"])
def user_profile(username):
    lookup = username.strip()
    user = USERS.get(lookup.lower())
    if not user and lookup.isdigit():
        numeric_id = int(lookup)
        user = next((candidate for candidate in USERS.values()
                     if candidate["id"] == numeric_id), None)
    if not user:
        return jsonify({"code": 9, "status": "error", "message": "Resource not found.",
                        "more_info": "https://api.chess.com/codes#9"}), 404
    return jsonify(_profile_payload(user)), 200


@app.route("/v1/friends", methods=["GET"])
def friends():
    return jsonify({"code": 0, "status": "success", "data": {"friends": []},
                    "friends": [], "count": 0}), 200


@app.route("/v1/users/flair-status", methods=["GET"])
def flair_status():
    return jsonify({"code": 0, "status": "success", "data": {"flairs": []},
                    "flairs": []}), 200


@app.route("/v1/games/challenges", methods=["GET", "POST"])
def game_challenges():
    return jsonify({"code": 0, "status": "success", "data": {"challenges": []},
                    "challenges": []}), 200


@app.route("/v1/games/current", methods=["GET"])
def current_games():
    games = []
    for game_id, game in GAMES.items():
        games.append({"id": game_id, "game_id": game_id, "status": game.get("status", "active"),
                      "fen": game["board"].fen(), "opponent": game.get("opponent", {})})
    return jsonify({"code": 0, "status": "success", "data": {"games": games},
                    "games": games}), 200


@app.route("/v1/users/notifications/current", methods=["GET"])
def current_notifications():
    return jsonify({"code": 0, "status": "success", "data": {"notifications": []},
                    "notifications": []}), 200


@app.route("/v1/league/user", methods=["GET"])
def league_user():
    return jsonify({"code": 0, "status": "success",
                    "data": {"league": None, "rank": 0, "points": 0},
                    "league": None, "rank": 0, "points": 0}), 200

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
    # The home screen deserializes this endpoint as List<BotPersonality>.
    # Do not wrap it in {"data": ...}; that causes BEGIN_ARRAY/BEGIN_OBJECT.
    return jsonify([
        {"id": "bot_easy", "name": "Novice Bot", "rating": 400, "avatarUrl": ""},
        {"id": "bot_medium", "name": "Intermediate Bot", "rating": 1200, "avatarUrl": ""},
        {"id": "bot_hard", "name": "Grandmaster Bot", "rating": 2200, "avatarUrl": ""}
    ]), 200

@app.route("/v1/puzzles/daily/today", methods=["GET"])
def daily_puzzle():
    puzzle = {
        "id": "daily_puzzle_01",
        "puzzleId": "daily_puzzle_01",
        "fen": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
        "rating": 1000,
        "initialFen": "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
        "moves": ["c6b4", "f3e5", "b4c2"],
        "solution": ["c6b4", "f3e5", "b4c2"],
        "theme": "opening",
        "status": "ready"
    }
    return jsonify(puzzle), 200


@app.route("/v1/tactics/puzzle-path/friends/stats", methods=["GET"])
def puzzle_path_friend_stats():
    stats = _complete_stats_schema()
    return jsonify({"code": 0, "status": "success", "data": stats, **stats}), 200


def _complete_stats_schema(game_type=90):
    """Return all counters used by different Android stats model versions."""
    now = int(time.time() * 1000)
    return {
        "gameType": game_type,
        "game_type": game_type,
        "rating": 1200,
        "bestRating": 1200,
        "best_rating": 1200,
        "highestRating": 1200,
        "highest_rating": 1200,
        # Android parses game_ratings as an object/map, not a JSON array.
        "game_ratings": {
            "bullet": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "blitz": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "rapid": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "daily": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "tactics": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "puzzles": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
        },
        "gameRatings": {
            "bullet": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "blitz": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "rapid": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "daily": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "tactics": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
            "puzzles": {"rating": 1200, "last_rating": 1200, "previous_rating": 1200, "delta": 0, "rating_delta": 0, "games_count": 0, "game_count": 0, "wins": 0, "losses": 0, "draws": 0},
        },
        "ratings": {},
        "rating_history": [],
        "ratingHistory": [],
        "rating_points": [],
        "game_count": 0,
        "games_count": 0,
        "gamesPlayed": 0,
        "games_played": 0,
        "gamesWon": 0,
        "games_won": 0,
        "gamesLost": 0,
        "games_lost": 0,
        "gamesDrawn": 0,
        "games_drawn": 0,
        "winRate": 0,
        "win_rate": 0,
        "tactics_count": 0,
        "tacticsCount": 0,
        "puzzles_count": 0,
        "puzzle_count": 0,
        "puzzlesCount": 0,
        "lesson_count": 0,
        "lessons_count": 0,
        "lessonCount": 0,
        "lessonsCompleted": 0,
        "lessons_completed": 0,
        "solved": 0,
        "solved_count": 0,
        "correct": 0,
        "incorrect": 0,
        "attempts": 0,
        "streak": 0,
        "current_streak": 0,
        "best_streak": 0,
        "maxStreak": 0,
        "points": 0,
        "xp": 0,
        "level": 1,
        "rank": 0,
        "graph": [],
        "ratingGraph": [],
        "rating_graph": [],
        "lastGraphTimestamp": now,
        "last_graph_timestamp": now,
        "lastPlayed": 0,
        "last_played": 0,
        "createdAt": 0,
        "updatedAt": now,
    }


@app.route("/v1/tactics/stats", methods=["GET"])
def tactics_stats():
    stats = _complete_stats_schema()
    return jsonify({"code": 0, "status": "success", "data": stats, **stats}), 200


@app.route("/v1/users/stats2/overview/<int:game_type>", methods=["GET"])
def user_stats_overview(game_type):
    """Stats overview used by the profile/statistics screen.

    The APK requests this endpoint with game type 90 for tactics. Keep all
    numeric fields present so the Android model can deserialize the response.
    """
    overview = _complete_stats_schema(game_type)
    return jsonify({"code": 0, "status": "success", "data": overview, **overview}), 200


@app.route("/v1/users/fcm", methods=["POST", "PUT"])
def register_fcm_token():
    payload = request.get_json(silent=True) or {}
    return jsonify({
        "code": 0,
        "status": "success",
        "success": True,
        "data": {"registered": True, "token": payload.get("token", "")},
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

def create_or_join_game(data):
    """Atomically pair two real player ids and persist the room in SQLite."""
    player_key, player_name = _player_identity(data)
    with MATCH_LOCK, DB_LOCK, _db_connect() as db:
        # Remove this player's old waiting entries so retries are idempotent.
        db.execute("DELETE FROM games WHERE status='waiting' AND white_key=?", (player_key,))
        waiting = db.execute(
            "SELECT * FROM games WHERE status='waiting' AND white_key<>? ORDER BY created_at LIMIT 1",
            (player_key,)).fetchone()
        now = time.time()
        if waiting:
            game_id = waiting["game_id"]
            db.execute("UPDATE games SET black_key=?, black_name=?, status='active', updated_at=? WHERE game_id=?",
                       (player_key, player_name, now, game_id))
            row = db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            game = _game_from_row(row)
            color = "black"
            opponent = {"id": row["white_key"], "username": row["white_name"], "online": True}
        else:
            game_id = uuid.uuid4().hex[:12]
            db.execute("INSERT INTO games(game_id,white_key,black_key,white_name,black_name,status,fen,moves,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (game_id, player_key, None, player_name, None, "waiting", chess.Board().fen(), "[]", now, now))
            row = db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
            game = _game_from_row(row)
            color = "white"
            opponent = None
        game["color"] = color
        game["time_control"] = data.get("time_control") or data.get("timeControl") or "10+0"
        if status == "active":
            _broadcast(game_id, {"event": "match_found", "game_id": game_id,
                                 "status": "active", "fen": game["board"].fen(),
                                 "opponent": opponent})
        return game_id, color, opponent, row["status"]


@app.route("/v1/players/online", methods=["GET"])
@app.route("/v1/users/online", methods=["GET"])
@app.route("/v1/presence", methods=["GET"])
def online_players():
    """Return deterministic fake presence for clients that load online users."""
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
    data = _request_data()
    game_id, player_color, opponent, status = create_or_join_game(data)
    payload = {
        "code": 0,
        "status": "matched" if status == "active" else "waiting",
        "success": True,
        "game_id": game_id,
        "gameId": game_id,
        "id": game_id,
        "player": {"color": player_color},
        "color": player_color,
        "time_control": data.get("time_control") or "10+0",
        "timeControl": data.get("timeControl") or "10+0",
        "initial_fen": chess.Board().fen(),
        "fen": chess.Board().fen(),
        "waiting": status != "active",
    }
    if opponent:
        payload["opponent"] = opponent
    return jsonify(payload), 200


@app.route("/v1/matchmaking/status/<game_id>", methods=["GET"])
@app.route("/v1/game/<game_id>", methods=["GET"])
def game_status(game_id):
    game = GAMES.get(game_id)
    if not game:
        with DB_LOCK, _db_connect() as db:
            row = db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone()
        game = _game_from_row(row)
    if not game:
        return jsonify({"code": 404, "status": "not_found", "message": "Game not found"}), 404
    status = game.get("status", "active")
    opponent = None
    if game.get("black_key"):
        opponent = {"id": game["black_key"], "username": game["black_name"], "online": True}
    elif status == "active":
        opponent = {"id": game["white_key"], "username": game["white_name"], "online": True}
    return jsonify({
        "code": 0,
        "status": status,
        "game_id": game_id,
        "gameId": game_id,
        "fen": game["board"].fen(),
        "opponent": opponent,
        "color": game.get("color"),
        "time_control": game.get("time_control", "10+0"),
    }), 200


@app.route("/v1/game/<game_id>/move", methods=["POST"])
@app.route("/v1/matchmaking/<game_id>/move", methods=["POST"])
def play_move(game_id):
    game = GAMES.get(game_id)
    if not game:
        with DB_LOCK, _db_connect() as db:
            game = _game_from_row(db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone())
    if not game:
        return jsonify({"code": 404, "status": "not_found", "message": "Game not found"}), 404
    if game.get("status") != "active" or not game.get("black_key"):
        return jsonify({"code": 409, "status": "waiting", "message": "Waiting for the second real player"}), 409

    payload = request.get_json(silent=True) or {}
    move_uci = payload.get("move") or payload.get("uci") or payload.get("move_uci")
    if not move_uci:
        return jsonify({"code": 400, "status": "error", "message": "A UCI move is required"}), 400
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return jsonify({"code": 400, "status": "error", "message": "Invalid UCI move"}), 400
    with GAME_LOCK:
        board = game["board"]
        if move not in board.legal_moves:
            return jsonify({"code": 400, "status": "error", "message": "Illegal move"}), 400
        board.push(move)
        with DB_LOCK, _db_connect() as db:
            row = db.execute("SELECT moves FROM games WHERE game_id=?", (game_id,)).fetchone()
            moves = json.loads(row["moves"] if row else "[]")
            moves.append(move_uci)
        _persist_game(game_id, board, moves=moves)
    _broadcast(game_id, {"event": "move_played", "game_id": game_id,
                         "move": move_uci, "last_move": move_uci,
                         "fen": board.fen(), "turn": "white" if board.turn else "black"})
    return jsonify({
        "code": 0,
        "status": "ok",
        "game_id": game_id,
        "gameId": game_id,
        "move": move_uci,
        "fen": board.fen(),
        "last_move": move_uci,
        "turn": "white" if board.turn else "black",
    }), 200


@app.route("/v1/matchmaking/cancel", methods=["POST", "DELETE"])
def cancel_matchmaking():
    return jsonify({"code": 0, "status": "cancelled", "success": True}), 200

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
        with DB_LOCK, _db_connect() as db:
            game = _game_from_row(db.execute("SELECT * FROM games WHERE game_id=?", (game_id,)).fetchone())
        if not game:
            ws.send(json.dumps({"event": "error", "message": "Game not found"}))
            return
    game = GAMES[game_id]
    board = game["board"]
    with GAME_LOCK:
        WS_CLIENTS.setdefault(game_id, set()).add(ws)
    ws.send(json.dumps({"event": "connected", "game_id": game_id,
                        "status": game.get("status", "active"), "fen": board.fen()}))

    while True:
        raw_msg = ws.receive()
        if not raw_msg:
            break
        try:
            payload = json.loads(raw_msg)
            if payload.get("action") == "move":
                move_uci = payload.get("move")
                if move_uci:
                    if game.get("status") != "active" or not game.get("black_key"):
                        ws.send(json.dumps({"event": "error", "message": "Waiting for the second real player"}))
                        continue
                    try:
                        move = chess.Move.from_uci(move_uci)
                    except ValueError:
                        ws.send(json.dumps({"event": "error", "message": "Invalid UCI move"}))
                        continue
                    with GAME_LOCK:
                        if move not in board.legal_moves:
                            ws.send(json.dumps({"event": "error", "message": "Illegal move"}))
                            continue
                        board.push(move)
                        with DB_LOCK, _db_connect() as db:
                            row = db.execute("SELECT moves FROM games WHERE game_id=?", (game_id,)).fetchone()
                            moves = json.loads(row["moves"] if row else "[]")
                            moves.append(move_uci)
                        _persist_game(game_id, board, moves=moves)
                        _broadcast(game_id, {"event": "move_played", "game_id": game_id,
                                             "move": move_uci, "last_move": move_uci,
                                             "fen": board.fen()})
        except Exception as err:
            try:
                ws.send(json.dumps({"event": "error", "message": str(err)}))
            except Exception:
                break
    with GAME_LOCK:
        WS_CLIENTS.get(game_id, set()).discard(ws)

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
