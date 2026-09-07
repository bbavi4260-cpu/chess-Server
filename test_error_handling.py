from server import app

client = app.test_client()

# Non-object JSON must produce a JSON error rather than an AttributeError/HTML 500.
response = client.post("/v1/game/missing/move", json=["e2e4"])
assert response.status_code == 404
assert isinstance(response.get_json(), dict)

response = client.post("/cometd", json=["not-an-object"])
assert response.status_code == 400
assert response.get_json()["code"] == 400

handshake = client.post("/cometd", json={"channel": "/meta/handshake", "id": "1"})
assert handshake.status_code == 200
client_id = handshake.get_json()["clientId"]
connect = client.post("/cometd", json={"channel": "/meta/connect", "clientId": client_id, "id": "2"})
assert connect.status_code == 200
assert connect.get_json()["successful"] is True
assert connect.get_json()["clientId"] == client_id

disconnect = client.post("/cometd", json={"channel": "/meta/disconnect", "clientId": client_id, "id": "3"})
assert disconnect.status_code == 200
assert disconnect.get_json()["successful"] is True

response = client.post("/v1/matchmaking/find", data="[]", content_type="application/json")
assert response.status_code == 200
assert response.get_json()["success"] is True

response = client.get("/v1/config", headers={"X-Forwarded-Proto": "https", "Host": "example.onrender.com"})
assert response.status_code == 200
assert response.get_json()["endpoints"]["websocket"].startswith("wss://")
assert "{game_id}" in response.get_json()["endpoints"]["websocket_template"]

response = client.get("/does-not-exist")
assert response.status_code == 404
assert response.is_json
print("PASS malformed payload and Render proxy regression checks")
