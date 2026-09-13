"""
题库生成：以面试官视角，针对「这个岗位 + 这份简历」出题。

这是整个产品最核心的能力——没有它，模拟面试只能问通用问题，练了没用。

每道题包含五块：
    问题 / 标准答案（结合真实项目）/ 面试官在考什么 / 面试官想听的重点 / 追问链

生成来源按优先级：
    1. 简历里最可能被深挖的项目（占大头）
    2. 岗位要求的核心职责（逐条对应）
    3. 简历里经不起推敲的地方——这些会标成 risk 题
"""

from __future__ import annotations

import json
import sqlite3

from . import clients, db
from .packaging import build_fact_base, build_job_context

ROUND_LABEL = {
    'hr': 'HR 面',
    'tech1': '技术一面',
    'tech2': '技术二面',
    'cross': '交叉面',
    'product': '产品面',
    'final': '总监面',
}

SYSTEM_PROMPT = """你是一位资深的技术面试官，正在为一次真实的面试准备题库。

你手上有一份候选人的简历、他的完整事实库（含项目细节），以及一份目标岗位的要求。

## 你的任务

站在面试官视角，问出**真正会问的问题**——不是通用八股，而是针对这份简历和这个岗位，
我会想验证什么。

## 出题的四条原则

1. **问题必须针对具体内容。** 「讲一下你的项目」不是好问题；
   「你说这个功能日活 1400 人，这个数是怎么统计的」才是。

2. **标准答案必须结合他的真实项目。** 你要从事实库里取材，
   给出这个人**应该怎么答**，而不是一个通用模板。
   如果事实库里信息不足，就在标准答案里说明"需要你补充 X"。

3. **必须给出追问链。** 真实面试官会追问 2-3 层。
   追问要体现三种套路：往下挖细节（怎么实现的）、往边上推约束（如果…呢）、
   往回想取舍（为什么不用 B 方案）。

4. **识别风险题。** 简历里这些地方一定会被撞上，必须单独出题：
   - 有"提升了性能""优化了流程"但没有数字的表述
   - 数字缺口径（百分比怎么算的）
   - 时间线可疑（集中几天完成的大项目）
   - 写了但可能答不上来的技术栈

## 难度分布

- 60% 深入考察他的**核心项目**（他最强的地方，面试官会挖到底）
- 30% 考察**岗位要求的核心职责**是否有对应经验
- 10% 考察**边界**（他明确没做过的，看他怎么诚实应对）

## 输出格式

只返回 JSON，不要解释：

{
  "questions": [
    {
      "question": "问题原文（口语化，像真的在问）",
      "standard": "标准答案。结合他的真实项目给出应该怎么答，150-400 字。",
      "probe": "面试官在考什么——往往不是字面意思，而是想验证的底层能力",
      "key_points": [
        {"do": true, "text": "该说的要点"},
        {"do": false, "text": "别说的 / 扣分点"}
      ],
      "followups": ["追问 1", "追问 2"],
      "round_type": "hr | tech1 | tech2 | cross | product | final",
      "is_risk": false,
      "risk_note": "如果是风险题，说明风险在哪；否则留空"
    }
  ]
}

生成 18-25 道题。"""


BATCH_SIZE = 6          # 每批生成几道题（首次尝试的上限）
MIN_BATCH_SIZE = 2      # 自适应缩容的下限
MAX_OUTPUT_TOKENS = 8000


def generate_questions(conn: sqlite3.Connection, job_id: int, resume_id: int,
                       round_type: str = '', count: int = 20) -> dict:
    """
    为「岗位 + 简历」生成题库。

    **为什么要分批**：一道题包含标准答案（150-400 字）+ 考察点 + 要点 + 追问链，
    约 500-700 token。一次要 20 道就是 12000+ token，**超过模型单次输出上限**，
    返回会被截断，JSON 直接解析失败（这是实测踩到的坑）。
    所以按 BATCH_SIZE 分批调用，每批都留足余量。
    """
    resume = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
    if resume is None:
        raise ValueError('简历不存在')

    job_ctx = build_job_context(conn, job_id)
    fact_base = build_fact_base(conn)

    round_hint = ''
    if round_type and round_type in ROUND_LABEL:
        round_hint = f'\n\n**本次请集中出「{ROUND_LABEL[round_type]}」的题目。**'

    base_prompt = f"""{job_ctx}

---

# 事实库（标准答案从这里取材）

{fact_base}

---

# 候选人简历

{resume['content'] or '（简历内容为空）'}
"""

    # 已生成过就清空，避免重复堆积
    conn.execute('DELETE FROM question WHERE job_id = ? AND resume_id = ?',
                 (job_id, resume_id))

    now = db.now_iso()
    added = 0
    batches = 0
    errors: list[str] = []
    remaining = max(1, count)
    asked: list[str] = []
    batch_cap = BATCH_SIZE      # 撞到输出上限就自动缩容，避免整批作废

    while remaining > 0:
        take = min(batch_cap, remaining)
        avoid = ''
        if asked:
            avoid = ('\n\n# 已经出过的题（**不要重复**）\n'
                     + '\n'.join(f'- {q}' for q in asked[-24:]))

        prompt = (f'{base_prompt}{avoid}\n\n---\n\n'
                  f'请出 {take} 道题，覆盖：他的核心项目深挖、岗位要求的核心职责、'
                  f'以及简历里经不起推敲的地方。{round_hint}')

        meta: dict = {}
        try:
            parsed = clients.chat_json(
                [{'role': 'system', 'content': SYSTEM_PROMPT},
                 {'role': 'user', 'content': prompt}],
                temperature=0.5, max_tokens=MAX_OUTPUT_TOKENS, meta=meta)
        except clients.ServiceError as exc:
            errors.append(str(exc))
            break

        items = parsed.get('questions') if isinstance(parsed, dict) else parsed
        truncated = bool(meta.get('truncated'))
        if not isinstance(items, list) or not items:
            # 整批被截断且抢救不出完整题目 → 缩容重试（不会死循环：cap 单调减半）
            if truncated and batch_cap > MIN_BATCH_SIZE:
                batch_cap = max(MIN_BATCH_SIZE, batch_cap // 2)
                errors.append(f'本批输出被截断，已把每批题量降到 {batch_cap} 道重试')
                continue
            errors.append('这一批没有返回可用题目')
            break

        batches += 1
        added_this_batch = 0
        for q in items:
            if remaining <= 0:
                break                      # 够了就停，避免模型多给导致 token 浪费
            if not isinstance(q, dict):
                continue
            text = str(q.get('question', '')).strip()
            if len(text) < 6 or text in asked:
                continue
            asked.append(text)
            added_this_batch += 1
            kp = q.get('key_points') or []
            if isinstance(kp, str):
                kp = [{'do': True, 'text': kp}]
            followups = q.get('followups') or []
            if isinstance(followups, str):
                followups = [followups]
            rt = str(q.get('round_type', round_type or 'tech1')).strip()
            if rt not in ROUND_LABEL:
                rt = 'tech1'
            conn.execute(
                'INSERT INTO question (job_id, resume_id, question, standard, probe, '
                'key_points, followups, round_type, is_risk, risk_note, status, created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (job_id, resume_id, text,
                 str(q.get('standard', '')).strip(),
                 str(q.get('probe', '')).strip(),
                 json.dumps(kp, ensure_ascii=False),
                 json.dumps(followups, ensure_ascii=False),
                 rt, 1 if q.get('is_risk') else 0,
                 str(q.get('risk_note', '')).strip(),
                 db.Q_UNSEEN, now))
            added += 1
            remaining -= 1

        # ★ 这一批一道新题都没产出就停。
        # 不检查的话，模型反复返回重复题目会让这个循环永不退出（实测踩到过）。
        if added_this_batch == 0:
            errors.append(f'第 {batches} 批没有产出新题（可能一直返回重复内容），已停止')
            break

        # 这批撞了输出上限（只抢救回一部分）→ 下一批少要几道，别每次都浪费半批
        if truncated and batch_cap > MIN_BATCH_SIZE:
            batch_cap = max(MIN_BATCH_SIZE, min(batch_cap, take) // 2)
            errors.append(f'上一批输出被截断，已把每批题量降到 {batch_cap} 道')

    return {
        'total': added,
        'batches': batches,
        'job_id': job_id,
        'resume_id': resume_id,
        'warnings': errors,
    }


def _decode_question(row) -> dict:
    item = dict(row)
    for field, fallback in (('key_points', []), ('followups', [])):
        try:
            item[field] = json.loads(row[field] or '[]')
        except (json.JSONDecodeError, TypeError):
            item[field] = fallback
    item['round_label'] = ROUND_LABEL.get(row['round_type'], row['round_type'])
    return item


def list_questions(conn: sqlite3.Connection, job_id: int | None = None,
                   status: str = '', round_type: str = '') -> list[dict]:
    sql = ('SELECT q.*, j.company, j.title FROM question q '
           'LEFT JOIN job j ON j.id = q.job_id WHERE 1=1')
    args: list = []
    if job_id:
        sql += ' AND q.job_id = ?'
        args.append(job_id)
    if status:
        sql += ' AND q.status = ?'
        args.append(status)
    if round_type:
        sql += ' AND q.round_type = ?'
        args.append(round_type)
    sql += ' ORDER BY q.is_risk DESC, q.id'
    return [_decode_question(r) for r in conn.execute(sql, args).fetchall()]


def get_question(conn: sqlite3.Connection, question_id: int) -> dict | None:
    row = conn.execute('SELECT * FROM question WHERE id = ?', (question_id,)).fetchone()
    return _decode_question(row) if row else None


def set_status(conn: sqlite3.Connection, question_id: int, status: str) -> dict | None:
    if status not in (db.Q_UNSEEN, db.Q_SEEN, db.Q_MASTERED, db.Q_REVIEW):
        raise ValueError('题目状态不合法')
    conn.execute('UPDATE question SET status = ? WHERE id = ?', (status, question_id))
    return get_question(conn, question_id)


def coverage(conn: sqlite3.Connection, job_id: int) -> dict:
    """某岗位的题库覆盖情况。"""
    rows = conn.execute(
        'SELECT status, COUNT(*) n FROM question WHERE job_id = ? GROUP BY status',
        (job_id,)).fetchall()
    by_status = {r['status']: r['n'] for r in rows}
    total = sum(by_status.values())
    risk = conn.execute(
        'SELECT COUNT(*) FROM question WHERE job_id = ? AND is_risk = 1', (job_id,)).fetchone()[0]
    return {
        'total': total,
        'by_status': by_status,
        'mastered': by_status.get(db.Q_MASTERED, 0),
        'review': by_status.get(db.Q_REVIEW, 0),
        'risk': risk,
    }
