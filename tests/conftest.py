"""
共享测试夹具。

设计原则：
1. **每个测试用独立的临时数据库**，互不污染，也不碰用户的真实数据。
2. **AI 调用全部 mock**（clients 层），让单元测试快且确定。
   真实 AI 链路由 `tests/e2e_pipeline.py` 单独覆盖。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth as auth_mod         # noqa: E402
from app import clients as clients_mod  # noqa: E402
from app import config as config_mod     # noqa: E402
from app import db as db_mod             # noqa: E402

TEST_PHONE = '13800000000'


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """
    把数据库、上传目录、账号库全部指向临时路径。

    ⚠️ `auth.CENTER_DB` 必须一起改——它默认指向 data/app.db（真实账号库），
    测试往里建用户就污染生产数据了。
    """
    upload = tmp_path / 'uploads'
    upload.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_mod, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(config_mod, 'UPLOAD_DIR', upload)
    monkeypatch.setattr(config_mod, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(auth_mod, 'CENTER_DB', tmp_path / 'app.db')
    # 测试绝不真发短信：清掉短信配置（走日志兜底），
    # 并打开 SMS_DEV_MODE 好让验证码能从响应里拿到
    for k in ('ALIYUN_ACCESS_KEY_ID', 'ALIYUN_ACCESS_KEY_SECRET',
              'SMS_SIGN_NAME', 'SMS_TEMPLATE_CODE'):
        monkeypatch.setitem(config_mod.CONFIG, k, '')
    monkeypatch.setitem(config_mod.CONFIG, 'SMS_DEV_MODE', '1')
    db_mod.init_db()
    return db_mod


@pytest.fixture()
def user(temp_db):
    """
    建一个测试账号，并把"当前用户"设成他。

    设了之后 `db.connect()` 就连到 data/users/uN.db——也就是
    多用户模式下真实发生的事，测试跑的就是生产路径。
    """
    u = auth_mod.create_user(TEST_PHONE, '测试用户')
    db_mod.set_current_user(u['id'])
    yield u
    db_mod.set_current_user(None)


@pytest.fixture()
def conn(user):
    """
    直接给一个**自动提交**的连接。

    为什么不用 db.session()：那个上下文管理器只在退出时提交，
    测试里刚插入的数据 API（另一个连接）看不到，会得到假失败。
    """
    c = db_mod.connect()
    c.isolation_level = None      # autocommit
    yield c
    c.close()


@pytest.fixture()
def seeded(conn, temp_db):
    """一份最小可用数据：已确认的事实 + 岗位 + 简历。"""
    now = temp_db.now_iso()
    fields = [
        ('基本信息', '手机', '13800000000'),
        ('基本信息', '现公司', '某某科技（杭州）有限公司'),
        ('工作成果', '市价委托日活', '日活约 1400 人'),
        ('工作成果', '查询模块复用率', '查询模块复用率达 90%'),
    ]
    for i, (sec, key, val) in enumerate(fields):
        conn.execute(
            'INSERT INTO profile_field (section, key, value, source, as_of, status, '
            'note, sort_order, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (sec, key, val, 'test', '', 'confirmed', '', i, now))

    cur = conn.execute(
        'INSERT INTO project (name, role, summary, status, sort_order, updated_at) '
        'VALUES (?,?,?,?,?,?)',
        ('Video Note', '独立开发', 'B站视频转笔记付费产品', 'confirmed', 0, now))
    pid = int(cur.lastrowid)
    for i, (text, metric) in enumerate([
        ('接入支付宝网站支付，实现充值和退款功能', ''),
        ('上线后产生 5 个真实付费用户', '5 个'),
        ('自研代码 5459 行', '5459 行'),
    ]):
        conn.execute(
            'INSERT INTO project_point (project_id, kind, text, metric, status, '
            'sort_order, updated_at) VALUES (?,?,?,?,?,?,?)',
            (pid, 'point', text, metric, 'confirmed', i, now))

    job = conn.execute(
        'INSERT INTO job (company, title, city, salary, raw_text, status, created_at, '
        'updated_at) VALUES (?,?,?,?,?,?,?,?)',
        ('蚂蚁集团', 'AI 工程师（金融智能）', '杭州', '30-60K',
         '1. 熟悉支付、计费、订阅、结算、风控\n2. 有 Agent 开发经验\n3. 本科及以上',
         'to_apply', now, now))
    job_id = int(job.lastrowid)

    resume = conn.execute(
        'INSERT INTO resume (name, job_id, content, status, created_at, updated_at) '
        'VALUES (?,?,?,?,?,?)',
        ('测试简历', job_id,
         '# 张三\n\n**求职意向**：AI 工程师\n\n## 项目经历\n'
         '- 接入支付宝网站支付，实现充值和退款功能\n- 优化了性能\n',
         'draft', now, now))
    return {
        'job_id': job_id,
        'resume_id': int(resume.lastrowid),
        'project_id': pid,
    }


@pytest.fixture()
def fake_ai(monkeypatch):
    """
    把 AI 调用全部换成假的。

    返回一个可配置对象：测试里改 `json_response` / `text_response` /
    `raise_error` 就能构造各种场景。
    """
    class FakeAI:
        def __init__(self):
            self.calls = []
            self.json_response = {}
            self.text_response = ''
            self.raise_error = None

        def _maybe_raise(self):
            if self.raise_error:
                raise self.raise_error

        def chat(self, messages, **kw):
            self.calls.append(('chat', messages, kw))
            self._maybe_raise()
            return self.text_response

        def chat_json(self, messages, **kw):
            self.calls.append(('json', messages, kw))
            self._maybe_raise()
            return self.json_response

        def read_image(self, path, prompt, **kw):
            self.calls.append(('image', str(path)))
            self._maybe_raise()
            return self.text_response

        def read_pdf(self, path, prompt):
            self.calls.append(('pdf', str(path)))
            self._maybe_raise()
            return self.text_response, 'text-layer'

        def transcribe(self, data, **kw):
            self.calls.append(('asr', len(data)))
            self._maybe_raise()
            return self.text_response or '这是识别出来的话'

        def synthesize(self, text, voice='', **kw):
            self.calls.append(('tts', text))
            self._maybe_raise()
            return b'RIFF0000WAVE'

    fake = FakeAI()

    # 留住真实实现：个别测试（如截断抢救）需要绕过假实现走真实解析逻辑
    fake.real = {n: getattr(clients_mod, n) for n in (
        'chat', 'chat_json', 'read_image', 'read_pdf', 'transcribe', 'synthesize')}

    # 直接替换模块属性（业务模块都是 `from . import clients` 后按属性访问）
    for name in ('chat', 'chat_json', 'read_image', 'read_pdf',
                 'transcribe', 'synthesize'):
        monkeypatch.setattr(clients_mod, name, getattr(fake, name))
    return fake

@pytest.fixture(autouse=True)
def _reset_current_user():
    """
    每个测试前后都把「当前用户」清空。

    这个值是 ContextVar，测试之间会残留：上一个测试最后调了一次接口，
    下一个测试如果没走登录流程，`db.connect()` 就会连到上一个人的库，
    报出莫名其妙的 "no such table"。
    """
    db_mod.set_current_user(None)
    yield
    db_mod.set_current_user(None)


def phone_login(client, phone: str = TEST_PHONE):
    """
    走一次完整的「发验证码 + 登录」，让 client 带上会话 cookie。

    测试里短信走日志兜底，验证码会通过 dev_code 返回来
    （只有 SMS_DEV_MODE=1 时才会，生产环境不会）。
    """
    r = client.post('/api/auth/send-code', json={'phone': phone})
    assert r.status_code == 200, f'发验证码失败：{r.text}'
    code = r.json()['dev_code']
    assert code, '测试里应该能拿到 dev_code（检查 SMS_DEV_MODE）'
    r = client.post('/api/auth/login', json={'phone': phone, 'code': code})
    assert r.status_code == 200, f'登录失败：{r.text}'
    return r.json()
