"""
数据库层：SQLite 建表与连接。

## 核心设计：每一条事实都带 provenance

`source`（来源）+ `as_of`（数据时点）+ `status`（确认状态）三件套贯穿所有表。
导入或 AI 生成的内容一律是草稿，必须由用户确认才成为可用于简历/面试的事实。

## 表分组

    档案层：profile_field / project / project_point
    材料层：material / material_fact
    岗位层：job / job_requirement
    简历层：resume / resume_suggestion
    题库层：question
    练习层：practice_session / practice_answer
    面试层：interview / interview_turn
    投递层：application
    系统：  change_log
"""

from __future__ import annotations

import sqlite3
import contextvars
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

DB_PATH = DATA_DIR / 'job_assistant.db'

# ── 状态枚举 ────────────────────────────────────────────────────────────

STATUS_UNCONFIRMED = 'unconfirmed'
STATUS_CONFIRMED = 'confirmed'
STATUS_CORRECTED = 'corrected'
STATUS_OBSOLETE = 'obsolete'
VALID_STATUS = (STATUS_UNCONFIRMED, STATUS_CONFIRMED, STATUS_CORRECTED, STATUS_OBSOLETE)

# 简历建议分级
GRADE_OK = 'ok'            # 可直接采纳
GRADE_NEED = 'need'        # 需要补充
GRADE_RISK = 'risk'        # 不建议写
VALID_GRADE = (GRADE_OK, GRADE_NEED, GRADE_RISK)

SUGGESTION_PENDING = 'pending'
SUGGESTION_ACCEPTED = 'accepted'
SUGGESTION_REJECTED = 'rejected'
SUGGESTION_EDITED = 'edited'

# 匹配强度
STRENGTH_STRONG = 'strong'
STRENGTH_MEDIUM = 'medium'
STRENGTH_WEAK = 'weak'
STRENGTH_NONE = 'none'
VALID_STRENGTH = (STRENGTH_STRONG, STRENGTH_MEDIUM, STRENGTH_WEAK, STRENGTH_NONE)

# 材料解析状态
MATERIAL_PENDING = 'pending'
MATERIAL_PARSING = 'parsing'
MATERIAL_DONE = 'done'
MATERIAL_FAILED = 'failed'

# 岗位状态
JOB_DRAFT = 'draft'
JOB_TO_APPLY = 'to_apply'
JOB_APPLIED = 'applied'
JOB_INTERVIEW = 'interview'
JOB_CLOSED = 'closed'
VALID_JOB_STATUS = (JOB_DRAFT, JOB_TO_APPLY, JOB_APPLIED, JOB_INTERVIEW, JOB_CLOSED)

# 题目掌握状态
Q_UNSEEN = 'unseen'
Q_SEEN = 'seen'
Q_MASTERED = 'mastered'
Q_REVIEW = 'review'

# 面试模式
MODE_PRACTICE = 'practice'
MODE_MOCK = 'mock'


SCHEMA = """
-- ══════════════ 档案层 ══════════════
CREATE TABLE IF NOT EXISTS profile_field (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    section     TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT '',
    as_of       TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'unconfirmed',
    note        TEXT    NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL,
    UNIQUE(section, key)
);

CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    role        TEXT    NOT NULL DEFAULT '',
    period      TEXT    NOT NULL DEFAULT '',
    summary     TEXT    NOT NULL DEFAULT '',
    card_path   TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    as_of       TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'unconfirmed',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    summary_doc TEXT    NOT NULL DEFAULT '',  -- 项目总结文档（Markdown，供预览）
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS project_point (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL DEFAULT 'point',
    text        TEXT    NOT NULL,
    metric      TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'unconfirmed',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL
);

-- ══════════════ 材料层 ══════════════
CREATE TABLE IF NOT EXISTS material (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT    NOT NULL,
    stored_path TEXT    NOT NULL,
    media_type  TEXT    NOT NULL,               -- image/jpeg | application/pdf
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    project_id  INTEGER REFERENCES project(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    error       TEXT    NOT NULL DEFAULT '',
    raw_text    TEXT    NOT NULL DEFAULT '',    -- 解析出的全文（PDF 文本层或视觉转写）
    summary     TEXT    NOT NULL DEFAULT '',
    source_url  TEXT    NOT NULL DEFAULT '',    -- GitHub 仓库地址等外部来源
    kind        TEXT    NOT NULL DEFAULT 'file',-- file | archive | repo
    doc_kind    TEXT    NOT NULL DEFAULT '',    -- 模型判定的文档类型：jd/resume/report/prd/repo
    doc_reviewed INTEGER NOT NULL DEFAULT 0,    -- 用户是否核对过这份材料的解析结果
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS material_fact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES material(id) ON DELETE CASCADE,
    text        TEXT    NOT NULL,
    locator     TEXT    NOT NULL DEFAULT '',    -- 页码 / 章节，用于溯源
    status      TEXT    NOT NULL DEFAULT 'unconfirmed',
    created_at  TEXT    NOT NULL
);

-- ══════════════ 简历对话助手 ══════════════
CREATE TABLE IF NOT EXISTS chat_session (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER REFERENCES job(id) ON DELETE SET NULL,
    resume_id   INTEGER REFERENCES resume(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL,               -- user | assistant
    content     TEXT    NOT NULL DEFAULT '',
    questions   TEXT    NOT NULL DEFAULT '[]',  -- 助手反问用户的问题
    draft       TEXT    NOT NULL DEFAULT '',    -- 这一轮产出的简历草稿（没有则空）
    created_at  TEXT    NOT NULL
);

-- ══════════════ 岗位层 ══════════════
CREATE TABLE IF NOT EXISTS job (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT    NOT NULL DEFAULT '',
    title       TEXT    NOT NULL DEFAULT '',
    salary      TEXT    NOT NULL DEFAULT '',
    city        TEXT    NOT NULL DEFAULT '',
    raw_text    TEXT    NOT NULL DEFAULT '',
    material_id INTEGER REFERENCES material(id) ON DELETE SET NULL,
    -- 四层拆解（JSON 数组，字符串存储）
    layer_gate  TEXT    NOT NULL DEFAULT '[]',
    layer_duty  TEXT    NOT NULL DEFAULT '[]',
    layer_plus  TEXT    NOT NULL DEFAULT '[]',
    layer_hidden TEXT   NOT NULL DEFAULT '[]',
    analyzed_resume_id INTEGER REFERENCES resume(id) ON DELETE SET NULL,  -- 这次分析用的是哪份简历
    score       INTEGER NOT NULL DEFAULT -1,     -- -1 表示未分析
    verdict     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'draft',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS job_requirement (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    text        TEXT    NOT NULL,
    layer       TEXT    NOT NULL DEFAULT 'duty',  -- gate | duty | plus | hidden
    weight      TEXT    NOT NULL DEFAULT 'mid',   -- high | mid | low
    evidence    TEXT    NOT NULL DEFAULT '',
    strength    TEXT    NOT NULL DEFAULT 'none',
    strategy    TEXT    NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- ══════════════ 简历层 ══════════════
CREATE TABLE IF NOT EXISTS resume (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    job_id      INTEGER REFERENCES job(id) ON DELETE SET NULL,
    content     TEXT    NOT NULL DEFAULT '',      -- markdown 正文
    status      TEXT    NOT NULL DEFAULT 'draft', -- draft | exported | applied
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_suggestion (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id   INTEGER NOT NULL REFERENCES resume(id) ON DELETE CASCADE,
    job_id      INTEGER REFERENCES job(id) ON DELETE SET NULL,
    location    TEXT    NOT NULL DEFAULT '',      -- 如「项目经历 · Video Note · 第 3 条」
    before_text TEXT    NOT NULL DEFAULT '',
    after_text  TEXT    NOT NULL DEFAULT '',
    reason      TEXT    NOT NULL DEFAULT '',      -- 为什么
    evidence    TEXT    NOT NULL DEFAULT '',      -- 依据（JSON 数组）
    grade       TEXT    NOT NULL DEFAULT 'ok',    -- ok | need | risk
    missing     TEXT    NOT NULL DEFAULT '',      -- need 时：缺什么
    decision    TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- ══════════════ 题库层 ══════════════
CREATE TABLE IF NOT EXISTS question (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       INTEGER REFERENCES job(id) ON DELETE CASCADE,
    resume_id    INTEGER REFERENCES resume(id) ON DELETE SET NULL,
    question     TEXT    NOT NULL,
    standard     TEXT    NOT NULL DEFAULT '',     -- 标准答案（结合项目）
    probe        TEXT    NOT NULL DEFAULT '',     -- 面试官在考什么
    key_points   TEXT    NOT NULL DEFAULT '[]',   -- 面试官想听的重点 [{do:..,text:..}]
    followups    TEXT    NOT NULL DEFAULT '[]',   -- 追问链
    round_type   TEXT    NOT NULL DEFAULT 'tech1',
    is_risk      INTEGER NOT NULL DEFAULT 0,      -- 是否风险题
    risk_note    TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'unseen',
    created_at   TEXT    NOT NULL
);

-- ══════════════ 练习层 ══════════════
CREATE TABLE IF NOT EXISTS practice_session (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER REFERENCES job(id) ON DELETE SET NULL,
    resume_id   INTEGER REFERENCES resume(id) ON DELETE SET NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    answered    INTEGER NOT NULL DEFAULT 0,
    looked      INTEGER NOT NULL DEFAULT 0,       -- 直接看答案的次数
    started_at  TEXT    NOT NULL,
    ended_at    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS practice_answer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES practice_session(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES question(id) ON DELETE CASCADE,
    answer      TEXT    NOT NULL DEFAULT '',
    input_mode  TEXT    NOT NULL DEFAULT 'voice',  -- voice | text
    looked_first INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

-- ══════════════ 面试层 ══════════════
CREATE TABLE IF NOT EXISTS interview (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER REFERENCES job(id) ON DELETE SET NULL,
    resume_id   INTEGER REFERENCES resume(id) ON DELETE SET NULL,
    round_type  TEXT    NOT NULL DEFAULT 'tech1',
    pressure    TEXT    NOT NULL DEFAULT 'normal',
    mode        TEXT    NOT NULL DEFAULT 'mock',
    status      TEXT    NOT NULL DEFAULT 'running',  -- running | finished
    started_at  TEXT    NOT NULL,
    ended_at    TEXT    NOT NULL DEFAULT '',
    duration_sec INTEGER NOT NULL DEFAULT 0,
    score_json  TEXT    NOT NULL DEFAULT '{}',
    summary     TEXT    NOT NULL DEFAULT '',
    highlights  TEXT    NOT NULL DEFAULT '[]',
    dangers     TEXT    NOT NULL DEFAULT '[]',
    review      TEXT    NOT NULL DEFAULT '',
    reviewed_at TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS interview_turn (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id INTEGER NOT NULL REFERENCES interview(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL DEFAULT 0,
    question     TEXT    NOT NULL DEFAULT '',
    answer       TEXT    NOT NULL DEFAULT '',
    followups    TEXT    NOT NULL DEFAULT '[]',
    probe        TEXT    NOT NULL DEFAULT '',
    input_mode   TEXT    NOT NULL DEFAULT 'voice',
    created_at   TEXT    NOT NULL
);

-- ══════════════ 投递层 ══════════════
CREATE TABLE IF NOT EXISTS application (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    resume_id   INTEGER REFERENCES resume(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'to_apply',
    applied_at  TEXT    NOT NULL DEFAULT '',
    next_step   TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    updated_at  TEXT    NOT NULL,
    UNIQUE(job_id)
);

-- ══════════════ 系统 ══════════════
CREATE TABLE IF NOT EXISTS change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity      TEXT    NOT NULL,
    entity_id   INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    before      TEXT    NOT NULL DEFAULT '',
    after       TEXT    NOT NULL DEFAULT '',
    at          TEXT    NOT NULL
);

-- ══════════════ 索引 ══════════════
CREATE INDEX IF NOT EXISTS idx_field_section ON profile_field(section, sort_order);
CREATE INDEX IF NOT EXISTS idx_point_project ON project_point(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_fact_material ON material_fact(material_id);
CREATE INDEX IF NOT EXISTS idx_req_job ON job_requirement(job_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_sugg_resume ON resume_suggestion(resume_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_question_job ON question(job_id);
CREATE INDEX IF NOT EXISTS idx_turn_interview ON interview_turn(interview_id, seq);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')


# ══════════════ 多用户：一人一个库 ══════════════
#
# 当前请求属于谁，用 ContextVar 带过来（中间件设置，业务代码完全不用改）。
# 这样 57 处 `with db.session()` 一行都不用动，就实现了数据隔离。
#
# 为什么不用"每张表加 user_id"：那要改上百条手写 SQL，
# 漏一处 WHERE 就是朋友能看到你的简历。文件级隔离则不可能串。
_current_user: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    'ja_user_id', default=None)


def set_current_user(user_id: int | None) -> None:
    _current_user.set(user_id)


def current_user() -> int | None:
    return _current_user.get()


def user_db_path(user_id: int) -> Path:
    d = DB_PATH.parent / 'users'
    d.mkdir(parents=True, exist_ok=True)
    return d / f'u{user_id}.db'


def _level() -> str:
    """当前该连哪个库。没人登录时退回单用户模式（本地开发仍然照旧能用）。"""
    uid = _current_user.get()
    if uid is None:
        return 'legacy'
    return str(uid)


def connect() -> sqlite3.Connection:
    level = _level()
    path = DB_PATH if level == 'legacy' else user_db_path(int(level))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    # WAL：多用户各写各的库，加上 WAL 更抗并发
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_user_db(user_id: int) -> None:
    """把某个用户的业务库建出来（建号时调用一次）。"""
    uid = _current_user.get()
    _current_user.set(user_id)
    try:
        with session() as conn:
            conn.executescript(SCHEMA)
            _ensure_columns(conn)
    finally:
        _current_user.set(uid)


# 后加的列：老数据库里没有，CREATE TABLE IF NOT EXISTS 补不上，得显式 ALTER
_ADDED_COLUMNS = [
    ('material', 'source_url', "TEXT NOT NULL DEFAULT ''"),
    ('material', 'kind', "TEXT NOT NULL DEFAULT 'file'"),
    ('project', 'summary_doc', "TEXT NOT NULL DEFAULT ''"),
    ('material', 'doc_kind', "TEXT NOT NULL DEFAULT ''"),
    ('material', 'doc_reviewed', 'INTEGER NOT NULL DEFAULT 0'),
    ('job', 'analyzed_resume_id', 'INTEGER'),
    ('interview_turn', 'topic_index', 'INTEGER'),
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """幂等地把后加的列补到已有表上（本地工具，不做完整的迁移框架）。"""
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
        if column not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {decl}')


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)


def log_change(conn: sqlite3.Connection, entity: str, entity_id: int,
               action: str, before: str = '', after: str = '') -> None:
    conn.execute(
        'INSERT INTO change_log (entity, entity_id, action, before, after, at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (entity, entity_id, action, before, after, now_iso()))


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def stats(conn: sqlite3.Connection) -> dict:
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        'fields_total': count('SELECT COUNT(*) FROM profile_field'),
        'fields_unconfirmed': count(
            "SELECT COUNT(*) FROM profile_field WHERE status='unconfirmed'"),
        'projects_total': count('SELECT COUNT(*) FROM project'),
        'projects_unconfirmed': count(
            "SELECT COUNT(*) FROM project WHERE status='unconfirmed'"),
        'points_total': count('SELECT COUNT(*) FROM project_point'),
        'points_unconfirmed': count(
            "SELECT COUNT(*) FROM project_point WHERE status='unconfirmed'"),
        'materials_total': count('SELECT COUNT(*) FROM material'),
        # 工作台只要两样：岗位要求材料 / 简历·项目材料
        'materials_jd': count(
            "SELECT COUNT(*) FROM material WHERE doc_kind = 'jd' "
            "OR id IN (SELECT material_id FROM job WHERE material_id IS NOT NULL)"),
        'materials_resume': count("SELECT COUNT(*) FROM material WHERE doc_kind = 'resume'"),
        'materials_project': count(
            "SELECT COUNT(*) FROM material WHERE kind IN ('archive','repo') "
            "OR doc_kind IN ('report','prd','repo') "
            "OR (doc_kind NOT IN ('jd','resume') AND project_id IS NOT NULL)"),
        'jobs_total': count('SELECT COUNT(*) FROM job'),
        'resumes_total': count('SELECT COUNT(*) FROM resume'),
        'suggestions_pending': count(
            "SELECT COUNT(*) FROM resume_suggestion WHERE decision='pending'"),
        'questions_total': count('SELECT COUNT(*) FROM question'),
        'questions_review': count("SELECT COUNT(*) FROM question WHERE status='review'"),
        # ★ 「已掌握」要老老实实数 status='mastered'。
        # 之前工作台用 total - review 当已掌握，结果 26 道全是未练却显示「26/26 已掌握」。
        'questions_mastered': count("SELECT COUNT(*) FROM question WHERE status='mastered'"),
        'questions_practiced': count(
            "SELECT COUNT(*) FROM question WHERE status IN ('seen','mastered','review')"),
        # 材料的解析用户还没核对过（核对是现在唯一的确认动作）
        'materials_unreviewed': count(
            "SELECT COUNT(*) FROM material WHERE status='done' AND doc_reviewed=0"),
        'interviews_total': count('SELECT COUNT(*) FROM interview'),
        # 「面试了几场」只算真答过的：开场问题都没答就退出的不算一场面试，
        # 否则数字会虚高（实测 35 场里有 21 场是只看了第一题就关掉的）
        'interviews_finished': count("SELECT COUNT(*) FROM interview WHERE status='finished'"),
        'interviews_abandoned': count(
            "SELECT COUNT(*) FROM interview i WHERE i.status='running' AND NOT EXISTS ("
            "  SELECT 1 FROM interview_turn t WHERE t.interview_id=i.id AND t.answer != '')"),
        'interviews_unreviewed': count(
            "SELECT COUNT(*) FROM interview WHERE status='finished' AND reviewed_at=''"),
        'applications_total': count('SELECT COUNT(*) FROM application'),
    }
