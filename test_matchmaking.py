from server import app

client = app.test_client()

for path in ("/v1/players/online", "/v1/users/online", "/v1/presence"):
    response = client.get(path)
    assert response.status_code == 200
    assert isinstance(response.get_json(), dict)
    assert response.get_json()["data"]["players"]
    print(f"PASS {path}: fake online players returned")

response = client.post("/v1/matchmaking/find", json={"time_control": "10+0"})
assert response.status_code == 200
match = response.get_json()
assert match["status"] == "matched"
assert match["success"] is True
assert match["game_id"] == match["gameId"]
game_id = match["game_id"]
print(f"PASS matchmaking: matched {game_id} vs {match['opponent']['username']}")

status = client.get(f"/v1/game/{game_id}")
assert status.status_code == 200
assert status.get_json()["game_id"] == game_id
print("PASS game status")

move = client.post(f"/v1/game/{game_id}/move", json={"move": "e2e4"})
assert move.status_code == 200
assert move.get_json()["fen"] != match["fen"]
print("PASS HTTP move e2e4")

cancel = client.post("/v1/matchmaking/cancel")
assert cancel.status_code == 200
assert cancel.get_json()["status"] == "cancelled"
print("PASS matchmaking cancel")
