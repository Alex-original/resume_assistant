"""
多用户：账号、会话、数据隔离。

## 为什么是「一人一个数据库文件」

这个应用的数据隔离要求很简单：**每个人只能看到自己的东西**。
两种做法：

    A. 16 张表全加 user_id，每条 SQL 都加 `WHERE user_id = ?`
    B. 一人一个 .db 文件，连接时选库

选 B。理由很直接：现有代码是**手写 SQL**（57 处 `db.session()`），
方案 A 只要漏一处 WHERE 就是数据泄露——朋友能看到你的简历、手机号、面试记录。
方案 B 靠文件物理隔离，**结构上就不可能串**，而且业务代码一行都不用改。

代价是无法做跨用户的共享/统计——但这个产品本来就不需要。

## 会话怎么做的

签名 cookie，不引入额外依赖：`uid.过期时间. HMAC-SHA256(secret, ...)`。
密码用 pbkdf2_hmac（标准库）+ 每人独立 salt。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from . import config, db

SESSION_COOKIE = 'ja_session'
SESSION_DAYS = 30

# 中心库：只放账号和用量，不放业务数据
CENTER_DB = config.DATA_DIR / 'app.db'


def _secret() -> bytes:
    """
    签名密钥。优先读环境变量；没有就生成一个存到 data/.secret。

    生产部署时请在 .env 里显式设置 SECRET_KEY——否则容器重建后
    文件丢了，所有人的登录态会一起失效。
    """
    env = config.get('SECRET_KEY')
    if env:
        return env.encode()
    path = config.DATA_DIR / '.secret'
    if path.is_file():
        return path.read_bytes()
    value = secrets.token_bytes(32)
    path.write_bytes(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


# ══════════════════ 中心库 ══════════════════

CENTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    display_name  TEXT    NOT NULL DEFAULT '',
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    daily_quota   INTEGER NOT NULL DEFAULT 300,   -- 每人每天能调用多少次 AI
    created_at    TEXT    NOT NULL,
    last_login_at TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS usage_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    day      TEXT    NOT NULL,          -- YYYY-MM-DD
    kind     TEXT    NOT NULL,          -- chat | vision | asr | tts
    calls    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_log(user_id, day);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def center() -> sqlite3.Connection:
    CENTER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CENTER_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    conn.executescript(CENTER_SCHEMA)
    return conn


def get_setting(key: str, default: str = '') -> str:
    with center() as c:
        row = c.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_setting(key: str, value: str) -> None:
    with center() as c:
        c.execute('INSERT INTO settings (key, value) VALUES (?,?) '
                  'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))


def registration_open() -> bool:
    return get_setting('registration_open', '1') == '1'


# ══════════════════ 账号 ══════════════════

def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 120_000)
    return f'pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}'


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split('$')
        salt = base64.b64decode(salt_b64)
        expect = base64.b64decode(dk_b64)
    except (ValueError, TypeError):
        return False
    got = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 120_000)
    return hmac.compare_digest(got, expect)


def user_db_path(user_id: int) -> Path:
    """每个人的业务数据单独一个文件。"""
    d = config.DATA_DIR / 'users'
    d.mkdir(parents=True, exist_ok=True)
    return d / f'u{user_id}.db'


def create_user(username: str, password: str, display_name: str = '',
                is_admin: bool = False) -> dict:
    username = (username or '').strip()
    if len(username) < 3:
        raise ValueError('用户名至少 3 个字符')
    if len(password or '') < 6:
        raise ValueError('密码至少 6 位')
    with center() as c:
        exists = c.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if exists:
            raise ValueError('这个用户名已经被用了')
        cur = c.execute(
            'INSERT INTO users (username, display_name, password_hash, is_admin, '
            'created_at) VALUES (?,?,?,?,?)',
            (username, display_name.strip() or username, _hash_password(password),
             1 if is_admin else 0, db.now_iso()))
        uid = int(cur.lastrowid)
    # 立刻把个人库建出来，避免第一次请求时才发现问题
    db.init_user_db(uid)
    return get_user(uid)


def get_user(user_id: int) -> dict | None:
    with center() as c:
        row = c.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(username: str) -> dict | None:
    with center() as c:
        row = c.execute('SELECT * FROM users WHERE username = ?',
                        ((username or '').strip(),)).fetchone()
    return dict(row) if row else None


def authenticate(username: str, password: str) -> dict | None:
    user = get_user_by_name(username)
    if not user or not user['is_active']:
        return None
    if not _verify_password(password, user['password_hash']):
        return None
    with center() as c:
        c.execute('UPDATE users SET last_login_at = ? WHERE id = ?',
                  (db.now_iso(), user['id']))
    return user


def list_users() -> list[dict]:
    with center() as c:
        rows = c.execute(
            'SELECT id, username, display_name, is_admin, is_active, daily_quota, '
            'created_at, last_login_at FROM users ORDER BY id').fetchall()
    return [dict(r) for r in rows]


def set_password(user_id: int, password: str) -> None:
    if len(password or '') < 6:
        raise ValueError('密码至少 6 位')
    with center() as c:
        c.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                  (_hash_password(password), user_id))


# ══════════════════ 会话签名 ══════════════════

def make_session(user_id: int) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f'{user_id}.{exp}'
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}.{sig}'


def read_session(token: str) -> int | None:
    """验签 + 查过期。任何一步不对就返回 None。"""
    if not token:
        return None
    try:
        uid_s, exp_s, sig = token.split('.')
        payload = f'{uid_s}.{exp_s}'
    except ValueError:
        return None
    expect = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        if int(exp_s) < time.time():
            return None
        return int(uid_s)
    except ValueError:
        return None


# ══════════════════ 用量配额 ══════════════════

def today() -> str:
    return time.strftime('%Y-%m-%d')


def used_today(user_id: int) -> int:
    with center() as c:
        row = c.execute('SELECT COALESCE(SUM(calls),0) AS n FROM usage_log '
                        'WHERE user_id = ? AND day = ?', (user_id, today())).fetchone()
    return int(row['n'])


def record_usage(user_id: int, kind: str = 'chat', calls: int = 1) -> None:
    with center() as c:
        c.execute('INSERT INTO usage_log (user_id, day, kind, calls) VALUES (?,?,?,?)',
                  (user_id, today(), kind, calls))


def quota_state(user: dict) -> dict:
    used = used_today(user['id'])
    quota = int(user.get('daily_quota') or 0)
    return {'used': used, 'quota': quota,
            'left': max(0, quota - used) if quota > 0 else -1,
            'exceeded': quota > 0 and used >= quota}


def usage_by_user() -> list[dict]:
    with center() as c:
        rows = c.execute(
            'SELECT u.id, u.username, u.display_name, u.daily_quota, '
            '  COALESCE(SUM(CASE WHEN g.day = ? THEN g.calls END), 0) AS today_calls, '
            '  COALESCE(SUM(g.calls), 0) AS total_calls '
            'FROM users u LEFT JOIN usage_log g ON g.user_id = u.id '
            'GROUP BY u.id ORDER BY today_calls DESC',
            (today(),)).fetchall()
    return [dict(r) for r in rows]

# ══════════════════ 老数据迁移 ══════════════════

def adopt_legacy_data(admin_id: int) -> dict:
    """
    把单用户时代的 job_assistant.db 交给第一个管理员。

    为什么要这一步：加多用户之前，所有数据都躺在 data/job_assistant.db 里。
    加完之后每个人登录进来看到的是**自己的空库**——老数据等于凭空消失了。
    本地开发升级上来会立刻撞上这个问题，所以在建号时自动认领一次。

    只在目标库还不存在、且老库确实有数据时才动手；用**复制**不用移动，
    原文件留着，万一判断错了还能捞回来。
    """
    import shutil

    def count_rows(path: Path) -> int:
        conn = sqlite3.connect(path)
        try:
            total = 0
            for table in ('job', 'resume', 'material', 'profile_field', 'interview'):
                try:
                    total += conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                except sqlite3.OperationalError:
                    continue    # 老库可能还没有某张表
            return total
        finally:
            conn.close()

    legacy = db.DB_PATH
    target = user_db_path(admin_id)
    if not legacy.is_file():
        return {'adopted': False, 'reason': '没有旧库'}
    # 注意：建号时已经把这个人的空库建出来了，所以不能只看"文件在不在"，
    # 要看**里面有没有数据**——空库就该被老数据覆盖掉。
    if target.exists() and count_rows(target) > 0:
        return {'adopted': False, 'reason': '该账号已经有自己的数据了'}

    rows = count_rows(legacy)
    if rows == 0:
        return {'adopted': False, 'reason': '旧库是空的'}

    shutil.copy2(legacy, target)
    # WAL 文件如果有，一起带过去，否则可能丢最后几笔写入
    for suffix in ('-wal', '-shm'):
        side = Path(str(legacy) + suffix)
        if side.is_file():
            shutil.copy2(side, Path(str(target) + suffix))
    return {'adopted': True, 'rows': rows, 'from': str(legacy), 'to': str(target)}
