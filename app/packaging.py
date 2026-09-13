"""
简历包装：读材料 → 对照 JD → 产出逐条修改建议。

## 这个模块的核心约束

**每条建议必须带「依据」。拿不到依据就走"提示补充"分支，不许硬生成。**

这不是写在提示词里祈告模型遵守，而是在代码里强制：
- 提示词要求模型对每条建议给出 evidence（来自哪条 JD / 哪条已确认事实）
- 返回后**代码校验**：`grade=ok` 但 evidence 为空的，一律降级为 `need`
- `grade=risk` 的建议不提供采纳入口

这样"不编造"就成了系统行为，而不是模型的自律。
"""

from __future__ import annotations

import json
import sqlite3

from . import clients, db

GRADE_LABEL = {
    db.GRADE_OK: '可直接采纳',
    db.GRADE_NEED: '需要补充',
    db.GRADE_RISK: '不建议写',
}

SYSTEM_PROMPT = """你是一位资深的简历顾问，服务对象是一位正在求职的工程师。

你的工作方式只有一种：**读材料 → 找到证据 → 给出更好的表达**。

## 三条铁律

1. **绝不编造事实。** 你可以重排、改写、突出，但每一个数字、每一项技术、
   每一个成果都必须能在「事实库」或「当前简历」里找到原文。
   找不到依据的内容，宁可不说。

2. **每条建议必须给出依据。** evidence 数组里写清楚：这条建议引用了哪条岗位要求、
   哪条事实。依据是你判断的基础，也是用户信任你的前提。

3. **分清三种情况：**
   - `ok`：事实齐全，只是表达方式可以更好 → 给改后的文本
   - `need`：方向对但**缺数据或细节**（比如写了"优化了性能"却没有数字）→
     在 missing 字段里明确说缺什么，after 留空或给一个不带数字的替代表述
   - `risk`：这句话在事实库里没有依据，写了有风险 → 建议删掉，说明风险

## 关于「包装」的边界

允许：把"充值和退款"展开成 JD 用的原词「支付、计费、订阅、结算、风控」；
把埋在长句里的数字提到显眼位置；把技术决策的"为什么"补上半句。

禁止：把"参与"改成"主导"；把没有的数字填上；把没做过的技术写进技能栏。

## 输出格式

只返回 JSON，不要解释：

{
  "overall": "一句话总评：这份简历与这个岗位的整体匹配情况",
  "suggestions": [
    {
      "location": "简历里的位置，如：项目经历 · Video Note · 第 3 条",
      "before": "原文（照抄，不要改写）",
      "after": "建议改成的内容；grade=need 时留空",
      "reason": "为什么这么改（一两句，说清对岗位要求的响应）",
      "grade": "ok | need | risk",
      "evidence": ["岗位要求第 4 条：支付、计费、订阅", "事实库：Video Note 项目卡 - 支付宝 RSA2 验签"],
      "missing": "grade=need 时说明缺什么；其他情况留空"
    }
  ]
}

建议数量控制在 5-15 条，优先改对岗位匹配度影响最大的条目。
如果这份简历与岗位已经很匹配，suggestions 可以是空数组。"""


def build_fact_base(conn: sqlite3.Connection, limit_points: int = 60,
                    limit_fields: int = 80) -> str:
    """
    构造「事实库」文本：只放**已确认**的内容。

    未确认的草稿不进事实库——这是关键。否则 AI 会拿用户还没认可的内容去改简历。
    """
    lines: list[str] = []

    # 注意排除「材料抽取」那一段：它是材料事实的副本，
    # 留在下面按"材料 + 出处"给更清楚，也不会同一件事说两遍。
    fields = conn.execute(
        "SELECT section, key, value FROM profile_field "
        "WHERE status IN ('confirmed','corrected') AND section != '材料抽取' "
        "ORDER BY section, sort_order LIMIT ?",
        (limit_fields,)).fetchall()
    if fields:
        lines.append('## 已确认的基础信息')
        for f in fields:
            lines.append(f'- [{f["section"]}] {f["key"]}：{f["value"]}')

    projects = conn.execute(
        "SELECT id, name, role, summary FROM project WHERE status IN ('confirmed','corrected')"
    ).fetchall()
    if projects:
        lines.append('\n## 已确认的项目要点')
        for p in projects:
            head = f'### {p["name"]}'
            if p['role']:
                head += f'（{p["role"]}）'
            lines.append(head)
            if p['summary']:
                lines.append(f'{p["summary"]}')
            pts = conn.execute(
                "SELECT text, metric FROM project_point WHERE project_id=? "
                "AND status IN ('confirmed','corrected') ORDER BY sort_order LIMIT 20",
                (p['id'],)).fetchall()
            for pt in pts:
                metric = f'（数字：{pt["metric"]}）' if pt['metric'] else ''
                lines.append(f'- {pt["text"]}{metric}')

    # 未确认的项目要点也给出来，但明确标注——让 AI 知道存在但需谨慎
    pending = conn.execute(
        "SELECT p.name, pt.text FROM project_point pt JOIN project p ON p.id=pt.project_id "
        "WHERE pt.status='unconfirmed' ORDER BY p.name, pt.sort_order LIMIT ?",
        (limit_points,)).fetchall()
    if pending:
        lines.append('\n## 项目要点（尚未确认，仅供你判断方向，不要当作已证实的数字使用）')
        for r in pending:
            lines.append(f'- [{r["name"]}] {r["text"]}')

    # 材料事实是事实库的主体。**已核对的材料**直接可用；
    # 没核对过的也带上，但标明出处和"未经核对"，让 AI 自己决定要不要先问用户。
    facts = conn.execute(
        "SELECT m.filename, m.doc_reviewed, f.text, f.locator FROM material_fact f "
        "JOIN material m ON m.id = f.material_id "
        "WHERE f.status != 'obsolete' ORDER BY m.id DESC, f.id LIMIT 160").fetchall()
    if facts:
        ok_rows = [f for f in facts if f['doc_reviewed']]
        raw_rows = [f for f in facts if not f['doc_reviewed']]
        if ok_rows:
            lines.append('\n## 材料事实（用户已核对过这份材料，可以直接用）')
            for f in ok_rows:
                loc = f'（{f["filename"]}{" " + f["locator"] if f["locator"] else ""}）'
                lines.append(f'- {f["text"]} {loc}')
        if raw_rows:
            lines.append('\n## 材料事实（**用户还没核对过**——用到这些数字前先跟他确认）')
            for f in raw_rows[:60]:
                loc = f'（{f["filename"]}{" " + f["locator"] if f["locator"] else ""}）'
                lines.append(f'- {f["text"]} {loc}')

    return '\n'.join(lines) if lines else '（事实库为空——请先确认档案内容）'


def build_job_context(conn: sqlite3.Connection, job_id: int) -> str:
    """构造岗位上下文：JD 原文 + 四层拆解 + 已有的匹配分析。"""
    job = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
    if job is None:
        raise ValueError('岗位不存在')

    parts = [f'## 目标岗位：{job["company"]} · {job["title"]}']
    if job['salary'] or job['city']:
        parts.append(f'薪资：{job["salary"]}　城市：{job["city"]}')
    if job['raw_text']:
        parts.append(f'\n### 岗位要求原文\n{job["raw_text"][:6000]}')

    reqs = conn.execute(
        'SELECT * FROM job_requirement WHERE job_id = ? ORDER BY sort_order', (job_id,)).fetchall()
    if reqs:
        parts.append('\n### 已拆解的要求（含匹配情况）')
        layer_name = {'gate': '硬门槛', 'duty': '核心职责', 'plus': '加分项', 'hidden': '隐性偏好'}
        for r in reqs:
            strength = {'strong': '强', 'medium': '中', 'weak': '弱', 'none': '无'}.get(r['strength'], '未评')
            parts.append(f'- [{layer_name.get(r["layer"], r["layer"])}｜匹配：{strength}] {r["text"]}')
    return '\n'.join(parts)


def generate_suggestions(conn: sqlite3.Connection, job_id: int, resume_id: int) -> dict:
    """生成简历修改建议并入库。"""
    resume = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
    if resume is None:
        raise ValueError('简历不存在')

    job_ctx = build_job_context(conn, job_id)
    fact_base = build_fact_base(conn)
    resume_text = resume['content'] or '（简历内容为空）'

    user_prompt = f"""{job_ctx}

---

# 事实库（你只能从这里取材）

{fact_base}

---

# 当前简历

{resume_text}

---

请对照岗位要求，逐条给出这份简历的修改建议。记住：每条建议都要有依据，
没有依据的内容不要写。"""

    parsed = clients.chat_json(
        [{'role': 'system', 'content': SYSTEM_PROMPT},
         {'role': 'user', 'content': user_prompt}],
        temperature=0.2)

    if not isinstance(parsed, dict):
        raise clients.ServiceError('建议生成返回结构异常')

    suggestions = parsed.get('suggestions') or []
    now = db.now_iso()

    conn.execute('DELETE FROM resume_suggestion WHERE resume_id = ? AND job_id = ?',
                 (resume_id, job_id))

    kept = 0
    downgraded = 0
    for i, s in enumerate(suggestions):
        if not isinstance(s, dict):
            continue
        before = str(s.get('before', '')).strip()
        after = str(s.get('after', '')).strip()
        reason = str(s.get('reason', '')).strip()
        grade = str(s.get('grade', db.GRADE_OK)).strip()
        if grade not in db.VALID_GRADE:
            grade = db.GRADE_OK
        evidence = s.get('evidence') or []
        if isinstance(evidence, str):
            evidence = [evidence]
        evidence = [str(e).strip() for e in evidence if str(e).strip()]
        missing = str(s.get('missing', '')).strip()

        if not before and not after:
            continue

        # ── 强制校验：没有依据就不能标「可直接采纳」──
        if grade == db.GRADE_OK and not evidence:
            grade = db.GRADE_NEED
            downgraded += 1
            if not missing:
                missing = '这条建议没有找到明确依据。请确认相关事实，或补充材料。'

        # risk 等级不提供改后文本，避免用户误采纳
        if grade == db.GRADE_RISK:
            after = ''

        conn.execute(
            'INSERT INTO resume_suggestion (resume_id, job_id, location, before_text, '
            'after_text, reason, evidence, grade, missing, decision, created_at, sort_order) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (resume_id, job_id, str(s.get('location', '')).strip(), before, after,
             reason, json.dumps(evidence, ensure_ascii=False), grade, missing,
             db.SUGGESTION_PENDING, now, i))
        kept += 1

    graded = {db.GRADE_OK: 0, db.GRADE_NEED: 0, db.GRADE_RISK: 0}
    for r in conn.execute(
            'SELECT grade, COUNT(*) n FROM resume_suggestion WHERE resume_id=? AND job_id=? '
            'GROUP BY grade', (resume_id, job_id)).fetchall():
        graded[r['grade']] = r['n']

    return {
        'overall': str(parsed.get('overall', '')).strip(),
        'total': kept,
        'ok': graded.get(db.GRADE_OK, 0),
        'need': graded.get(db.GRADE_NEED, 0),
        'risk': graded.get(db.GRADE_RISK, 0),
        'downgraded': downgraded,
    }


def list_suggestions(conn: sqlite3.Connection, resume_id: int,
                     job_id: int | None = None) -> list[dict]:
    sql = 'SELECT * FROM resume_suggestion WHERE resume_id = ?'
    args: list = [resume_id]
    if job_id:
        sql += ' AND job_id = ?'
        args.append(job_id)
    sql += ' ORDER BY grade, sort_order'
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item['evidence'] = json.loads(r['evidence'] or '[]')
        except json.JSONDecodeError:
            item['evidence'] = []
        item['grade_label'] = GRADE_LABEL.get(r['grade'], r['grade'])
        out.append(item)
    return out


def decide_suggestion(conn: sqlite3.Connection, suggestion_id: int,
                      decision: str, edited_text: str = '') -> dict:
    """用户处理一条建议：采纳 / 改一下 / 不要。"""
    if decision not in (db.SUGGESTION_ACCEPTED, db.SUGGESTION_REJECTED,
                        db.SUGGESTION_EDITED):
        raise ValueError('decision 取值不合法')
    row = conn.execute('SELECT * FROM resume_suggestion WHERE id = ?',
                       (suggestion_id,)).fetchone()
    if row is None:
        raise ValueError('建议不存在')

    after = row['after_text']
    if decision == db.SUGGESTION_EDITED:
        after = edited_text.strip()
        if not after:
            raise ValueError('改成什么不能为空')

    conn.execute('UPDATE resume_suggestion SET decision=?, after_text=? WHERE id=?',
                 (decision, after, suggestion_id))
    db.log_change(conn, 'resume_suggestion', suggestion_id, decision,
                  row['decision'], decision)
    return dict(conn.execute('SELECT * FROM resume_suggestion WHERE id = ?',
                             (suggestion_id,)).fetchone())


def apply_accepted(conn: sqlite3.Connection, resume_id: int) -> dict:
    """
    把已采纳的建议应用到简历正文。

    做法：在正文末尾追加一节「已采纳的修改」，逐条列出 before → after。
    不直接替换正文——因为简历是 markdown 自由文本，做精确替换容易误伤。
    用户可以在编辑页手工合并，或直接用这一节作为改稿指引。
    """
    rows = conn.execute(
        "SELECT * FROM resume_suggestion WHERE resume_id = ? "
        "AND decision IN ('accepted','edited') ORDER BY sort_order", (resume_id,)).fetchall()
    if not rows:
        return {'applied': 0}

    lines = ['\n\n---\n\n## 已采纳的修改（AI 建议 · 你已确认）\n']
    for i, r in enumerate(rows, 1):
        lines.append(f'**{i}. {r["location"]}**\n')
        lines.append(f'- 原文：{r["before_text"]}')
        lines.append(f'- 改为：{r["after_text"]}\n')

    resume = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
    content = (resume['content'] or '').split('## 已采纳的修改')[0].rstrip()
    content += '\n'.join(lines)

    conn.execute('UPDATE resume SET content=?, status=?, updated_at=? WHERE id=?',
                 (content, 'draft', db.now_iso(), resume_id))
    return {'applied': len(rows)}
