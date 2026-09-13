"""
短信验证码：发送 + 校验。

改自 `video_note_product/video-note/sms.py` 的成熟方案，差别是把存储
从 SQLAlchemy 换成了本项目的中心库（sqlite），其余逻辑照搬：
验证码 6 位、5 分钟有效、同号 60 秒才能重发、用过即作废。

## 没配短信时怎么办

`ALIYUN_ACCESS_KEY_ID` / `_SECRET` / `SMS_SIGN_NAME` / `SMS_TEMPLATE_CODE`
四个都填齐才走真实短信。没填齐时**把验证码打进服务端日志**，
这样开发环境（和资质还没批下来的内测期）依然能登录。

注意：日志里的验证码只能从服务器上看到（`docker compose logs app`），
不会通过接口返回——否则任何人都能拿到别人的验证码。
"""

from __future__ import annotations

import logging
import random
import re
import time

from . import auth

logger = logging.getLogger(__name__)

CODE_EXPIRE_SECONDS = 300      # 5 分钟
CODE_LENGTH = 6
SEND_INTERVAL_SECONDS = 60     # 同号发送间隔，防止短信费被刷
PHONE_RE = re.compile(r'^1[3-9]\d{9}$')

# 每个手机号每小时最多发这么多条。60 秒间隔只能挡住"快速连点"，
# 挡不住"每分钟一条刷一小时"——那也是一百多条短信的钱。
HOURLY_LIMIT = 8


def _cfg(key: str) -> str:
    from . import config
    return (config.get(key) or '').strip()


def access_key_id() -> str:
    return _cfg('ALIYUN_ACCESS_KEY_ID')


def access_key_secret() -> str:
    return _cfg('ALIYUN_ACCESS_KEY_SECRET')


def sign_name() -> str:
    return _cfg('SMS_SIGN_NAME')


def template_code() -> str:
    return _cfg('SMS_TEMPLATE_CODE')


def configured() -> bool:
    """四项齐全才算配好；缺任何一项都会退回打日志。"""
    return all([access_key_id(), access_key_secret(), sign_name(), template_code()])


def _gen_code() -> str:
    return f'{random.randint(0, 10 ** CODE_LENGTH - 1):0{CODE_LENGTH}d}'


def normalize(phone: str) -> str:
    return re.sub(r'\D', '', phone or '')


def valid_phone(phone: str) -> bool:
    return bool(PHONE_RE.match(normalize(phone)))


def send_code(phone: str) -> dict:
    """
    发送验证码。返回 {'ok': bool, 'message': str, 'dev_code': str|''}。

    同一手机号只保留最新一条，之前的立刻作废——避免用户点了两次，
    拿着第一条输进去却发现"验证码错误"。
    """
    phone = normalize(phone)
    if not valid_phone(phone):
        return {'ok': False, 'message': '手机号格式不对，应该是 11 位数字', 'dev_code': ''}

    now = time.time()
    with auth.center() as conn:
        conn.execute('DELETE FROM sms_code WHERE expires_at < ?', (now - 3600,))
        latest = conn.execute(
            'SELECT created_at FROM sms_code WHERE phone = ? ORDER BY id DESC LIMIT 1',
            (phone,)).fetchone()
        if latest:
            elapsed = now - float(latest['created_at'])
            if elapsed < SEND_INTERVAL_SECONDS:
                wait = int(SEND_INTERVAL_SECONDS - elapsed) + 1
                return {'ok': False, 'message': f'发送太频繁，请 {wait} 秒后再试', 'dev_code': ''}
        recent = conn.execute(
            'SELECT COUNT(*) AS n FROM sms_code WHERE phone = ? AND created_at > ?',
            (phone, now - 3600)).fetchone()['n']
        if recent >= HOURLY_LIMIT:
            return {'ok': False,
                    'message': '这个号码一小时内发得太多了，过一会儿再试', 'dev_code': ''}

        code = _gen_code()
        conn.execute('DELETE FROM sms_code WHERE phone = ?', (phone,))
        conn.execute(
            'INSERT INTO sms_code (phone, code, expires_at, used, created_at) '
            'VALUES (?,?,?,0,?)', (phone, code, now + CODE_EXPIRE_SECONDS, now))

    if not configured():
        # 开发/内测兜底：打进日志，不通过接口返回
        logger.warning('【验证码】%s → %s（%d 分钟内有效）',
                       phone, code, CODE_EXPIRE_SECONDS // 60)
        print(f'[验证码] {phone} → {code}（{CODE_EXPIRE_SECONDS // 60} 分钟内有效）', flush=True)
        return {'ok': True, 'dev_code': code,
                'message': f'短信服务还没配置，验证码已打印到服务器日志（{CODE_EXPIRE_SECONDS // 60} 分钟内有效）'}

    ok, msg = _send_via_aliyun(phone, template_code(), f'{{"code":"{code}"}}')
    if not ok:
        logger.error('短信发送失败 %s: %s', phone, msg)
        return {'ok': False, 'message': f'短信发送失败：{msg}', 'dev_code': ''}
    return {'ok': True, 'message': '验证码已发送，请查收短信', 'dev_code': ''}


def verify_code(phone: str, code: str) -> bool:
    """校验验证码。通过则标记已用（一次性）。"""
    phone = normalize(phone)
    code = (code or '').strip()
    if not valid_phone(phone) or len(code) != CODE_LENGTH:
        return False
    now = time.time()
    with auth.center() as conn:
        row = conn.execute(
            'SELECT * FROM sms_code WHERE phone = ? AND code = ? AND used = 0 '
            'ORDER BY id DESC LIMIT 1', (phone, code)).fetchone()
        if row is None or float(row['expires_at']) < now:
            return False
        conn.execute('UPDATE sms_code SET used = 1 WHERE id = ?', (row['id'],))
    return True


def _send_via_aliyun(phone: str, tpl: str, param: str) -> tuple[bool, str]:
    """调阿里云短信 SDK。返回 (ok, 错误信息)。"""
    try:
        from alibabacloud_dysmsapi20170525 import models as dysms_models
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_tea_openapi import models as open_models
    except ImportError:
        return False, ('未安装阿里云短信 SDK（pip install '
                       'alibabacloud_dysmsapi20170525）')

    try:
        cfg = open_models.Config(
            access_key_id=access_key_id(),
            access_key_secret=access_key_secret(),
            endpoint='dysmsapi.aliyuncs.com',
        )
        resp = Client(cfg).send_sms(dysms_models.SendSmsRequest(
            phone_numbers=phone, sign_name=sign_name(),
            template_code=tpl, template_param=param))
        if resp.body.code == 'OK':
            return True, ''
        return False, resp.body.message or '未知错误'
    except Exception as exc:                       # noqa: BLE001
        return False, str(exc)[:200]


def status() -> dict:
    """给设置页看：短信是走真实通道还是打日志。"""
    return {
        'configured': configured(),
        'sign_name': sign_name() if configured() else '',
        'template': template_code() if configured() else '',
    }
