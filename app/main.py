"""
求职助手 · 应用主体（FastAPI）

启动：
    ./run.sh                       # http://127.0.0.1:7870
    ./run.sh --https               # https://127.0.0.1:8443（手机可用麦克风）

模块划分见 ../需求文档.md。所有 AI 生成的内容都是草稿，需用户确认。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (auth, clients, config, db, interview, materials, packaging,
               questions, resume_chat, sms)
from .questions import ROUND_LABEL

WEB_DIR = Path(__file__).resolve().parent.parent / 'web'

logger = logging.getLogger(__name__)

app = FastAPI(title='求职助手', version='1.0.0',
              description='求职工作台 · 档案 / 材料 / 岗位 / 简历 / 面试')


@app.on_event('startup')
def _startup() -> None:
    db.init_db()          # 单用户模式的旧库（本地开发时还在用）
    with auth.center():   # 中心库：账号 + 验证码 + 用量
        pass

    # 从「用户名+密码」迁到「手机号」。必须在建号之前跑，
    # 否则会往老结构里插数据然后报错。
    admin_phone = config.get('ADMIN_PHONE').strip()
    out = auth.migrate_to_phone_login(admin_phone)
    if out.get('migrated'):
        logger.warning('账号体系已迁移到手机号：%s 个账号', out['users'])
        if out.get('admin_bound'):
            logger.warning('管理员已绑定 %s', out['admin_phone'])
        else:
            logger.error('⚠️ 没有可用的 ADMIN_PHONE，管理员无法登录！'
                         '请在 .env 里设置 ADMIN_PHONE 后重启')
        # ★ 有数据但没手机号的账号会被锁在门外，必须喊出来。
        # 实测撞到过：用户注册的普通账号里有真实简历和面试，手机号却绑给了空的管理员账号。
        for o in out.get('orphans') or []:
            logger.error('⚠️ 账号 #%s「%s」没有手机号，登录不了。'
                         '如果那里面有数据，用 bind_phone() 把手机号挪过去',
                         o['id'], o['name'])

    # 每次启动都检查一遍（不只是迁移那一次）
    for o in auth.accounts_without_phone():
        if o['phone'].startswith('imported-') or o['phone'].startswith('retired-'):
            logger.warning('账号 #%s「%s」当前无法登录（手机号=%s）',
                           o['id'], o['display_name'], o['phone'])

    if not auth.list_users():
        if not (admin_phone and sms.valid_phone(admin_phone)):
            logger.error('⚠️ 账号库是空的，但 ADMIN_PHONE 没设或格式不对，'
                         '没人能登录。请在 .env 里设置 ADMIN_PHONE 后重启')
        else:
            admin = auth.create_user(admin_phone, '管理员', is_admin=True)
            logger.warning('已创建管理员账号 %s', admin_phone)
            adopted = auth.adopt_legacy_data(admin['id'])
            if adopted.get('adopted'):
                logger.warning('把原有的单用户数据交给了管理员（%s 条记录）',
                               adopted['rows'])
    else:
        # 已有账号但没有一个有手机号 → 谁都进不来，必须在日志里喊出来
        with auth.center() as conn:
            ok = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE phone LIKE '1%' AND LENGTH(phone)=11"
            ).fetchone()['n']
        if not ok:
            logger.error('⚠️ 所有账号都没有可用手机号，没人能登录！'
                         '请设置 ADMIN_PHONE 后重启')

    if not sms.configured():
        logger.warning('短信服务未配置（缺 ALIYUN_ACCESS_KEY_ID / _SECRET / '
                       'SMS_SIGN_NAME / SMS_TEMPLATE_CODE）——验证码会打印到本日志，'
                       '配置齐全后自动切换到真实短信')


# ══════════════════════════ 登录与鉴权 ══════════════════════════
#
# 放公网就必须有门。没有门的话，任何人打开网址就能看到简历、手机号，
# 还能拿你的 API key 烧额度。
#
# 鉴权靠一张签名 cookie；当前用户 id 通过 ContextVar 传给 db.session()，
# 于是 57 处业务查询一行都不用改，就实现了"各看各的库"。

PUBLIC_PATHS = ('/login', '/api/auth/', '/static/', '/health', '/favicon.ico')


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PATHS)


@app.middleware('http')
async def auth_middleware(request, call_next):
    path = request.url.path
    uid = auth.read_session(request.cookies.get(auth.SESSION_COOKIE, ''))
    db.set_current_user(uid)

    if _is_public(path):
        return await call_next(request)

    if uid is None:
        # 接口返回 401，页面跳登录页
        if path.startswith('/api/') or path.startswith('/__'):
            return Response(
                json.dumps({'detail': '请先登录'}, ensure_ascii=False),
                status_code=401, media_type='application/json')
        return RedirectResponse('/login', status_code=302)

    user = auth.get_user(uid)
    if user is None or not user['is_active']:
        db.set_current_user(None)
        if path.startswith('/api/'):
            return Response(json.dumps({'detail': '账号已停用'}, ensure_ascii=False),
                            status_code=401, media_type='application/json')
        return RedirectResponse('/login', status_code=302)

    request.state.user = user
    return await call_next(request)


class PhoneIn(BaseModel):
    phone: str


class LoginIn(BaseModel):
    phone: str
    code: str


@app.get('/login')
def login_page() -> FileResponse:
    return FileResponse(WEB_DIR / 'login.html', headers=NO_CACHE)


@app.post('/api/auth/send-code')
def send_code(body: PhoneIn) -> dict:
    """
    发验证码。同号 60 秒内只能发一次，一小时最多 8 条。

    短信没配置时会把验证码打印到**服务端日志**（不通过接口返回——
    否则任何人都能拿到别人的验证码），配置齐全后自动走真实短信。
    """
    out = sms.send_code(body.phone)
    if not out['ok']:
        raise HTTPException(400, out['message'])
    # 只有本机开发（SMS_DEV_MODE=1）才把验证码回给前端，方便调试
    dev = config.get('SMS_DEV_MODE') == '1'
    return {'ok': True, 'message': out['message'],
            'dev_code': out['dev_code'] if dev else '',
            'sms_configured': sms.configured()}


@app.post('/api/auth/login')
def login(body: LoginIn) -> dict:
    """手机号 + 验证码登录。没注册过就自动建号（受注册开关控制）。"""
    if not sms.valid_phone(body.phone):
        raise HTTPException(400, '手机号格式不对，应该是 11 位数字')
    if not sms.verify_code(body.phone, body.code):
        raise HTTPException(401, '验证码错误或已过期')
    try:
        user = auth.login_or_register(body.phone,
                                      registration_open=auth.registration_open())
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    resp = JSONResponse({'ok': True, 'user': _public_user(user)})
    _set_session_cookie(resp, user['id'])
    return resp


@app.post('/api/auth/logout')
def logout() -> dict:
    resp = JSONResponse({'ok': True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@app.get('/api/auth/me')
def me(request: Request) -> dict:
    uid = auth.read_session(request.cookies.get(auth.SESSION_COOKIE, ''))
    if uid is None:
        raise HTTPException(401, '未登录')
    user = auth.get_user(uid)
    if user is None:
        raise HTTPException(401, '未登录')
    return {'user': _public_user(user), 'usage': auth.quota_state(user),
            'registration_open': auth.registration_open(),
            'sms': sms.status()}


def _public_user(user: dict) -> dict:
    """只把能对外的字段给前端——密码哈希之类绝对不出现在响应里。"""
    phone = user.get('phone') or ''
    return {'id': user['id'], 'phone': phone,
            'phone_masked': f'{phone[:3]}****{phone[-4:]}' if len(phone) == 11 else phone,
            'display_name': user.get('display_name') or phone,
            'is_admin': bool(user.get('is_admin'))}


def _set_session_cookie(resp: Response, user_id: int) -> None:
    resp.set_cookie(
        auth.SESSION_COOKIE, auth.make_session(user_id),
        max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite='lax',
        secure=config.get('COOKIE_SECURE', '') == '1')


# ══════════════════════════ 基础 / 设置 ══════════════════════════

@app.get('/api/version')
def version() -> dict:
    """前端资源版本号：用来确认浏览器加载的不是缓存里的旧代码。"""
    files = list((WEB_DIR / 'js').rglob('*.js')) + list((WEB_DIR / 'css').rglob('*.css'))
    return {'build': _build_stamp(), 'files': len(files)}


@app.get('/health')
def health() -> dict:
    return {'ok': True}


@app.get('/api/overview')
def overview() -> dict:
    """工作台首页：全局状态与待办。"""
    with db.session() as conn:
        s = db.stats(conn)
        # ★ action 必须是**真实存在的路由**。之前写的是 '#/profile' / '#/practice'
        # / '#/records'——路由表里没有这些，render() 会静默回退到工作台，
        # 用户看到的现象就是"点了没反应"。这里的路径要和前端 PAGES 对齐。
        todos = []
        # ★ 「档案待确认」这条已经作废：档案模块取消了，而且 action 指向
        # #/mine/profile 现在会跳到材料库——点进去驴唇不对马嘴。
        # 现在要确认的单位是**材料**：它的解析你核对过没有。
        if s.get('materials_unreviewed'):
            todos.append({'type': 'material', 'label': '有材料的解析还没核对', 'short': '材料待核对',
                          'count': s['materials_unreviewed'],
                          'action': '#/mine/materials'})
        if s['suggestions_pending']:
            # 直接跳到**某一份有建议的简历的包装页**，而不是简历列表——
            # 列表是一级页面、没有返回按钮，跳过去用户会觉得"进得去出不来"。
            target = conn.execute(
                'SELECT s.resume_id, s.job_id FROM resume_suggestion s '
                "WHERE s.decision = 'pending' "
                'GROUP BY s.resume_id, s.job_id ORDER BY COUNT(*) DESC LIMIT 1').fetchone()
            action = '#/resume'
            if target:
                action = f'#/resume/pack?resume_id={target["resume_id"]}'
                if target['job_id']:
                    action += f'&job_id={target["job_id"]}'
            todos.append({'type': 'suggestion', 'label': '简历有未处理的修改建议', 'short': '简历建议待处理',
                          'count': s['suggestions_pending'], 'action': action})
        if s['questions_review']:
            todos.append({'type': 'review', 'label': '有标记待复习的题目', 'short': '题目待复习',
                          'count': s['questions_review'],
                          'action': '#/interview/questions?status=review'})
        if s['interviews_unreviewed']:
            todos.append({'type': 'interview', 'label': '有面试还没复盘', 'short': '面试待复盘',
                          'count': s['interviews_unreviewed'],
                          'action': '#/interview/records'})
    s['todos'] = todos
    s['pending_total'] = sum(t['count'] for t in todos)
    return s


@app.get('/api/settings')
def settings() -> dict:
    """设置页：密钥是否配置（不回显内容）+ 模型 + 缺失项。"""
    return {
        'keys': config.key_status(),
        'models': clients.health()['models'],
        'missing': config.missing_keys(),
        'env_files': [str(p) for p in config.ENV_CANDIDATES],
    }


# ══════════════════════════ 档案 ══════════════════════════

class FieldPatch(BaseModel):
    value: str | None = None
    note: str | None = None
    status: str | None = None


@app.get('/api/profile')
def list_profile() -> dict:
    with db.session() as conn:
        rows = conn.execute(
            'SELECT * FROM profile_field ORDER BY section, sort_order, id').fetchall()
    sections: dict[str, list[dict]] = {}
    for r in rows:
        sections.setdefault(r['section'], []).append(dict(r))
    return {'sections': [{'name': k, 'fields': v} for k, v in sections.items()],
            'total': len(rows),
            'unconfirmed': sum(1 for r in rows if r['status'] == db.STATUS_UNCONFIRMED)}


@app.patch('/api/profile/{field_id}')
def patch_field(field_id: int, patch: FieldPatch) -> dict:
    with db.session() as conn:
        row = conn.execute('SELECT * FROM profile_field WHERE id = ?', (field_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '字段不存在')
        if patch.status is not None and patch.status not in db.VALID_STATUS:
            raise HTTPException(400, f'status 必须是 {db.VALID_STATUS} 之一')
        value = patch.value if patch.value is not None else row['value']
        note = patch.note if patch.note is not None else row['note']
        if patch.status is not None:
            status = patch.status
        elif value != row['value']:
            status = db.STATUS_CORRECTED
        else:
            status = row['status']
        conn.execute('UPDATE profile_field SET value=?, note=?, status=?, updated_at=? '
                     'WHERE id=?', (value, note, status, db.now_iso(), field_id))
        db.log_change(conn, 'profile_field', field_id,
                      'correct' if value != row['value'] else 'status',
                      row['value'], value)
        return dict(conn.execute('SELECT * FROM profile_field WHERE id = ?',
                                 (field_id,)).fetchone())


@app.delete('/api/profile/{field_id}')
def delete_field(field_id: int) -> dict:
    """删除一条档案字段（确认是错的、或者重复的）。"""
    with db.session() as conn:
        row = conn.execute('SELECT * FROM profile_field WHERE id = ?', (field_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '字段不存在')
        conn.execute('DELETE FROM profile_field WHERE id = ?', (field_id,))
        db.log_change(conn, 'profile_field', field_id, 'delete',
                      before=f'{row["key"]}={row["value"]}')
    return {'ok': True}


@app.delete('/api/points/{point_id}')
def delete_point(point_id: int) -> dict:
    """删除一条项目要点。"""
    with db.session() as conn:
        row = conn.execute('SELECT * FROM project_point WHERE id = ?', (point_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '要点不存在')
        conn.execute('DELETE FROM project_point WHERE id = ?', (point_id,))
        db.log_change(conn, 'project_point', point_id, 'delete', before=row['text'][:80])
    return {'ok': True}


@app.post('/api/materials/{material_id}/confirm-facts')
def confirm_material_facts(material_id: int) -> dict:
    """
    一次确认这份材料抽出来的**全部**事实。

    为什么要有这个：以前是把事实提升成"档案字段"，再让用户去「我的档案」里
    一条条点确认——一份简历能拆出 121 条，没人会去点。
    确认的单位应该是**材料**："这份材料我传的、解析得对"，一句话就够。
    """
    with db.session() as conn:
        row = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '材料不存在')
        cur = conn.execute(
            "UPDATE material_fact SET status='confirmed' WHERE material_id = ?",
            (material_id,))
        n = cur.rowcount
        # 材料本身也标成已确认，材料库一眼能看出哪些还没核对过
        conn.execute("UPDATE material SET doc_reviewed = 1, updated_at = ? WHERE id = ?",
                     (db.now_iso(), material_id))
        db.log_change(conn, 'material', material_id, 'confirm_facts',
                      after=f'{n} 条事实')
    return {'ok': True, 'confirmed': n}


@app.post('/api/profile/{field_id}/confirm')
def confirm_field(field_id: int) -> dict:
    return patch_field(field_id, FieldPatch(status=db.STATUS_CONFIRMED))


@app.post('/api/profile/{field_id}/obsolete')
def obsolete_field(field_id: int) -> dict:
    return patch_field(field_id, FieldPatch(status=db.STATUS_OBSOLETE))


def _confirm_many(table: str, ids: list[int], status: str) -> int:
    if table not in ('profile_field', 'project_point', 'project', 'material_fact'):
        raise HTTPException(400, '不支持的表')
    if status not in db.VALID_STATUS:
        raise HTTPException(400, 'status 不合法')
    if not ids:
        return 0
    marks = ','.join('?' * len(ids))
    with db.session() as conn:
        conn.execute(f'UPDATE {table} SET status=? WHERE id IN ({marks})',
                     [status, *ids])
    return len(ids)


class BatchStatus(BaseModel):
    ids: list[int]
    status: str = db.STATUS_CONFIRMED
    table: str = 'profile_field'


@app.post('/api/batch/status')
def batch_status(body: BatchStatus) -> dict:
    return {'updated': _confirm_many(body.table, body.ids, body.status)}


class ProjectIn(BaseModel):
    name: str
    role: str = ''
    period: str = ''
    summary: str = ''


@app.post('/api/projects')
def create_project(body: ProjectIn) -> dict:
    """手动新建项目（材料解析不出来、或者想自己先起个名字时用）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, '项目名不能为空')
    now = db.now_iso()
    with db.session() as conn:
        exists = conn.execute('SELECT id FROM project WHERE name = ?', (name,)).fetchone()
        if exists:
            raise HTTPException(400, f'已经有一个叫「{name}」的项目了')
        cur = conn.execute(
            'INSERT INTO project (name, role, period, summary, source, status, sort_order, '
            'updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (name, body.role.strip(), body.period.strip(), body.summary.strip(),
             '手动录入', db.STATUS_UNCONFIRMED, 99, now))
        pid = int(cur.lastrowid)
        db.log_change(conn, 'project', pid, 'create', after=name)
    return {'id': pid, 'name': name}


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: int) -> dict:
    """
    删除项目。

    项目要点跟着删（本来就从属于项目）；**材料不删**——材料是原始证据，
    删了就没法追溯了，只是把它和项目的关联断开（project_id 置空）。
    """
    with db.session() as conn:
        row = conn.execute('SELECT * FROM project WHERE id = ?', (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '项目不存在')
        conn.execute('UPDATE material SET project_id = NULL WHERE project_id = ?', (project_id,))
        conn.execute('DELETE FROM project_point WHERE project_id = ?', (project_id,))
        conn.execute('DELETE FROM project WHERE id = ?', (project_id,))
        db.log_change(conn, 'project', project_id, 'delete', before=row['name'])
    return {'ok': True}


@app.get('/api/projects')
def list_projects() -> dict:
    with db.session() as conn:
        projects = [dict(r) for r in conn.execute(
            'SELECT * FROM project ORDER BY sort_order, id').fetchall()]
        points = [dict(r) for r in conn.execute(
            'SELECT * FROM project_point ORDER BY project_id, sort_order, id').fetchall()]
    by_project: dict[int, list[dict]] = {}
    for p in points:
        by_project.setdefault(p['project_id'], []).append(p)
    for p in projects:
        p['points'] = by_project.get(p['id'], [])
    return {'projects': projects, 'total': len(projects)}


class PointPatch(BaseModel):
    text: str | None = None
    status: str | None = None


@app.patch('/api/points/{point_id}')
def patch_point(point_id: int, patch: PointPatch) -> dict:
    with db.session() as conn:
        row = conn.execute('SELECT * FROM project_point WHERE id = ?', (point_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '要点不存在')
        text = patch.text if patch.text is not None else row['text']
        if patch.status is not None:
            if patch.status not in db.VALID_STATUS:
                raise HTTPException(400, 'status 不合法')
            status = patch.status
        elif text != row['text']:
            status = db.STATUS_CORRECTED
        else:
            status = row['status']
        conn.execute('UPDATE project_point SET text=?, status=?, updated_at=? WHERE id=?',
                     (text, status, db.now_iso(), point_id))
        return dict(conn.execute('SELECT * FROM project_point WHERE id = ?',
                                 (point_id,)).fetchone())


@app.get('/api/changelog')
def changelog(limit: int = 50) -> dict:
    with db.session() as conn:
        rows = conn.execute('SELECT * FROM change_log ORDER BY id DESC LIMIT ?',
                            (limit,)).fetchall()
    return {'items': db.rows_to_dicts(rows)}


# ══════════════════════════ 材料库 ══════════════════════════

@app.get('/api/materials')
def api_materials() -> dict:
    with db.session() as conn:
        return {'materials': materials.list_materials(conn)}


@app.post('/api/materials')
async def upload_material(file: UploadFile = File(...),
                          project_id: int | None = Query(default=None),
                          doc_kind: str = Query(default='')) -> dict:
    """
    上传材料。

    `doc_kind` 指定它属于哪一类（jd / resume / project）——用户在材料库哪个
    页签上传的，就归哪一类。不用等模型解析完再猜，分类立刻生效。
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, '文件是空的')
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, '文件超过 20MB')
    kind = doc_kind if doc_kind in ('jd', 'resume', 'project') else ''
    try:
        with db.session() as conn:
            mid = materials.create_material(conn, file.filename or 'unnamed', content,
                                            project_id)
            if kind:
                conn.execute('UPDATE material SET doc_kind = ? WHERE id = ?',
                             ('repo' if kind == 'project' and
                              (file.filename or '').lower().endswith('.zip') else kind, mid))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'id': mid, 'filename': file.filename}


class MaterialFromUrl(BaseModel):
    url: str
    project_id: int | None = None


@app.post('/api/materials/from-url')
def material_from_url(body: MaterialFromUrl) -> dict:
    """
    用 GitHub 仓库地址建一条项目材料。

    拉下来的仓库压缩包和上传 zip 是同一条解析链路——面试官深挖项目时
    要的是"代码里真实存在的东西"，而不是自己复述的简介。
    """
    try:
        data, filename, real_url = materials.fetch_github_zip(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with db.session() as conn:
        mid = materials.create_material(conn, filename, data,
                                        project_id=body.project_id,
                                        source_url=real_url, kind='repo')
    return {'id': mid, 'filename': filename, 'bytes': len(data), 'source_url': real_url}


@app.post('/api/projects/{project_id}/summary')
def project_summary(project_id: int) -> dict:
    """生成/刷新项目总结文档（供预览，也是面试深挖的底稿）。"""
    try:
        with db.session() as conn:
            return materials.generate_project_summary(conn, project_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except clients.ServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete('/api/materials/{material_id}')
def delete_material(material_id: int) -> dict:
    """删除材料：抽出来的事实一起删（它们只对这份材料有意义）。"""
    with db.session() as conn:
        row = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '材料不存在')
        facts = conn.execute('SELECT COUNT(*) AS n FROM material_fact WHERE material_id = ?',
                             (material_id,)).fetchone()['n']
        conn.execute('DELETE FROM material_fact WHERE material_id = ?', (material_id,))
        conn.execute('DELETE FROM material WHERE id = ?', (material_id,))
        db.log_change(conn, 'material', material_id, 'delete', before=row['filename'])
    try:
        Path(row['stored_path']).unlink(missing_ok=True)
    except OSError:
        pass
    return {'ok': True, 'facts_removed': facts}


@app.post('/api/materials/{material_id}/parse-jd')
def parse_jd_material(material_id: int) -> dict:
    """
    按「岗位要求」解析一份材料：拿完整原文 + 公司/岗位/城市/薪资。

    和通用解析的区别：通用解析给的是"一句话摘要 + 若干事实"，
    而岗位分析要的是**JD 原文**——只给摘要的话，匹配度会少判一大半要求。
    """
    try:
        with db.session() as conn:
            return materials.parse_job_material(conn, material_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/materials/{material_id}/parse')
def parse_material(material_id: int) -> dict:
    """解析材料（耗时，前端需显示进度）。"""
    with db.session() as conn:
        result = materials.parse_material(conn, material_id)
    if not result.get('ok'):
        raise HTTPException(400, result.get('error', '解析失败'))
    return result


@app.get('/api/materials/{material_id}')
def material_detail(material_id: int) -> dict:
    with db.session() as conn:
        data = materials.material_detail(conn, material_id)
    if data is None:
        raise HTTPException(404, '材料不存在')
    return data


class PromoteFacts(BaseModel):
    fact_ids: list[int]


@app.post('/api/materials/{material_id}/promote')
def promote_facts(material_id: int, body: PromoteFacts) -> dict:
    with db.session() as conn:
        added = materials.promote_facts_to_profile(conn, material_id, body.fact_ids)
    return {'added': added}


@app.get('/api/materials/{material_id}/file')
def material_file(material_id: int):
    with db.session() as conn:
        row = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '材料不存在')
    path = Path(row['stored_path'])
    if not path.is_file():
        raise HTTPException(404, '文件已丢失')
    return FileResponse(path, media_type=row['media_type'], filename=row['filename'])


# ══════════════════════════ 岗位 ══════════════════════════

class JobIn(BaseModel):
    company: str = ''
    title: str = ''
    salary: str = ''
    city: str = ''
    raw_text: str = ''
    material_id: int | None = None


@app.get('/api/jobs')
def api_jobs() -> dict:
    with db.session() as conn:
        rows = conn.execute(
            'SELECT j.*, (SELECT COUNT(*) FROM question q WHERE q.job_id=j.id) AS q_count, '
            '(SELECT COUNT(*) FROM job_requirement r WHERE r.job_id=j.id) AS req_count '
            'FROM job j ORDER BY j.id DESC').fetchall()
    return {'jobs': db.rows_to_dicts(rows)}


@app.post('/api/jobs')
def create_job(body: JobIn) -> dict:
    if not body.raw_text.strip() and not body.material_id:
        raise HTTPException(400, '请粘贴岗位要求原文，或上传 JD 截图')

    company, title = body.company.strip(), body.title.strip()
    city, salary = body.city.strip(), body.salary.strip()
    raw_text = body.raw_text.strip()
    parsed = None

    if body.material_id:
        with db.session() as conn:
            row = conn.execute('SELECT * FROM material WHERE id = ?',
                               (body.material_id,)).fetchone()
            if row is None:
                raise HTTPException(400, '上传的材料不存在')
            # ★ 上传了 JD 截图就一定要把它解析成原文。
            # 之前只是把 material_id 存下来、**从来不解析**，于是 job.raw_text 是空的，
            # 做匹配度分析时只能拿一句摘要充数（甚至直接报"没有 JD 原文"）。
            # 只在"表单没给原文、材料也没解析过"时才解析（避免白白多花 13 秒）
            if not raw_text and not (row['raw_text'] or '').strip():
                try:
                    parsed = materials.parse_job_material(conn, body.material_id)
                except ValueError as exc:
                    raise HTTPException(400, f'JD 解析失败：{exc}') from exc
                if parsed.get('ok'):
                    raw_text = raw_text or parsed.get('raw_text', '')
                    company = company or parsed.get('company', '')
                    title = title or parsed.get('title', '')
                    city = city or parsed.get('city', '')
                    salary = salary or parsed.get('salary', '')
            if not raw_text:
                raw_text = (row['raw_text'] or row['summary'] or '').strip()

    now = db.now_iso()
    with db.session() as conn:
        cur = conn.execute(
            'INSERT INTO job (company, title, salary, city, raw_text, material_id, '
            'status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (company, title, salary, city, raw_text, body.material_id,
             db.JOB_TO_APPLY, now, now))
        job_id = int(cur.lastrowid)
    return {'id': job_id, 'parsed': bool(parsed), 'raw_text_chars': len(raw_text)}


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: int) -> dict:
    """
    删除岗位。

    连带处理：岗位要求、匹配度分析、投递记录、**题库**都跟着删——它们只对这个岗位有意义。
    **简历保留**（独立资产），只解除关联。
    """
    with db.session() as conn:
        row = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '岗位不存在')
        removed_q = conn.execute('SELECT COUNT(*) AS n FROM question WHERE job_id = ?',
                                 (job_id,)).fetchone()['n']
        kept_r = conn.execute('SELECT COUNT(*) AS n FROM resume WHERE job_id = ?',
                              (job_id,)).fetchone()['n']
        conn.execute('DELETE FROM job_requirement WHERE job_id = ?', (job_id,))
        conn.execute('DELETE FROM application WHERE job_id = ?', (job_id,))
        # ★ 题库跟着岗位一起删。之前是"置空保留"，结果那些题既不属于任何岗位、
        # 也永远不会被取到（取题按 job_id 过滤），只会在库里发霉。
        # 简历不同：简历是独立资产，只解除关联。
        conn.execute('DELETE FROM question WHERE job_id = ?', (job_id,))
        conn.execute('UPDATE resume SET job_id = NULL WHERE job_id = ?', (job_id,))
        conn.execute('DELETE FROM job WHERE id = ?', (job_id,))
        db.log_change(conn, 'job', job_id, 'delete',
                      before=f'{row["company"]} {row["title"]}')
    return {'ok': True, 'removed_questions': removed_q, 'kept_resumes': kept_r}


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '岗位不存在')
        out = dict(row)
        for f in ('layer_gate', 'layer_duty', 'layer_plus', 'layer_hidden'):
            try:
                out[f] = json.loads(row[f] or '[]')
            except (json.JSONDecodeError, TypeError):
                out[f] = []
        out['requirements'] = db.rows_to_dicts(conn.execute(
            'SELECT * FROM job_requirement WHERE job_id = ? ORDER BY sort_order',
            (job_id,)).fetchall())
        out['questions'] = questions.coverage(conn, job_id)
        resume_rows = conn.execute(
            'SELECT id, name, status FROM resume WHERE job_id = ? ORDER BY id DESC',
            (job_id,)).fetchall()
        out['resumes'] = db.rows_to_dicts(resume_rows)
        out['interviews'] = db.rows_to_dicts(conn.execute(
            'SELECT id, round_type, status, started_at, score_json FROM interview '
            'WHERE job_id = ? ORDER BY id DESC', (job_id,)).fetchall())
        m = conn.execute('SELECT filename FROM material WHERE id = ?',
                         (row['material_id'],)).fetchone()
        out['material_name'] = m['filename'] if m else ''
        # 这次分析用的是哪份简历（让用户一眼看出"分析的是不是我最新那份"）
        if row['analyzed_resume_id']:
            r = conn.execute('SELECT id, name, updated_at FROM resume WHERE id = ?',
                             (row['analyzed_resume_id'],)).fetchone()
            out['analyzed_resume_name'] = r['name'] if r else ''
            out['analyzed_resume_updated'] = r['updated_at'] if r else ''
        else:
            out['analyzed_resume_name'] = ''
            out['analyzed_resume_updated'] = ''
        # 所有简历（给"换一份简历分析"用）
        out['all_resumes'] = db.rows_to_dicts(conn.execute(
            'SELECT id, name, job_id, updated_at, LENGTH(content) AS chars '
            'FROM resume ORDER BY updated_at DESC, id DESC').fetchall())
    return out


class AnalyzeIn(BaseModel):
    resume_id: int | None = None


@app.post('/api/jobs/{job_id}/analyze')
def analyze_job(job_id: int, body: AnalyzeIn | None = None) -> dict:
    """
    四层拆解 + 匹配度矩阵 + 投递建议。

    `resume_id` 不传就用最近更新的那份简历——匹配度是对着简历算的，
    只对着档案事实库算的话，简历里改过的东西它看不见。
    """
    from . import jobs as jobs_mod
    try:
        with db.session() as conn:
            return jobs_mod.analyze(conn, job_id,
                                    body.resume_id if body else None)
    except (ValueError, clients.ServiceError) as exc:
        raise HTTPException(400, str(exc)) from exc


class JobStatusIn(BaseModel):
    status: str


@app.patch('/api/jobs/{job_id}')
def patch_job(job_id: int, body: JobStatusIn) -> dict:
    if body.status not in db.VALID_JOB_STATUS:
        raise HTTPException(400, 'status 不合法')
    with db.session() as conn:
        conn.execute('UPDATE job SET status=?, updated_at=? WHERE id=?',
                     (body.status, db.now_iso(), job_id))
        row = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '岗位不存在')
    return dict(row)


# ══════════════════════════ 简历 ══════════════════════════

class ResumeIn(BaseModel):
    name: str
    job_id: int | None = None
    content: str = ''


@app.get('/api/resumes')
def api_resumes() -> dict:
    with db.session() as conn:
        rows = conn.execute(
            'SELECT r.*, j.company, j.title FROM resume r '
            'LEFT JOIN job j ON j.id = r.job_id ORDER BY r.id DESC').fetchall()
    return {'resumes': db.rows_to_dicts(rows)}


@app.post('/api/resumes')
def create_resume(body: ResumeIn) -> dict:
    now = db.now_iso()
    content = body.content
    if not content.strip():
        # 默认从简历基线拉一份
        # 简历基线：本地开发时用工作区里的那份；服务器上没有就跳过（从空白开始）
        baseline = config.workspace_dir() / '求职助手' / '00-档案' / '简历基线.md'
        if baseline.is_file():
            text = baseline.read_text(encoding='utf-8')
            idx = text.find('## 一、定制简历正文')
            content = text[idx:] if idx >= 0 else text
    with db.session() as conn:
        cur = conn.execute(
            'INSERT INTO resume (name, job_id, content, status, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?)',
            (body.name.strip() or '未命名简历', body.job_id, content, 'draft', now, now))
        rid = int(cur.lastrowid)
    return {'id': rid, 'chars': len(content)}


class ChatMessage(BaseModel):
    role: str = 'user'
    content: str = ''


class ResumeChatIn(BaseModel):
    messages: list[ChatMessage] = []
    job_id: int | None = None
    resume_id: int | None = None


@app.get('/api/resume-chat/session')
def resume_chat_session(new: bool = False) -> dict:
    """取当前对话（含历史消息和最新草稿），页面重进能接着聊。"""
    with db.session() as conn:
        sid = resume_chat.get_or_create_session(conn, new=new)
        return resume_chat.session_detail(conn, sid)


@app.delete('/api/resume-chat/session')
def resume_chat_reset() -> dict:
    """开一段新对话（旧的不删，只是不再取用）。"""
    with db.session() as conn:
        sid = resume_chat.get_or_create_session(conn, new=True)
        return {'session_id': sid, 'messages': [], 'draft': ''}


@app.post('/api/resume-chat')
def resume_chat_endpoint(body: ResumeChatIn) -> dict:
    """
    简历对话助手：能读全部资料，把资料组织成一份能投的简历。

    两条硬规矩：资料里没有的会明确说"需要你补充"（不替用户编）；
    产出的简历是草稿，要用户点确认才存进简历列表。
    每轮都落库，所以刷新页面不会丢对话，也能事后查。
    """
    try:
        with db.session() as conn:
            sid = resume_chat.get_or_create_session(conn, job_id=body.job_id,
                                                    resume_id=body.resume_id)
            msgs = [m.model_dump() for m in body.messages]
            if msgs:
                last = msgs[-1]
                if last.get('role') != 'assistant' and str(last.get('content', '')).strip():
                    resume_chat.add_message(conn, sid, 'user', str(last['content']))
            out = resume_chat.chat(conn, msgs, job_id=body.job_id,
                                   resume_id=body.resume_id, session_id=sid)
            out['session_id'] = sid
            return out
    except clients.ServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/resumes/{resume_id}')
def resume_detail(resume_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '简历不存在')
        return dict(row)


class ResumePatch(BaseModel):
    name: str | None = None
    content: str | None = None
    status: str | None = None
    job_id: int | None = None      # 指定/切换这份简历的目标岗位；显式传 null 表示取消关联


@app.delete('/api/resumes/{resume_id}')
def delete_resume(resume_id: int) -> dict:
    """
    删除一份简历。

    关联数据的处理（schema 里已经声明好）：
    - resume_suggestion 级联删除
    - question / practice_session / interview / application 的 resume_id 置空
      （题库和面试记录要留着——它们对岗位仍然有效）
    外键约束靠 db.connect() 里的 `PRAGMA foreign_keys = ON` 生效。
    """
    with db.session() as conn:
        row = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '简历不存在')
        kept_questions = conn.execute(
            'SELECT COUNT(*) AS n FROM question WHERE resume_id = ?', (resume_id,)).fetchone()['n']
        kept_interviews = conn.execute(
            'SELECT COUNT(*) AS n FROM interview WHERE resume_id = ?', (resume_id,)).fetchone()['n']
        conn.execute('DELETE FROM resume WHERE id = ?', (resume_id,))
        db.log_change(conn, 'resume', resume_id, 'delete', before=row['name'], after='')
    return {'ok': True, 'kept_questions': kept_questions, 'kept_interviews': kept_interviews}


@app.patch('/api/resumes/{resume_id}')
def patch_resume(resume_id: int, body: ResumePatch) -> dict:
    with db.session() as conn:
        row = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '简历不存在')
        name = body.name if body.name is not None else row['name']
        content = body.content if body.content is not None else row['content']
        status = body.status if body.status is not None else row['status']
        # job_id 用 model_fields_set 判断"有没有传"——传 null 是"取消关联"，
        # 不传是"不动它"。不区分的话就没法取消关联了。
        job_id = body.job_id if 'job_id' in body.model_fields_set else row['job_id']
        if job_id is not None:
            exists = conn.execute('SELECT id FROM job WHERE id = ?', (job_id,)).fetchone()
            if exists is None:
                raise HTTPException(400, '指定的岗位不存在')
        conn.execute(
            'UPDATE resume SET name=?, content=?, status=?, job_id=?, updated_at=? WHERE id=?',
            (name, content, status, job_id, db.now_iso(), resume_id))
        return dict(conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone())


# ══════════════════════════ 简历包装 ══════════════════════════

class PackagingIn(BaseModel):
    job_id: int
    resume_id: int


@app.post('/api/packaging/generate')
def packaging_generate(body: PackagingIn) -> dict:
    """生成逐条修改建议（耗时）。"""
    try:
        with db.session() as conn:
            return packaging.generate_suggestions(conn, body.job_id, body.resume_id)
    except (ValueError, clients.ServiceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/packaging/suggestions')
def packaging_list(resume_id: int, job_id: int | None = None) -> dict:
    with db.session() as conn:
        items = packaging.list_suggestions(conn, resume_id, job_id)
    counts = {'ok': 0, 'need': 0, 'risk': 0, 'pending': 0}
    for it in items:
        counts[it['grade']] = counts.get(it['grade'], 0) + 1
        if it['decision'] == db.SUGGESTION_PENDING:
            counts['pending'] += 1
    return {'suggestions': items, 'counts': counts, 'total': len(items)}


class DecideIn(BaseModel):
    decision: str
    edited_text: str = ''


@app.post('/api/packaging/suggestions/{suggestion_id}/decide')
def packaging_decide(suggestion_id: int, body: DecideIn) -> dict:
    try:
        with db.session() as conn:
            return packaging.decide_suggestion(conn, suggestion_id, body.decision,
                                               body.edited_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ApplyIn(BaseModel):
    resume_id: int


@app.post('/api/packaging/apply')
def packaging_apply(body: ApplyIn) -> dict:
    with db.session() as conn:
        return packaging.apply_accepted(conn, body.resume_id)


# ══════════════════════════ 题库 ══════════════════════════

class GenerateQuestionsIn(BaseModel):
    job_id: int
    resume_id: int
    round_type: str = ''
    count: int = 20


@app.post('/api/questions/generate')
def questions_generate(body: GenerateQuestionsIn) -> dict:
    try:
        with db.session() as conn:
            return questions.generate_questions(conn, body.job_id, body.resume_id,
                                                body.round_type, body.count)
    except (ValueError, clients.ServiceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/questions')
def api_questions(job_id: int | None = None, status: str = '',
                  round_type: str = '') -> dict:
    with db.session() as conn:
        items = questions.list_questions(conn, job_id, status, round_type)
    return {'questions': items, 'total': len(items), 'rounds': ROUND_LABEL}


class QuestionStatusIn(BaseModel):
    status: str


@app.patch('/api/questions/{question_id}')
def patch_question(question_id: int, body: QuestionStatusIn) -> dict:
    try:
        with db.session() as conn:
            item = questions.set_status(conn, question_id, body.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if item is None:
        raise HTTPException(404, '题目不存在')
    return item


# ══════════════════════════ 练习模式 ══════════════════════════

class PracticeStart(BaseModel):
    job_id: int
    resume_id: int


@app.post('/api/practice/start')
def practice_start(body: PracticeStart) -> dict:
    with db.session() as conn:
        items = questions.list_questions(conn, body.job_id)
        if not items:
            raise HTTPException(400, '这个岗位还没有题库，请先生成题库')
        cur = conn.execute(
            'INSERT INTO practice_session (job_id, resume_id, total, started_at) '
            'VALUES (?,?,?,?)', (body.job_id, body.resume_id, len(items), db.now_iso()))
    return {'session_id': int(cur.lastrowid), 'total': len(items)}


class PracticeAnswer(BaseModel):
    session_id: int
    question_id: int
    answer: str = ''
    input_mode: str = 'voice'
    looked_first: bool = False


@app.post('/api/practice/answer')
def practice_answer(body: PracticeAnswer) -> dict:
    with db.session() as conn:
        conn.execute(
            'INSERT INTO practice_answer (session_id, question_id, answer, input_mode, '
            'looked_first, created_at) VALUES (?,?,?,?,?,?)',
            (body.session_id, body.question_id, body.answer.strip(), body.input_mode,
             1 if body.looked_first else 0, db.now_iso()))
        if body.answer.strip():
            conn.execute(
                "UPDATE practice_session SET answered = answered + 1 WHERE id = ?",
                (body.session_id,))
        if body.looked_first:
            conn.execute(
                'UPDATE practice_session SET looked = looked + 1 WHERE id = ?',
                (body.session_id,))
        conn.execute(
            "UPDATE question SET status = CASE WHEN status='unseen' THEN 'seen' ELSE status END "
            'WHERE id = ?', (body.question_id,))
    return {'ok': True}


@app.post('/api/practice/{session_id}/finish')
def practice_finish(session_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute('SELECT * FROM practice_session WHERE id = ?',
                           (session_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '练习会话不存在')
        conn.execute('UPDATE practice_session SET ended_at = ? WHERE id = ?',
                     (db.now_iso(), session_id))
        review = conn.execute(
            "SELECT COUNT(*) FROM question WHERE job_id = ? AND status = 'review'",
            (row['job_id'],)).fetchone()[0]
        return {'total': row['total'], 'answered': row['answered'],
                'looked': row['looked'], 'review': review}


# ══════════════════════════ 模拟面试 ══════════════════════════

class InterviewStart(BaseModel):
    job_id: int
    resume_id: int
    round_type: str = 'tech1'
    pressure: str = 'normal'


@app.post('/api/interviews')
def interview_start(body: InterviewStart) -> dict:
    try:
        with db.session() as conn:
            return interview.start(conn, body.job_id, body.resume_id,
                                   body.round_type, body.pressure)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete('/api/interviews/abandoned')
def cleanup_abandoned_interviews() -> dict:
    """
    清理「只看了第一题就退出」的面试（开场问题都没答过）。

    这些记录没有评分价值，堆在记录列表里会把场次数字撑虚。
    只删**一个字都没答过**的，答过一句的都留着（那是有内容的记录）。
    """
    with db.session() as conn:
        rows = conn.execute(
            "SELECT id FROM interview i WHERE i.status='running' AND NOT EXISTS ("
            "  SELECT 1 FROM interview_turn t WHERE t.interview_id=i.id AND t.answer != '')"
        ).fetchall()
        ids = [r['id'] for r in rows]
        for iid in ids:
            conn.execute('DELETE FROM interview_turn WHERE interview_id = ?', (iid,))
            conn.execute('DELETE FROM interview WHERE id = ?', (iid,))
        if ids:
            db.log_change(conn, 'interview', 0, 'cleanup_abandoned',
                          after=f'{len(ids)} 场')
    return {'ok': True, 'removed': len(ids)}


@app.get('/api/interviews')
def interview_list() -> dict:
    with db.session() as conn:
        return {'interviews': interview.list_interviews(conn)}


@app.post('/api/interviews/{interview_id}/reviewed')
def mark_interview_reviewed(interview_id: int) -> dict:
    """
    把一场面试标记为「已复盘」。

    ★ 之前 reviewed_at 这个字段**从来没有被写过**，所以工作台的「待复盘」
    只会越涨越多、永远清不掉——用户看完了报告，系统也不知道他看过了。
    打开复盘页就是复盘这个动作本身，进页面时调一下这里。
    """
    with db.session() as conn:
        row = conn.execute('SELECT * FROM interview WHERE id = ?',
                           (interview_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '面试不存在')
        if not row['reviewed_at']:
            conn.execute('UPDATE interview SET reviewed_at = ? WHERE id = ?',
                         (db.now_iso(), interview_id))
    return {'ok': True}


@app.get('/api/interviews/{interview_id}')
def interview_detail(interview_id: int) -> dict:
    with db.session() as conn:
        data = interview.detail(conn, interview_id)
    if data is None:
        raise HTTPException(404, '面试不存在')
    return data


class InterviewAnswer(BaseModel):
    text: str
    input_mode: str = 'voice'
    is_followup: bool = False


@app.post('/api/interviews/{interview_id}/answer')
def interview_answer(interview_id: int, body: InterviewAnswer) -> dict:
    try:
        with db.session() as conn:
            if body.is_followup:
                # 先把这句存进追问链，再让 answer 判断下一步；
                # 注意 is_followup 要透传，否则它会把主问题的回答覆盖掉
                interview.submit_followup_answer(conn, interview_id, body.text)
            return interview.answer(conn, interview_id, body.text,
                                    body.input_mode, is_followup=body.is_followup)
    except (ValueError, clients.ServiceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post('/api/interviews/{interview_id}/finish')
def interview_finish(interview_id: int) -> dict:
    try:
        with db.session() as conn:
            return interview.abort(conn, interview_id)
    except (ValueError, clients.ServiceError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ══════════════════════════ 语音 ══════════════════════════

class TTSIn(BaseModel):
    text: str
    voice: str = ''


@app.post('/api/voice/tts')
def voice_tts(body: TTSIn) -> Response:
    try:
        audio = clients.synthesize(body.text, body.voice)
    except clients.ServiceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(content=audio, media_type='audio/wav',
                    headers={'cache-control': 'no-store'})


@app.post('/api/voice/tts-stream')
async def voice_tts_stream(body: TTSIn) -> StreamingResponse:
    """
    流式语音合成：边合成边吐 PCM，客户端边收边播。

    首块音频 ~0.45s 就能出来（整段合成要 3s），对话的"接话感"全靠这个。
    返回的是裸 PCM（16-bit / 24kHz / 单声道），前端用 Web Audio 直接播。
    """
    async def gen():
        try:
            async for chunk in clients.synthesize_stream(body.text, body.voice):
                yield chunk
        except clients.ServiceError as exc:
            # 已经开流了，改不了状态码，只能记日志（前端会因为没有音频而走兜底）
            logger.warning('流式合成中断：%s', exc)

    return StreamingResponse(
        gen(), media_type='audio/L16',
        headers={'X-Sample-Rate': str(clients.TTS_SAMPLE_RATE),
                 'Cache-Control': 'no-store'})


@app.post('/api/voice/asr')
async def voice_asr(file: UploadFile = File(...)) -> dict:
    """接收录音（wav），返回识别文本。"""
    content = await file.read()
    if not content:
        raise HTTPException(400, '录音是空的')
    fmt = (Path(file.filename or 'a.wav').suffix.lstrip('.') or 'wav').lower()
    try:
        text = clients.transcribe(content, audio_format=fmt)
    except clients.ServiceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'text': text}


@app.get('/api/voice/status')
def voice_status() -> dict:
    return clients.health()


# ══════════════════════════ 投递看板 ══════════════════════════

@app.get('/api/applications')
def api_applications() -> dict:
    with db.session() as conn:
        rows = conn.execute(
            'SELECT a.*, j.company, j.title, j.salary, j.city FROM application a '
            'JOIN job j ON j.id = a.job_id ORDER BY a.id DESC').fetchall()
        stats = db.stats(conn)
        applied = conn.execute(
            "SELECT COUNT(*) FROM application WHERE status != 'to_apply'").fetchone()[0]
        interviewed = conn.execute(
            "SELECT COUNT(*) FROM application WHERE status IN ('interview','closed')"
        ).fetchone()[0]
    total = len(rows)
    return {
        'applications': db.rows_to_dicts(rows),
        'stats': {
            'total': total,
            'applied': applied,
            'interviewed': interviewed,
            'interview_rate': round(interviewed / applied * 100, 1) if applied else 0.0,
        },
        'global': stats,
    }


class ApplicationIn(BaseModel):
    job_id: int
    resume_id: int | None = None
    status: str = 'to_apply'
    note: str = ''


@app.post('/api/applications')
def upsert_application(body: ApplicationIn) -> dict:
    now = db.now_iso()
    with db.session() as conn:
        existing = conn.execute('SELECT id FROM application WHERE job_id = ?',
                                (body.job_id,)).fetchone()
        applied_at = now if body.status not in ('to_apply',) else ''
        if existing:
            conn.execute('UPDATE application SET resume_id=?, status=?, note=?, '
                         'applied_at=CASE WHEN ? THEN ? ELSE applied_at END, updated_at=? '
                         'WHERE id=?',
                         (body.resume_id, body.status, body.note,
                          body.status != 'to_apply', now, now, existing['id']))
            aid = existing['id']
        else:
            cur = conn.execute(
                'INSERT INTO application (job_id, resume_id, status, applied_at, note, '
                'updated_at) VALUES (?,?,?,?,?,?)',
                (body.job_id, body.resume_id, body.status, applied_at, body.note, now))
            aid = int(cur.lastrowid)
        return dict(conn.execute('SELECT * FROM application WHERE id = ?', (aid,)).fetchone())


# ══════════════════════════ 页面 ══════════════════════════

# 本地工具，缓存没有收益，只会让改动看不见
NO_CACHE = {'Cache-Control': 'no-cache, must-revalidate'}

def _build_stamp() -> str:
    """前端资源的最新修改时间，作为版本号。"""
    newest = 0.0
    for pattern in ('js/**/*.js', 'css/**/*.css'):
        for f in WEB_DIR.glob(pattern):
            newest = max(newest, f.stat().st_mtime)
    import datetime
    return datetime.datetime.fromtimestamp(newest).strftime('%m%d%H%M%S')


@app.get('/')
def index() -> Response:
    """
    首页。

    ★ 这里**故意不给静态资源加 `?v=` 版本号**，虽然那样能绕开缓存。
    原因：ES Module 的 import 是按 URL 去重的，而页面模块里写的是
    `import ... from '../app.js'`（不带参数）。入口用 `app.js?v=xxx`、
    import 用 `app.js`，浏览器会当成**两个不同的模块**各执行一遍——
    于是有两份模块状态、两条渲染管线：页面内容会出现两份，
    通话页会**同时请求两次语音合成**（听起来就是两条音轨在念同一道题）。
    这个坑我踩过。

    防缓存改用响应头：所有静态资源都发 `no-cache, must-revalidate`，
    浏览器每次会回源校验，变了就取新的，没变走 304。
    """
    html = (WEB_DIR / 'index.html').read_text(encoding='utf-8')
    return Response(html, media_type='text/html', headers=NO_CACHE)


class NoCacheStatic(StaticFiles):
    """
    静态资源每次都回源校验（304 就够，不会真的重传）。

    为什么要动这个：默认只发 etag / last-modified，浏览器会**启发式缓存**。
    而且这个前端是 SPA——页面不刷新的话，改完 JS 用户还会一直跑旧代码，
    现象就是「我改了你怎么没变化」。开发期这种缓存只会添乱。
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return resp


app.mount('/static', NoCacheStatic(directory=str(WEB_DIR)), name='static')
