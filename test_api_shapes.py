from server import app

client = app.test_client()

checks = [
    ("GET", "/v1/config", "object", 200),
    ("POST", "/v1/users/guest-login", "object", 200),
    ("GET", "/v1/computer/bot-personalities", "array", 200),
    ("GET", "/v1/watch", "object", 200),
    ("GET", "/v1/tv/show", "object", 200),
    ("GET", "/v1/puzzles/daily/today", "object", 200),
    ("GET", "/v1/tactics-batch?includeTacticsIds=606032,41839", "object", 200),
    ("GET", "/v1/home", "object", 200),
]

for method, path, expected_type, expected_status in checks:
    response = client.open(path, method=method)
    payload = response.get_json()
    actual_type = "array" if isinstance(payload, list) else "object" if isinstance(payload, dict) else type(payload).__name__
    assert response.status_code == expected_status, (path, response.status_code, payload)
    assert actual_type == expected_type, (path, actual_type, payload)
    print(f"PASS {method} {path}: HTTP {response.status_code}, JSON {actual_type}")

unknown = client.get("/unknown-endpoint")
assert unknown.status_code == 404
assert isinstance(unknown.get_json(), dict)
print("PASS unknown endpoint: HTTP 404, JSON object")
