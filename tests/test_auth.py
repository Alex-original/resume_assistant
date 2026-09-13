"""
多用户与鉴权的回归测试。

这一层出问题的后果比其他任何 bug 都严重：**数据泄露**。
所以这里测的不是"功能对不对"，而是"门关得严不严"。
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db


@pytest.fixture()
def solo(tmp_path, monkeypatch):
    """不带登录态的客户端 + 独立的账号库。"""
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
    db.init_db()
    from app import main
    return TestClient(main.app)


class TestAuthGate:
    """门禁：没登录什么都别想拿到。"""

    def test_page_redirects_to_login(self, solo):
        r = solo.get('/', follow_redirects=False)
        assert r.status_code == 302 and r.headers['location'] == '/login'

    def test_api_returns_401(self, solo):
        for path in ('/api/overview', '/api/jobs', '/api/resumes', '/api/materials',
                     '/api/interviews', '/api/settings'):
            assert solo.get(path).status_code == 401, f'{path} 居然没拦'

    def test_login_page_public(self, solo):
        assert solo.get('/login').status_code == 200

    def test_static_public(self, solo):
        assert solo.get('/static/js/app.js').status_code == 200

    def test_health_public(self, solo):
        assert solo.get('/health').json() == {'ok': True}

    def test_register_and_use(self, solo):
        r = solo.post('/api/auth/register',
                      json={'username': 'zhangsan', 'password': 'pw123456'})
        assert r.status_code == 200
        assert solo.get('/api/overview').status_code == 200

    def test_wrong_password_rejected(self, solo):
        solo.post('/api/auth/register', json={'username': 'ua1', 'password': 'pw123456'})
        solo.post('/api/auth/logout')
        assert solo.post('/api/auth/login',
                         json={'username': 'ua1', 'password': 'wrong'}).status_code == 401

    def test_short_password_rejected(self, solo):
        assert solo.post('/api/auth/register',
                         json={'username': 'ub1', 'password': '123'}).status_code == 400

    def test_duplicate_username_rejected(self, solo):
        solo.post('/api/auth/register', json={'username': 'uc1', 'password': 'pw123456'})
        assert solo.post('/api/auth/register',
                         json={'username': 'uc1', 'password': 'pw123456'}).status_code == 400

    def test_logout_closes_the_door(self, solo):
        solo.post('/api/auth/register', json={'username': 'ud1', 'password': 'pw123456'})
        assert solo.get('/api/overview').status_code == 200
        solo.post('/api/auth/logout')
        assert solo.get('/api/overview').status_code == 401

    def test_forged_cookie_rejected(self, solo):
        """自己造一张 cookie 是进不来的——签名对不上。"""
        solo.cookies.set(auth.SESSION_COOKIE, '1.9999999999.deadbeef')
        assert solo.get('/api/overview').status_code == 401

    def test_cookie_without_signature_rejected(self, solo):
        solo.cookies.set(auth.SESSION_COOKIE, '1.9999999999')
        assert solo.get('/api/overview').status_code == 401

    def test_closed_registration_blocks_new_users(self, solo):
        solo.post('/api/auth/register', json={'username': 'ue1', 'password': 'pw123456'})
        auth.set_setting('registration_open', '0')
        r = solo.post('/api/auth/register',
                      json={'username': 'ue2', 'password': 'pw123456'})
        assert r.status_code == 403
        # 已有账号不受影响
        assert solo.get('/api/overview').status_code == 200

    def test_deactivated_user_locked_out(self, solo):
        solo.post('/api/auth/register', json={'username': 'uf1', 'password': 'pw123456'})
        with auth.center() as c:
            c.execute("UPDATE users SET is_active = 0 WHERE username = 'uf1'")
        assert solo.get('/api/overview').status_code == 401


class TestDataIsolation:
    """★ 最要紧的一组：朋友之间绝不能互相看见。"""

    def _fresh(self):
        from app import main
        return TestClient(main.app)

    def test_users_cannot_see_each_others_data(self, solo):
        # alice 建一个岗位
        solo.post('/api/auth/register', json={'username': 'alice', 'password': 'pw123456'})
        solo.post('/api/jobs', json={'company': '爱丽丝的公司', 'title': '岗位A',
                                     'raw_text': '这是爱丽丝的 JD'})
        assert [j['company'] for j in solo.get('/api/jobs').json()['jobs']] == ['爱丽丝的公司']

        # bob 登录：必须一个都看不到
        solo.post('/api/auth/logout')
        solo.post('/api/auth/register', json={'username': 'bob', 'password': 'pw123456'})
        assert solo.get('/api/jobs').json()['jobs'] == [], 'bob 看到了 alice 的岗位！'

        # bob 建自己的
        solo.post('/api/jobs', json={'company': '鲍勃的公司', 'title': '岗位B',
                                     'raw_text': '这是鲍勃的 JD'})
        assert [j['company'] for j in solo.get('/api/jobs').json()['jobs']] == ['鲍勃的公司']

        # alice 回来：还是只有自己的
        solo.post('/api/auth/logout')
        solo.post('/api/auth/login', json={'username': 'alice', 'password': 'pw123456'})
        assert [j['company'] for j in solo.get('/api/jobs').json()['jobs']] == ['爱丽丝的公司']

    def test_each_user_gets_own_database_file(self, solo, tmp_path):
        solo.post('/api/auth/register', json={'username': 'uu1', 'password': 'pw123456'})
        solo.post('/api/auth/logout')
        solo.post('/api/auth/register', json={'username': 'uu2', 'password': 'pw123456'})
        files = sorted(p.name for p in (tmp_path / 'users').glob('u*.db'))
        assert len(files) == 2, f'应该一人一个库文件，实际是 {files}'

    def test_profile_isolated(self, solo):
        solo.post('/api/auth/register', json={'username': 'up1', 'password': 'pw123456'})
        solo.post('/api/jobs', json={'company': 'X', 'title': 'Y', 'raw_text': 'JD'})
        n1 = solo.get('/api/overview').json()['jobs_total']

        solo.post('/api/auth/logout')
        solo.post('/api/auth/register', json={'username': 'up2', 'password': 'pw123456'})
        assert solo.get('/api/overview').json()['jobs_total'] == 0
        assert n1 == 1


class TestQuota:
    """共用一把 key，配额是唯一的刹车。"""

    def test_blocks_after_limit(self, solo, monkeypatch):
        solo.post('/api/auth/register', json={'username': 'uq1', 'password': 'pw123456'})
        uid = auth.get_user_by_name('uq1')['id']
        with auth.center() as c:
            c.execute('UPDATE users SET daily_quota = 3 WHERE id = ?', (uid,))
        auth.record_usage(uid, 'chat', calls=3)

        db.set_current_user(uid)
        from app import clients
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-fake')
        with pytest.raises(clients.ServiceError, match='上限'):
            clients.chat([{'role': 'user', 'content': 'hi'}])

    def test_under_limit_passes(self, solo, monkeypatch):
        solo.post('/api/auth/register', json={'username': 'uq2', 'password': 'pw123456'})
        uid = auth.get_user_by_name('uq2')['id']
        db.set_current_user(uid)
        from app import clients
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-fake')
        # 真的会发请求，所以会失败在网络上——但**不能**失败在配额上
        try:
            clients.chat([{'role': 'user', 'content': 'hi'}])
        except clients.ServiceError as exc:
            assert '上限' not in str(exc), '没超额度却被拦了'

    def test_usage_accumulates(self, solo):
        solo.post('/api/auth/register', json={'username': 'uq3', 'password': 'pw123456'})
        uid = auth.get_user_by_name('uq3')['id']
        assert auth.used_today(uid) == 0
        auth.record_usage(uid, 'chat')
        auth.record_usage(uid, 'vision')
        assert auth.used_today(uid) == 2

    def test_zero_quota_means_unlimited(self, solo):
        solo.post('/api/auth/register', json={'username': 'uq4', 'password': 'pw123456'})
        uid = auth.get_user_by_name('uq4')['id']
        with auth.center() as c:
            c.execute('UPDATE users SET daily_quota = 0 WHERE id = ?', (uid,))
        auth.record_usage(uid, 'chat', calls=9999)
        assert auth.quota_state(auth.get_user(uid))['exceeded'] is False


class TestSessionMechanics:
    """cookie 本身的安全性。"""

    def test_round_trip(self):
        token = auth.make_session(42)
        assert auth.read_session(token) == 42

    def test_tampered_user_id_rejected(self):
        token = auth.make_session(1)
        _, exp, sig = token.split('.')
        assert auth.read_session(f'2.{exp}.{sig}') is None

    def test_tampered_signature_rejected(self):
        token = auth.make_session(1)
        assert auth.read_session(f'{token[:-6]}abcdef') is None

    def test_expired_rejected(self):
        payload = '1.1000000000'
        import hashlib
        import hmac
        sig = hmac.new(auth._secret(), payload.encode(), hashlib.sha256).hexdigest()
        assert auth.read_session(f'{payload}.{sig}') is None

    def test_garbage_rejected(self):
        for bad in ('', 'x', 'a.b', 'a.b.c.d', '...', '1.2.3'):
            assert auth.read_session(bad) is None

    def test_password_hashes_are_salted(self):
        a = auth._hash_password('samepassword')
        b = auth._hash_password('samepassword')
        assert a != b, '两次哈希应该不同（salt 不同）'
        assert auth._verify_password('samepassword', a)
        assert not auth._verify_password('otherpassword', a)


class TestNoSecretsLeak:
    """接口响应里绝不能出现密码哈希、密钥、别人的信息。"""

    def test_me_never_returns_hash(self, solo):
        solo.post('/api/auth/register',
                  json={'username': 'us1', 'password': 'pw123456', 'display_name': '小一'})
        body = solo.get('/api/auth/me').text
        assert 'password' not in body.lower()
        assert 'pbkdf2' not in body
        assert '小一' in body

    def test_login_response_never_returns_hash(self, solo):
        r = solo.post('/api/auth/register', json={'username': 'us2', 'password': 'pw123456'})
        assert 'pbkdf2' not in r.text and 'salt' not in r.text.lower()

    def test_settings_masks_keys(self, solo, monkeypatch):
        solo.post('/api/auth/register', json={'username': 'us3', 'password': 'pw123456'})
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-abcdef1234567890xyz')
        body = solo.get('/api/settings').text
        assert 'sk-abcdef1234567890xyz' not in body


class TestLegacyDataAdoption:
    """
    ★ 回归：升级到多用户后，**老数据不能凭空消失**。

    加多用户之前数据都在 data/job_assistant.db；加完之后登录进来看到的是自己的空库。
    所以建号时要有一次"认领"：把老库复制给第一个管理员。
    """

    def test_adopts_legacy_db_for_admin(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
        legacy = tmp_path / 'legacy.db'
        monkeypatch.setattr(db, 'DB_PATH', legacy)
        monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
        db.init_db()
        # 往"老库"里塞点东西
        db.set_current_user(None)
        with db.session() as c:
            c.execute("INSERT INTO job (company,title,city,salary,raw_text,status,"
                      "created_at,updated_at) VALUES ('老公司','老岗位','','','JD','to_apply','','')")
        assert auth.adopt_legacy_data.__doc__

        admin = auth.create_user('boss', 'pw123456', is_admin=True)
        out = auth.adopt_legacy_data(admin['id'])
        assert out['adopted'] is True
        assert out['rows'] >= 1

        db.set_current_user(admin['id'])
        with db.session() as c:
            n = c.execute('SELECT COUNT(*) FROM job').fetchone()[0]
        assert n == 1, '老数据没被交给管理员'
        db.set_current_user(None)

    def test_does_not_overwrite_existing_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
        monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'legacy.db')
        monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
        db.init_db()
        db.set_current_user(None)
        with db.session() as c:
            c.execute("INSERT INTO job (company,title,city,salary,raw_text,status,"
                      "created_at,updated_at) VALUES ('老公司','老岗位','','','JD','to_apply','','')")

        u = auth.create_user('boss2', 'pw123456')
        # 先自己建数据
        db.set_current_user(u['id'])
        with db.session() as c:
            c.execute("INSERT INTO job (company,title,city,salary,raw_text,status,"
                      "created_at,updated_at) VALUES ('自己的','岗位','','','JD','to_apply','','')")
        db.set_current_user(None)

        out = auth.adopt_legacy_data(u['id'])
        assert out['adopted'] is False, '已经有数据了就不该再覆盖'
        db.set_current_user(u['id'])
        with db.session() as c:
            names = [r[0] for r in c.execute('SELECT company FROM job')]
        assert names == ['自己的'], f'自己的数据被覆盖了：{names}'
        db.set_current_user(None)

    def test_empty_legacy_db_is_not_adopted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
        monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'legacy.db')
        monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
        db.init_db()
        u = auth.create_user('boss3', 'pw123456')
        assert auth.adopt_legacy_data(u['id'])['adopted'] is False
