"""
岗位分析：把一段 JD 原文拆成四层，并逐条对照候选人的证据算匹配度。

四层拆解的意义在于**不同层的处理方式完全不同**：
    硬门槛  —— 不满足就是 0 或 1，无法靠表述弥补
    核心职责 —— 逐条找项目证据，这是匹配度的主体
    加分项  —— 用来排序和加分，不是门槛
    隐性偏好 —— 从措辞读出的真实需求，最容易被忽略但最影响胜率
"""

from __future__ import annotations

import json
import sqlite3

from . import clients, db
from .packaging import build_fact_base

LAYER_LABEL = {'gate': '硬门槛', 'duty': '核心职责', 'plus': '加分项', 'hidden': '隐性偏好'}
WEIGHT_LABEL = {'high': '高', 'mid': '中', 'low': '低'}
STRENGTH_LABEL = {'strong': '强', 'medium': '中', 'weak': '弱', 'none': '无'}

SYSTEM_PROMPT = """你是一位资深的求职顾问，正在帮候选人判断一个岗位值不值得投。

## 第一步：把 JD 拆成四层

- **硬门槛**：学历、年限、专业、必须有的大厂/领域背景。不满足就是 0 或 1。
- **核心职责**：入职后每天要做的事。逐条找证据，这是匹配度的主体。
- **加分项**：「有…者优先」这类。用来排序，不是门槛。
- **隐性偏好**：从措辞读出的真实需求。这是最容易被忽略的一层，举例：
  - 通篇强调"从 0 到 1""独立负责" → 团队小、要能自己扛
  - 反复提"跨部门协作""推动落地" → 难点在组织而非技术
  - 要求"熟悉 XX 业务" → 业务壁垒比技术更重要
  - 技术名词堆得很杂 → 可能是小团队全栈岗，或 JD 是抄的
  - 强调"稳定""长期" → 可能之前有人快速离职

## 第二步：逐条匹配，标注证据与强度

强度定义（**严格按此执行，不要放水**）：
- `strong`：有可展示的产出（代码、上线产品、文档），能讲三层细节
- `medium`：做过类似的事，但不是完全对应的技术栈或场景
- `weak`：了解概念、学习过、写过 demo，但没有真实项目
- `none`：没有

**"weak" 和 "none" 不能算匹配。** 简历上写"了解"的东西，在这里就是 weak。

## 第三步：给结论

- `score`：总体匹配度 0-100。评分口径：
  85+ 强匹配 / 70-84 可投但需补强 / 55-69 可以投但要有被拒的准备 / <55 不建议投
- `verdict`：明确结论 + 理由。不要模棱两可。

**不要为了给候选人信心而抬高分数。** 高分低能会让他把时间浪费在不该投的岗位上。

## 输出格式

只返回 JSON：

{
  "gate":   [{"text": "...", "weight": "high|mid|low", "evidence": "...", "strength": "...", "strategy": "..."}],
  "duty":   [...],
  "plus":   [...],
  "hidden": [{"text": "你从 JD 读出的隐性需求", "weight": "high|mid|low", "evidence": "你的推断依据", "strength": "medium", "strategy": "应对建议"}],
  "score": 0,
  "verdict": "明确结论 + 理由"
}"""


def analyze(conn: sqlite3.Connection, job_id: int,
            resume_id: int | None = None) -> dict:
    """
    分析一个岗位并存档。

    ★ 匹配度必须**对着简历**算，不能只对着档案事实库算。
    之前只用 build_fact_base（档案 + 项目要点），简历里改过、新写的表述它完全看不见——
    用户新建/更新了简历，分析结果却一点不变，看着就像"没用我的简历"。

    现在：档案事实库 + **指定简历（默认最近更新的那份）** 一起喂进去，
    并让模型指出两者之间的矛盾（简历里写了、但档案里没依据的地方）。
    """
    job = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
    if job is None:
        raise ValueError('岗位不存在')

    # 用哪份简历：调用方指定 > 最近更新的那份
    if resume_id:
        resume = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
    else:
        resume = conn.execute(
            'SELECT * FROM resume ORDER BY updated_at DESC, id DESC LIMIT 1').fetchone()

    raw = (job['raw_text'] or '').strip()
    if not raw and job['material_id']:
        row = conn.execute('SELECT raw_text, summary FROM material WHERE id = ?',
                           (job['material_id'],)).fetchone()
        if row:
            raw = (row['raw_text'] or row['summary'] or '').strip()
    if not raw:
        raise ValueError('这个岗位没有 JD 原文——请粘贴文本或上传截图并先解析材料')

    fact_base = build_fact_base(conn)
    header = f'岗位：{job["company"]} · {job["title"]}'
    if job['salary'] or job['city']:
        header += f'（{job["city"]}　{job["salary"]}）'

    resume_block = ''
    if resume:
        resume_block = (
            f'\n---\n\n# 候选人这次的简历（「{resume["name"]}」，更新于 {resume["updated_at"]}）\n\n'
            f'**匹配度要对着这份简历算**——他投出去的就是这一份。\n\n'
            f'{(resume["content"] or "")[:12000]}\n'
        )

    prompt = f'''{header}

# 岗位要求原文

{raw[:8000]}

---

# 候选人的事实库（已确认的档案与项目，用于核对简历里的话有没有依据）

{fact_base}
{resume_block}
---

请完成四层拆解与匹配度分析。

注意：
- 「我的证据」优先引用**简历里的原话**，其次是事实库
- 如果简历里写了某项能力，但事实库里找不到依据，标记为需要小心（面试会被追问）
'''

    parsed = clients.chat_json(
        [{'role': 'system', 'content': SYSTEM_PROMPT},
         {'role': 'user', 'content': prompt}],
        temperature=0.2)

    if not isinstance(parsed, dict):
        raise clients.ServiceError('岗位分析返回结构异常')

    conn.execute('DELETE FROM job_requirement WHERE job_id = ?', (job_id,))
    order = 0
    counts = {}
    for layer in ('gate', 'duty', 'plus', 'hidden'):
        items = parsed.get(layer) or []
        counts[layer] = len(items)
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get('text', '')).strip()
            if not text:
                continue
            weight = str(item.get('weight', 'mid')).strip()
            if weight not in WEIGHT_LABEL:
                weight = 'mid'
            strength = str(item.get('strength', 'none')).strip()
            if strength not in STRENGTH_LABEL:
                strength = 'none'
            conn.execute(
                'INSERT INTO job_requirement (job_id, text, layer, weight, evidence, '
                'strength, strategy, sort_order) VALUES (?,?,?,?,?,?,?,?)',
                (job_id, text, layer, weight, str(item.get('evidence', '')).strip(),
                 strength, str(item.get('strategy', '')).strip(), order))
            order += 1

    try:
        score = int(parsed.get('score', -1))
    except (TypeError, ValueError):
        score = -1
    score = max(-1, min(100, score))
    verdict = str(parsed.get('verdict', '')).strip()

    conn.execute(
        'UPDATE job SET score=?, verdict=?, layer_gate=?, layer_duty=?, layer_plus=?, '
        'layer_hidden=?, analyzed_resume_id=?, updated_at=? WHERE id=?',
        (score, verdict,
         json.dumps([i.get('text') for i in (parsed.get('gate') or []) if isinstance(i, dict)],
                    ensure_ascii=False),
         json.dumps([i.get('text') for i in (parsed.get('duty') or []) if isinstance(i, dict)],
                    ensure_ascii=False),
         json.dumps([i.get('text') for i in (parsed.get('plus') or []) if isinstance(i, dict)],
                    ensure_ascii=False),
         json.dumps([i.get('text') for i in (parsed.get('hidden') or []) if isinstance(i, dict)],
                    ensure_ascii=False),
         resume['id'] if resume else None,
         db.now_iso(), job_id))

    return {'score': score, 'verdict': verdict, 'counts': counts, 'total': order,
            'resume_id': resume['id'] if resume else None,
            'resume_name': resume['name'] if resume else '',
            'resume_chars': len(resume['content'] or '') if resume else 0}
