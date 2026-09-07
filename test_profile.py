from server import app

client = app.test_client()
created = client.post('/v1/users', json={'username': 'profile_user', 'email': 'profile@example.com'})
payload = created.get_json()
user_id = payload['data']['id']

for path in ('/v1/users/me', '/v1/me', '/v1/users/profile', '/v1/profile', '/v1/account'):
    response = client.get(path)
    assert response.status_code == 200, (path, response.get_json())
    assert response.get_json()['data']['id'] == user_id

for path in ('/v1/users/profile_user', f'/v1/users/{user_id}'):
    response = client.get(path)
    assert response.status_code == 200, (path, response.get_json())
    assert response.get_json()['data']['id'] == user_id

print('PASS profile aliases')
print('PASS username profile lookup')
print('PASS numeric ID profile lookup')
