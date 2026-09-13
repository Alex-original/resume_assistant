"""
多用户 / 手机号登录的回归测试。

这一层出问题的后果比其他任何 bug 都严重：**数据泄露**。
所以这里测的不是"功能对不对"，而是"门关得严不严"。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db, sms

PHONE = '13800000001'
PHONE2 = '13800000002'


@pytest.fixture()
def solo(tmp_path, monkeypatch):
    """不带登录态的客户端 + 独立的账号库。"""
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
    # 短信走日志兜底（不真发），并让验证码能从响应里拿到，测试才好写
    for k in ('ALIYUN_ACCESS_KEY_ID', 'ALIYUN_ACCESS_KEY_SECRET',
              'SMS_SIGN_NAME', 'SMS_TEMPLATE_CODE'):
        monkeypatch.setitem(config.CONFIG, k, '')
    monkeypatch.setitem(config.CONFIG, 'SMS_DEV_MODE', '1')
    db.init_db()
    from app import main
    return TestClient(main.app)


def _age_codes(phone):
    """把已验证码的时间往前推 2 分钟，绕过 60 秒发送间隔。"""
    with auth.center() as c:
        c.execute('UPDATE sms_code SET created_at = created_at - 120 WHERE phone = ?',
                  (phone,))


def login(client, phone=PHONE) -> dict:
    """走一次完整的发码 + 登录，返回登录响应。

    先把这个号之前的验证码"变老"——否则同一个测试里第二次登录会被
    60 秒发送间隔挡住（那是在测限流，不是在测隔离）。
    """
    _age_codes(phone)
    r = client.post('/api/auth/send-code', json={'phone': phone})
    assert r.status_code == 200, r.text
    code = r.json()['dev_code']
    assert code, '测试里应该能拿到 dev_code'
    return client.post('/api/auth/login', json={'phone': phone, 'code': code}).json()


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

    def test_send_code_is_public(self, solo):
        """发验证码必须在门外就能调——登录前哪来的会话。"""
        assert solo.post('/api/auth/send-code', json={'phone': PHONE}).status_code == 200

    def test_login_then_use(self, solo):
        assert login(solo)['ok'] is True
        assert solo.get('/api/overview').status_code == 200

    def test_logout_closes_the_door(self, solo):
        login(solo)
        assert solo.get('/api/overview').status_code == 200
        solo.post('/api/auth/logout')
        assert solo.get('/api/overview').status_code == 401

    def test_forged_cookie_rejected(self, solo):
        solo.cookies.set(auth.SESSION_COOKIE, '1.9999999999.deadbeef')
        assert solo.get('/api/overview').status_code == 401

    def test_closed_registration_blocks_new_users(self, solo):
        login(solo)                                   # 已有账号
        auth.set_setting('registration_open', '0')
        code = solo.post('/api/auth/send-code', json={'phone': PHONE2}).json()['dev_code']
        blocked = solo.post('/api/auth/login', json={'phone': PHONE2, 'code': code})
        assert blocked.status_code == 403, '关了注册还能建新号'
        # 但已有账号必须还能进——不然一关注册连自己都锁在外面
        assert solo.get('/api/overview').status_code == 200

    def test_deactivated_user_locked_out(self, solo):
        login(solo)
        with auth.center() as c:
            c.execute('UPDATE users SET is_active = 0 WHERE phone = ?', (PHONE,))
        assert solo.get('/api/overview').status_code == 401


class TestPhoneCode:
    """验证码本身的正确性和防刷。"""

    def test_wrong_code_rejected(self, solo):
        solo.post('/api/auth/send-code', json={'phone': PHONE})
        r = solo.post('/api/auth/login', json={'phone': PHONE, 'code': '000000'})
        assert r.status_code == 401

    def test_code_is_single_use(self, solo):
        code = solo.post('/api/auth/send-code', json={'phone': PHONE}).json()['dev_code']
        assert solo.post('/api/auth/login',
                         json={'phone': PHONE, 'code': code}).status_code == 200
        solo.post('/api/auth/logout')
        again = solo.post('/api/auth/login', json={'phone': PHONE, 'code': code})
        assert again.status_code == 401, '同一个验证码居然能用两次'

    def test_bad_phone_rejected(self, solo):
        for bad in ('123', '1234567890', '2380000000', 'abcdefghijk', '138000000012'):
            r = solo.post('/api/auth/send-code', json={'phone': bad})
            assert r.status_code == 400, f'{bad} 应该被拒'

    def test_resend_interval(self, solo):
        solo.post('/api/auth/send-code', json={'phone': PHONE})
        second = solo.post('/api/auth/send-code', json={'phone': PHONE})
        assert second.status_code == 400
        assert '频繁' in second.json()['detail']

    def test_new_code_invalidates_the_old_one(self, solo):
        """重新发一条，之前的必须作废——否则用户输旧的那条会莫名失败。"""
        first = solo.post('/api/auth/send-code', json={'phone': PHONE}).json()['dev_code']
        _age_codes(PHONE)
        second = solo.post('/api/auth/send-code', json={'phone': PHONE}).json()['dev_code']
        assert first != second
        assert solo.post('/api/auth/login',
                         json={'phone': PHONE, 'code': first}).status_code == 401
        assert solo.post('/api/auth/login',
                         json={'phone': PHONE, 'code': second}).status_code == 200

    def test_hourly_limit(self, solo):
        """60 秒间隔挡不住"每分钟一条刷一小时"，所以还有小时上限。"""
        for _ in range(sms.HOURLY_LIMIT):
            _age_codes(PHONE)
            assert solo.post('/api/auth/send-code',
                             json={'phone': PHONE}).status_code == 200
        over = solo.post('/api/auth/send-code', json={'phone': PHONE})
        assert over.status_code == 400, f'每小时上限 {sms.HOURLY_LIMIT} 条没生效'

    def test_expired_code_rejected(self, solo):
        code = solo.post('/api/auth/send-code', json={'phone': PHONE}).json()['dev_code']
        with auth.center() as c:
            c.execute('UPDATE sms_code SET expires_at = 1 WHERE phone = ?', (PHONE,))
        assert solo.post('/api/auth/login',
                         json={'phone': PHONE, 'code': code}).status_code == 401

    def test_dev_code_not_returned_when_dev_mode_off(self, solo, monkeypatch):
        """生产环境不能把验证码回给前端——那等于没有验证码。"""
        monkeypatch.setitem(config.CONFIG, 'SMS_DEV_MODE', '')
        r = solo.post('/api/auth/send-code', json={'phone': PHONE}).json()
        assert r['dev_code'] == '', 'SMS_DEV_MODE 关掉后不该再返回验证码'

    def test_old_login_endpoints_gone(self, solo):
        """用户名密码那套必须彻底没了——留着就是后门。"""
        assert solo.post('/api/auth/register',
                         json={'username': 'x', 'password': 'y'}).status_code in (404, 405)
        r = solo.post('/api/auth/login',
                      json={'username': 'admin', 'password': 'admin12345'})
        assert r.status_code == 422, '用户名密码还能登录！'


class TestDataIsolation:
    """★ 最要紧的一组：朋友之间绝不能互相看见。"""

    def test_users_cannot_see_each_others_data(self, solo):
        login(solo, PHONE)
        solo.post('/api/jobs', json={'company': '甲的公司', 'title': '岗位A',
                                     'raw_text': '甲的 JD'})
        assert [j['company'] for j in solo.get('/api/jobs').json()['jobs']] == ['甲的公司']

        solo.post('/api/auth/logout')
        login(solo, PHONE2)
        assert solo.get('/api/jobs').json()['jobs'] == [], '乙看到了甲的数据！'

        solo.post('/api/jobs', json={'company': '乙的公司', 'title': '岗位B',
                                     'raw_text': '乙的 JD'})
        solo.post('/api/auth/logout')
        login(solo, PHONE)
        assert [j['company'] for j in solo.get('/api/jobs').json()['jobs']] == ['甲的公司']

    def test_each_user_gets_own_database_file(self, solo, tmp_path):
        login(solo, PHONE)
        solo.post('/api/auth/logout')
        login(solo, PHONE2)
        files = sorted(p.name for p in (tmp_path / 'users').glob('u*.db'))
        assert len(files) == 2, f'应该一人一个库文件，实际是 {files}'

    def test_overview_isolated(self, solo):
        login(solo, PHONE)
        solo.post('/api/jobs', json={'company': 'X', 'title': 'Y', 'raw_text': 'JD'})
        assert solo.get('/api/overview').json()['jobs_total'] == 1
        solo.post('/api/auth/logout')
        login(solo, PHONE2)
        assert solo.get('/api/overview').json()['jobs_total'] == 0


class TestQuota:
    """共用一把 key，配额是唯一的刹车。"""

    def test_blocks_after_limit(self, solo, monkeypatch):
        login(solo)
        uid = auth.get_user_by_phone(PHONE)['id']
        with auth.center() as c:
            c.execute('UPDATE users SET daily_quota = 3 WHERE id = ?', (uid,))
        auth.record_usage(uid, 'chat', calls=3)

        db.set_current_user(uid)
        from app import clients
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-fake')
        with pytest.raises(clients.ServiceError, match='上限'):
            clients.chat([{'role': 'user', 'content': 'hi'}])

    def test_usage_accumulates(self, solo):
        login(solo)
        uid = auth.get_user_by_phone(PHONE)['id']
        assert auth.used_today(uid) == 0
        auth.record_usage(uid, 'chat')
        auth.record_usage(uid, 'vision')
        assert auth.used_today(uid) == 2

    def test_zero_quota_means_unlimited(self, solo):
        login(solo)
        uid = auth.get_user_by_phone(PHONE)['id']
        with auth.center() as c:
            c.execute('UPDATE users SET daily_quota = 0 WHERE id = ?', (uid,))
        auth.record_usage(uid, 'chat', calls=9999)
        assert auth.quota_state(auth.get_user(uid))['exceeded'] is False


class TestSessionMechanics:
    """cookie 本身的安全性。"""

    def test_round_trip(self):
        assert auth.read_session(auth.make_session(42)) == 42

    def test_tampered_user_id_rejected(self):
        _, exp, sig = auth.make_session(1).split('.')
        assert auth.read_session(f'2.{exp}.{sig}') is None

    def test_tampered_signature_rejected(self):
        token = auth.make_session(1)
        assert auth.read_session(f'{token[:-6]}abcdef') is None

    def test_expired_rejected(self):
        import hashlib
        import hmac
        payload = '1.1000000000'
        sig = hmac.new(auth._secret(), payload.encode(), hashlib.sha256).hexdigest()
        assert auth.read_session(f'{payload}.{sig}') is None

    def test_garbage_rejected(self):
        for bad in ('', 'x', 'a.b', 'a.b.c.d', '...', '1.2.3'):
            assert auth.read_session(bad) is None


class TestMigrationToPhone:
    """
    ★ 回归：从「用户名+密码」迁到「手机号」时，**不能把人锁在外面**。

    老表 username/password_hash 都是 NOT NULL，SQLite 改不了列约束，
    只能重建表；重建时要把管理员绑到 ADMIN_PHONE 上。
    """

    def _old_schema(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
        monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
        monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
        db.init_db()
        with auth.center() as c:
            c.executescript('DROP TABLE users;')
            c.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    daily_quota INTEGER NOT NULL DEFAULT 300,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL DEFAULT ''
                );
            ''')
            c.execute("INSERT INTO users (username, display_name, password_hash, is_admin,"
                      " daily_quota, created_at) VALUES ('admin','管理员','pbkdf2$x$y',1,"
                      " 500,'2026-01-01 00:00:00')")

    def test_admin_is_bound_to_phone(self, tmp_path, monkeypatch):
        self._old_schema(tmp_path, monkeypatch)
        out = auth.migrate_to_phone_login(PHONE)
        assert out['migrated'] is True and out['admin_bound'] is True
        admin = auth.get_user_by_phone(PHONE)
        assert admin is not None and admin['is_admin'] == 1
        assert admin['daily_quota'] == 500, '额度没保留'
        assert admin['display_name'] == '管理员', '昵称没保留'

    def test_idempotent(self, tmp_path, monkeypatch):
        self._old_schema(tmp_path, monkeypatch)
        auth.migrate_to_phone_login(PHONE)
        assert auth.migrate_to_phone_login(PHONE)['migrated'] is False

    def test_without_admin_phone_nobody_can_login(self, tmp_path, monkeypatch):
        """没配 ADMIN_PHONE 时要能看出来"没人能登录"，而不是悄悄失败。"""
        self._old_schema(tmp_path, monkeypatch)
        out = auth.migrate_to_phone_login('')
        assert out['admin_bound'] is False
        with auth.center() as c:
            ok = c.execute("SELECT COUNT(*) AS n FROM users WHERE phone LIKE '1%' "
                           "AND LENGTH(phone)=11").fetchone()['n']
        assert ok == 0


class TestNoSecretsLeak:
    """接口响应里绝不能出现别人的信息、密码、密钥。"""

    def test_me_masks_phone(self, solo):
        login(solo)
        body = solo.get('/api/auth/me').json()
        assert body['user']['phone_masked'].endswith(PHONE[-4:])
        assert '****' in body['user']['phone_masked']

    def test_no_password_fields_anywhere(self, solo):
        login(solo)
        body = solo.get('/api/auth/me').text.lower()
        assert 'password' not in body and 'pbkdf2' not in body

    def test_settings_masks_keys(self, solo, monkeypatch):
        login(solo)
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-abcdef1234567890xyz')
        assert 'sk-abcdef1234567890xyz' not in solo.get('/api/settings').text


class TestOrphanAccounts:
    """
    ★ 回归：迁移后**有数据但没手机号**的账号会被锁在门外。

    真实撞到过：用户注册的普通账号（jiaxiong）里有真实简历、岗位、面试记录，
    但迁移时手机号默认绑给了空的管理员账号——那些数据等于丢了。
    所以迁移要主动报出来，并且要有办法把手机号挪过去。
    """

    def _two_accounts(self, tmp_path, monkeypatch):
        """造一个"管理员空着 + 普通账号有数据"的局面。"""
        monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
        monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
        monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
        db.init_db()
        admin = auth.create_user('13800000001', '管理员', is_admin=True)
        worker = auth.create_user('13800000002', 'jiaxiong')
        db.set_current_user(worker['id'])
        with db.session() as c:
            c.execute("INSERT INTO job (company,title,city,salary,raw_text,status,"
                      "created_at,updated_at) VALUES ('真公司','真岗位','','','JD',"
                      "'to_apply','','')")
        db.set_current_user(None)
        return admin, worker

    def test_orphans_are_reported(self, tmp_path, monkeypatch):
        """迁移要能把"没绑上手机号"的账号列出来。"""
        monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
        monkeypatch.setattr(auth, 'CENTER_DB', tmp_path / 'app.db')
        monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
        db.init_db()
        with auth.center() as c:
            c.executescript('DROP TABLE users;')
            c.executescript('''
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    daily_quota INTEGER NOT NULL DEFAULT 300,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL DEFAULT ''
                );
            ''')
            c.execute("INSERT INTO users (username,display_name,password_hash,is_admin,"
                      "created_at) VALUES ('admin','管理员','x',1,'2026-01-01')")
            c.execute("INSERT INTO users (username,display_name,password_hash,is_admin,"
                      "created_at) VALUES ('jiaxiong','jiaxiong','x',0,'2026-01-02')")

        out = auth.migrate_to_phone_login('13800000001')
        assert out['migrated'] is True
        names = [o['name'] for o in out['orphans']]
        assert 'jiaxiong' in names, f'有数据的普通账号没被报出来：{out["orphans"]}'
        assert len(out['orphans']) == 1, '管理员不该出现在 orphan 里'

    def test_accounts_without_phone_lists_them(self, tmp_path, monkeypatch):
        self._two_accounts(tmp_path, monkeypatch)
        with auth.center() as c:
            c.execute("UPDATE users SET phone = 'imported-2' WHERE id = 2")
        ids = [u['id'] for u in auth.accounts_without_phone()]
        assert 2 in ids

    def test_bind_phone_moves_it_to_the_data_owner(self, tmp_path, monkeypatch):
        """手机号能从空账号挪到有数据的账号上，数据跟着回来。"""
        admin, worker = self._two_accounts(tmp_path, monkeypatch)
        assert auth.get_user_by_phone('13800000001')['id'] == admin['id']

        moved = auth.bind_phone(worker['id'], '13800000001')
        assert moved['id'] == worker['id']
        assert auth.get_user_by_phone('13800000001')['id'] == worker['id']

        db.set_current_user(worker['id'])
        with db.session() as c:
            assert c.execute('SELECT COUNT(*) FROM job').fetchone()[0] == 1
        db.set_current_user(None)

        # 原号主被腾退，不再占用这个手机号
        old = auth.get_user(admin['id'])
        assert old['phone'] != '13800000001'

    def test_bind_phone_rejects_bad_input(self, tmp_path, monkeypatch):
        admin, worker = self._two_accounts(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            auth.bind_phone(worker['id'], '123')
        with pytest.raises(ValueError):
            auth.bind_phone(999999, '13800000003')
