from server import app

client = app.test_client()

check = client.get('/v1/users/validate-username/hehehe')
assert check.status_code == 200
assert check.get_json()['code'] == 0
assert check.get_json()['available'] is True
print('PASS username validation')

signup = client.post('/v1/users', json={'username': 'hehehhe', 'email': 'test@example.com'})
assert signup.status_code == 200
signup_json = signup.get_json()
assert signup_json['code'] == 0
assert signup_json['success'] is True
assert signup_json['token']
assert signup_json['data']['user']['username'] == 'hehehhe'
print('PASS signup root and data response')

login = client.post('/v1/auth/login', json={'username': 'hehehhe'})
assert login.status_code == 200
assert login.get_json()['code'] == 0
print('PASS login')

wrapped = client.post('/v1/auth/register', json={'data': {'username': 'wrapped_user'}})
assert wrapped.status_code == 200
assert wrapped.get_json()['data']['user']['username'] == 'wrapped_user'
print('PASS wrapped signup')

guest = client.post('/v1/users/guest-login', json={})
assert guest.status_code == 200
assert guest.get_json()['code'] == 0
print('PASS guest login')
