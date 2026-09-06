import os
import json
import uuid
import chess
import chess.engine
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock

app = Flask(__name__)
CORS(app)
sock = Sock(app)

# In-memory database for active games
# Structure: { game_id: {"board": chess.Board(), "white": "player", "black": "bot"} }
GAMES = {}

# -------------------------------------------------------------
# 1. REST API ENDPOINTS (api.chess.com style)
# -------------------------------------------------------------

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({
        "status": "online",
        "server": "Chess API & Live Server",
        "active_games": len(GAMES)
    }), 200

@app.route("/api/v1/game/new", methods=["POST"])
def create_game():
    """Create a new game. Accepts JSON body like: {"vs_bot": true}"""
    data = request.get_json(silent=True) or {}
    game_id = str(uuid.uuid4())[:8]
    
    GAMES[game_id] = {
        "board": chess.Board(),
        "vs_bot": data.get("vs_bot", False)
    }
    
    return jsonify({
        "game_id": game_id,
        "fen": GAMES[game_id]["board"].fen(),
        "vs_bot": GAMES[game_id]["vs_bot"],
        "status": "created"
    }), 201

@app.route("/api/v1/game/<game_id>", methods=["GET"])
def get_game(game_id):
    """Get status and legal moves for an ongoing game."""
    game = GAMES.get(game_id)
    if not game:
        return jsonify({"error": "Game not found"}), 404
        
    board = game["board"]
    return jsonify({
        "game_id": game_id,
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "is_game_over": board.is_game_over(),
        "legal_moves": [move.uci() for move in board.legal_moves]
    }), 200

@app.route("/api/v1/game/<game_id>/move", methods=["POST"])
def make_move(game_id):
    """Play a move via REST. Body: {"move": "e2e4"}"""
    game = GAMES.get(game_id)
    if not game:
        return jsonify({"error": "Game not found"}), 404
        
    board = game["board"]
    data = request.get_json(silent=True) or {}
    move_uci = data.get("move")

    try:
        move = chess.Move.from_uci(move_uci)
        if move in board.legal_moves:
            board.push(move)
            return jsonify({
                "status": "success",
                "fen": board.fen(),
                "turn": "white" if board.turn == chess.WHITE else "black",
                "is_game_over": board.is_game_over()
            }), 200
        else:
            return jsonify({"error": "Illegal move"}), 400
    except Exception as e:
        return jsonify({"error": f"Invalid move format: {str(e)}"}), 400


# -------------------------------------------------------------
# 2. COMETD EMULATION ROUTE (Chess.com compatibility)
# -------------------------------------------------------------

@app.route("/cometd", methods=["POST"])
def cometd_handshake():
    """Emulates CometD/Bayeux protocol responses used by Chess.com clients."""
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
# 3. WEBSOCKET REAL-TIME SERVER (live.chess.com style)
# -------------------------------------------------------------

@sock.route("/ws/live/<game_id>")
def live_websocket(ws, game_id):
    """Handles real-time WebSocket connections for live moves."""
    if game_id not in GAMES:
        GAMES[game_id] = {"board": chess.Board(), "vs_bot": False}
        
    board = GAMES[game_id]["board"]

    # Send initial state
    ws.send(json.dumps({
        "event": "connected",
        "game_id": game_id,
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black"
    }))

    while True:
        raw_msg = ws.receive()
        if not raw_msg:
            break

        try:
            payload = json.loads(raw_msg)
            
            # Action format: {"action": "move", "move": "e2e4"}
            if payload.get("action") == "move":
                move_uci = payload.get("move")
                move = chess.Move.from_uci(move_uci)

                if move in board.legal_moves:
                    board.push(move)
                    
                    # Broadcast user move
                    ws.send(json.dumps({
                        "event": "move_played",
                        "fen": board.fen(),
                        "turn": "white" if board.turn == chess.WHITE else "black",
                        "is_game_over": board.is_game_over()
                    }))
                else:
                    ws.send(json.dumps({"event": "error", "message": "Illegal move"}))

        except Exception as err:
            ws.send(json.dumps({"event": "error", "message": str(err)}))


# -------------------------------------------------------------
# 4. SERVER RUNNER
# -------------------------------------------------------------

if __name__ == "__main__":
    # Binds to $PORT for Render deployment, defaults to 8000 for local dev
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
