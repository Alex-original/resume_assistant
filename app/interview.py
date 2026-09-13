"""
模拟面试：语音对话式的真实面试演练。

## 与练习模式的本质区别

    练习模式：每题自己选是否看答案 → 学
    模拟面试：全程不给答案，追问到你答不出 → 考

## 面试官的六条硬规则（写死在代码里，不靠模型自觉）

    1. 一次只问一个问题
    2. 面试中不给答案、不点评
    3. 必须追问，每个核心问题追 1-2 层
    4. 不放过含糊表述
    5. 不替用户回答
    6. 可以尖锐但不羞辱

## 状态机

    running ──(答完所有题 / 用户结束)──> finished ──(生成评分)──> 可复盘
"""

from __future__ import annotations

import json
import logging
import sqlite3

from . import clients, db
from .packaging import build_fact_base, build_job_context
from .questions import ROUND_LABEL

logger = logging.getLogger(__name__)

PRESSURE_LABEL = {'normal': '常规', 'strict': '偏严', 'stress': '压力面'}

FOLLOWUP_SYSTEM = """你是一位正在**打电话**做技术面试的面试官。对面是候选人，你们在实时对话。

## 最重要的一件事：你不是在念题库

下面会给你一份「可问的话题清单」——那是根据他的简历和岗位准备的**弹药库**，
**不是必须逐条念完的流程表**。

真实的面试官是这样工作的：

- 顺着候选人**刚说的话**往下问。他提到什么有意思、可疑、或没讲清的，就顺着挖
- 他答得含糊 → 追；答得清楚 → 换个方向
- 话题之间**自然过渡**：「好，那我们聊聊你那个量化项目」「换个话题，我想了解下…」
- 把清单里的问题**用你自己的话问出来**，不要照念——照念就像在考试。
  但问题里的关键信息（具体数字、具体技术）要保留
- 聊透了就往下走，不要为了用完清单而硬问

## 判断顺序（按这个来）

1. 他刚说的这句话里，有没有**值得挖的点**？（有 → 追问）
2. 当前这个话题**聊透了吗**？（没透 且 追问次数 < 2 → 继续追）
3. 聊透了 → 从话题清单里挑一个**标着"已聊过"以外**的编号，用口语问出来
4. 主要话题都覆盖了，或者已经聊得够久（≥ 8 个话题）→ 结束面试

## 绝对不许重复问

「已经问过的话题」那一节列了前面问过的每一件事。**同一件事不能再问一遍——
换个说法也不行。** 典型错误：

- 前面问过「90% 是怎么算出来的」，后面又问「复用率 90% 的口径是什么」→ 同一件事
- 前面问过「7000 行代码怎么转的」，后面又问「那个 Android 转换具体怎么做」→ 同一件事

判断标准是**这件事的实质**，不是字面。如果新问题只是在换措辞重问，
就换一个编号；实在没有新东西可问，就结束面试，别硬凑。

## 说话方式

你在打电话，不是在写邮件：

- 可以有简短的接话：「嗯」「好」「我明白」「这个有意思」
- 一次只问**一个**问题，问句要短、要像说话
- **不点评对错、不给答案、不夸人**（最多一句「好」），更不要说「答得不错」
- 语气直接、专业、不带情绪；可以尖锐，但不羞辱

## 输出格式

只返回 JSON：

{
  "ack": "对他刚说的那句的短回应，10 字以内，如「嗯」「好，我明白」。不要点评对错。",
  "action": "followup | next | finish",
  "text": "你要问的下一个问题（action=followup 或 next 时必填）。口语、一句话。",
  "topic": "这一题在聊什么，8 字以内（内部记录）",
  "reason": "你判断的理由（内部记录，不展示给候选人）"
}

- action=followup：继续挖**当前这个话题**
- action=next：换一个**新话题**（从清单里挑没聊过的，或顺着他的话自然带出的新方向）
- action=finish：主要话题都聊完了
"""

REVIEW_SYSTEM = """你是一位刚结束面试的资深面试官，正在写评估报告。

请对这次面试给出严格、具体、可执行的评价。

## 评分纪律

**默认 6 分是"能进面试但会被刷"的水平，8 分以上才叫好。**
不要因为候选人努力了就抬高分数——虚高的分数会让他在真实面试中被打懵。

## 五个维度（各 10 分）

- 技术深度：能否讲到实现细节与取舍
- 表达结构：有没有先给结论、逻辑是否清晰
- 诚信一致：简历与回答是否自洽（这一项可以一票否决）
- 取舍判断：能否说清为什么这么选、代价是什么
- 沟通互动：是否好共事

## 输出格式

只返回 JSON：

{
  "scores": {"tech": 0, "structure": 0, "honesty": 0, "tradeoff": 0, "communication": 0},
  "summary": "一句话总评（本次面试最核心的问题）",
  "highlights": [
    {"turn": 3, "quote": "他原话里的关键句", "why": "为什么这是亮点"}
  ],
  "dangers": [
    {"turn": 5, "problem": "问题所在", "better": "改进后的答法，可直接背"}
  ],
  "advice": ["下周三件改进点，每条要具体可执行"],
  "turn_reviews": [
    {
      "seq": 1,
      "wanted": "这道题面试官真正想听什么（一两句）",
      "gap": "他的回答差在哪（没答到 / 没数字 / 没过程 / 自相矛盾）",
      "model_answer": "标准回答案例：用**他自己简历和材料里的真实信息**组织一段可以直接背的答案，150-300 字。要有结论、有过程、有数字、有取舍。**不许编造他没有的经历或数字**；资料里缺的部分就写「（这里需要你补一个真实数字）」"
    }
  ]
}

- highlights 和 dangers 各给 3 条
- **turn_reviews 要给满每一道题**（面试记录里问了几个话题就给几条），这是用户复盘时最需要的东西
- model_answer 是"他应该怎么答"，不是泛泛而谈——要具体到他自己的项目细节
"""


def pick_questions(conn: sqlite3.Connection, job_id: int,
                   resume_id: int | None) -> list:
    """
    取这个岗位可用的题。

    ★ 为什么要"或 resume_id 为空"：题库是按 (岗位, 简历) 生成的，
    但简历是可以被删的——删了之后 `ON DELETE SET NULL` 会把题的 resume_id 置空。
    如果这里只按 resume_id 严格匹配，那些题就变成孤儿：明明有 12 道题，
    却提示"这个岗位还没有题库"。所以简历对不上时退一步用岗位级的题。
    """
    return conn.execute(
        'SELECT * FROM question WHERE job_id = ? AND (resume_id = ? OR resume_id IS NULL) '
        'ORDER BY is_risk DESC, id', (job_id, resume_id)).fetchall()



SIM_THRESHOLD = 0.30       # 判重阈值：见下面 _match_topic 的说明
MIN_TOPICS = 5             # 至少聊过这么多个话题，才允许面试官收尾
MAX_TOPICS = 15            # 聊到这个数就强制结束，别没完没了


def _bigrams(text: str) -> set:
    """中文按字符 2-gram 切，用来粗略判断两句话是不是在说同一件事。"""
    t = ''.join(ch for ch in (text or '') if ch.isalnum())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _similar(a: str, b: str) -> float:
    """两句话的重合度（0~1）。中文里 2-gram 的 Jaccard 足够好用了。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _match_topic(bank, text: str, threshold: float = SIM_THRESHOLD) -> int | None:
    """
    把一句"模型自己组织过的话"对回题库编号。

    为什么要这一步：判重若只认模型返回的编号，模型有一半时候不返回；
    若只认文字，模型又每次换个说法。两条路都堵，所以补一条"像不像"的兜底。
    """
    best, best_score = None, 0.0
    for i, q in enumerate(bank):
        score = _similar(q['question'], text)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= threshold else None


def start(conn: sqlite3.Connection, job_id: int, resume_id: int,
          round_type: str = 'tech1', pressure: str = 'normal') -> dict:
    """
    开始一场模拟面试。

    开场白和第一个问题由模型生成（不是直接念题库第一题）——
    真人面试官不会"第一题：……"这么开口，他会先打个招呼再切入。
    """
    questions = pick_questions(conn, job_id, resume_id)
    if not questions:
        raise ValueError('这个岗位还没有题库。到「岗位」详情页点「生成题库」，'
                         '或在面试设置页直接生成。')

    job = conn.execute('SELECT * FROM job WHERE id = ?', (job_id,)).fetchone()
    resume = conn.execute('SELECT * FROM resume WHERE id = ?', (resume_id,)).fetchone()
    now = db.now_iso()

    opening = _opening(conn, job, resume, questions, round_type, pressure)

    cur = conn.execute(
        'INSERT INTO interview (job_id, resume_id, round_type, pressure, mode, status, '
        'started_at) VALUES (?,?,?,?,?,?,?)',
        (job_id, resume_id, round_type, pressure, db.MODE_MOCK, 'running', now))
    interview_id = int(cur.lastrowid)

    conn.execute(
        'INSERT INTO interview_turn (interview_id, seq, question, probe, topic_index, '
        'created_at) VALUES (?,?,?,?,?,?)',
        (interview_id, 1, opening['question'], '', opening.get('topic_index'), now))

    return {
        'interview_id': interview_id,
        'job': f'{job["company"]} · {job["title"]}' if job else '',
        'round_label': ROUND_LABEL.get(round_type, round_type),
        'pressure_label': PRESSURE_LABEL.get(pressure, pressure),
        'total_questions': len(questions),
        'turn': 1,
        'topic_count': 1,
        'min_topics': MIN_TOPICS,
        'greeting': opening['greeting'],
        'question': opening['question'],
    }


OPENING_SYSTEM = """你是一位正在打电话做技术面试的面试官，刚接通。

请用一两句自然的话开场，然后问出第一个问题。

要求：
- 开场白像真人：报一下自己是谁（"我是这次的技术面试官"），然后自然切入，
  不要念"第一题：……"
- 第一个问题**从话题清单里挑一个最该先问的**，并且**用你自己的话问出来**
- 不要点评简历、不要夸人、不要客套太长
- 一次只问一个问题

只返回 JSON：
{"greeting": "开场白，40 字以内", "question": "第一个问题，一句话，口语",
 "topic_index": 你问的是清单里的第几条（数字）}
"""


def _opening(conn, job, resume, questions, round_type: str, pressure: str) -> dict:
    """开场白 + 第一问。模型失败就退回题库第一题，不能让面试开不起来。"""
    fact_base = build_fact_base(conn, limit_points=15, limit_fields=15)
    topics = '\n'.join(f'[{i}] {q["question"]}' for i, q in enumerate(questions[:12]))
    prompt = (
        f'{build_job_context(conn, job["id"]) if job else ""}\n\n'
        f'# 候选人简历\n\n{(resume["content"] or "")[:4000] if resume else ""}\n\n'
        f'# 简历事实库\n\n{fact_base[:2000]}\n\n'
        f'# 话题清单（带编号；只是弹药库，不是流程表）\n\n{topics}\n\n'
        f'轮次：{ROUND_LABEL.get(round_type, round_type)}，'
        f'压力等级：{PRESSURE_LABEL.get(pressure, pressure)}'
    )
    try:
        out = clients.chat_json(
            [{'role': 'system', 'content': OPENING_SYSTEM},
             {'role': 'user', 'content': prompt}],
            temperature=0.7, max_tokens=500)
        if isinstance(out, dict) and str(out.get('question') or '').strip():
            idx = out.get('topic_index')
            try:
                idx = int(idx)
                if not (0 <= idx < len(questions)):
                    idx = None
            except (TypeError, ValueError):
                idx = None
            return {'greeting': str(out.get('greeting') or '').strip(),
                    'question': str(out['question']).strip(), 'topic_index': idx}
    except clients.ServiceError as exc:
        logger.warning('生成开场白失败，退回题库第一题：%s', exc)
    return {'greeting': '', 'question': questions[0]['question'], 'topic_index': 0}


def _current_turn(conn: sqlite3.Connection, interview_id: int):
    return conn.execute(
        'SELECT * FROM interview_turn WHERE interview_id = ? ORDER BY seq DESC LIMIT 1',
        (interview_id,)).fetchone()


def _next_question_row(conn: sqlite3.Connection, interview_id: int):
    """取出本题之后的下一个主问题。"""
    used = conn.execute(
        'SELECT question FROM interview_turn WHERE interview_id = ?', (interview_id,)).fetchall()
    used_texts = {r['question'] for r in used}
    interview = conn.execute('SELECT * FROM interview WHERE id = ?', (interview_id,)).fetchone()
    rows = pick_questions(conn, interview['job_id'], interview['resume_id'])
    for r in rows:
        if r['question'] not in used_texts:
            return r
    return None


def answer(conn: sqlite3.Connection, interview_id: int, text: str,
           input_mode: str = 'voice', is_followup: bool = False) -> dict:
    """
    提交一次回答，返回下一步：追问 / 换话题 / 结束。

    ★ 核心设计：**下一步由面试官（模型）决定，不是从题库里取下一行**。
    题库只是"可问的话题清单"（弹药库）。真人面试会顺着你说的话走，
    而不是照着清单一条条念——这正是之前"像在遍历题库"的原因。

    代码仍然强制两条底线：一次只问一个问题；每个话题最多追 2 层。
    """
    interview = conn.execute('SELECT * FROM interview WHERE id = ?',
                             (interview_id,)).fetchone()
    if interview is None:
        raise ValueError('面试不存在')
    if interview['status'] != 'running':
        raise ValueError('这场面试已经结束了')

    turn = _current_turn(conn, interview_id)
    if turn is None:
        raise ValueError('面试状态异常')

    answer_text = (text or '').strip()
    if not is_followup:
        # 只记主问题的回答；追问的回答由 submit_followup_answer 存进 followups
        conn.execute('UPDATE interview_turn SET answer = ?, input_mode = ? WHERE id = ?',
                     (answer_text, input_mode, turn['id']))

    turn = _current_turn(conn, interview_id) or turn
    followup_count = len(json.loads(turn['followups'] or '[]'))

    ack = ''
    action = 'next'
    follow_text = ''
    topic = ''

    if answer_text:
        fact_base = build_fact_base(conn, limit_points=15, limit_fields=15)
        job_ctx = build_job_context(conn, interview['job_id'])

        # 这一题的完整对话（主问 + 每一层追问）
        chain = [f'问：{turn["question"]}']
        if turn['answer']:
            chain.append(f'答：{turn["answer"]}')
        for i, f in enumerate(json.loads(turn['followups'] or '[]'), 1):
            chain.append(f'  追问 {i}：{f.get("q", "")}')
            if f.get('a'):
                chain.append(f'  答：{f["a"]}')

        # 整场面试到目前为止的脉络：让面试官知道聊过什么，避免绕回去
        history_rows = conn.execute(
            'SELECT seq, question, answer FROM interview_turn WHERE interview_id = ? '
            'ORDER BY seq', (interview_id,)).fetchall()
        history = '\n'.join(
            f'- 第 {r["seq"]} 个话题：{r["question"][:60]}'
            + ('（已聊）' if r['answer'] else '（正在聊）')
            for r in history_rows) or '（刚开场）'

        # 话题清单：题库 + 哪些已经聊过
        # ★ 判重必须按**话题编号**，不能按文字。
        # 之前拿"模型问出来的原话"去和题库原文比，而模型每次都会换个说法，
        # 结果题库里每一条永远显示「未聊」——于是同一道题被反复问。
        # 现在让模型报告它问的是第几条，我们按编号记账。
        bank = pick_questions(conn, interview['job_id'], interview['resume_id'])[:20]
        asked_idx = conn.execute(
            'SELECT seq, question, topic_index FROM interview_turn WHERE interview_id = ? '
            'ORDER BY seq', (interview_id,)).fetchall()
        used_idx = {r['topic_index'] for r in asked_idx if r['topic_index'] is not None}
        # 模型没给编号的那些，按"像不像"补回来——不然它们会被当成没聊过。
        # ★ 一句问过的话只能认领**最像的那一条**，不能把所有超过阈值的都算上：
        # 题库里如果有几道题长得很像（我构造过"第 1/2/3 个问题吗"这种），
        # 一句提问会把它们全部标成已聊 → 剩余话题为空 → 面试刚开场就结束。
        asked_texts = [r['question'] for r in asked_idx]
        for t in asked_texts:
            best, best_score = None, 0.0
            for i, q in enumerate(bank):
                if i in used_idx:
                    continue
                sc = _similar(q['question'], t)
                if sc > best_score:
                    best, best_score = i, sc
            if best is not None and best_score >= SIM_THRESHOLD:
                used_idx.add(best)
        topics = '\n'.join(
            f'[{i}] {"（已聊过）" if i in used_idx else ""}{q["question"]}'
            + (f'  ｜考察点：{q["probe"][:40]}' if q['probe'] else '')
            for i, q in enumerate(bank))
        asked_list = '\n'.join(
            f'- 第 {r["seq"]} 个话题（编号 {r["topic_index"] if r["topic_index"] is not None else "?"}）：'
            f'{r["question"]}'
            for r in asked_idx) or '（这是第一个话题）'

        prompt = f"""{job_ctx}

# 这一题到目前为止的完整对话

{chr(10).join(chain)}

本题已追问次数：{followup_count}（最多 2 次，到了就不要再追）

# 已经问过的话题（**不许再问同一件事，换个说法也不行**）

{asked_list}

# 可问的话题清单（带编号；标注了哪些已经聊过）

{topics}

选新话题时，**从没有「已聊过」标记的编号里挑**，并且把 `topic_index` 填成那个编号。

# 简历事实背景（判断他的回答是否可信）

{fact_base[:2000]}

请判断下一步：追问、换个新话题、还是结束？
"""
        try:
            decision = clients.chat_json(
                [{'role': 'system', 'content': FOLLOWUP_SYSTEM},
                 {'role': 'user', 'content': prompt}],
                temperature=0.75, max_tokens=700)
        except clients.ServiceError as exc:
            logger.warning('面试官决策失败，退回题库下一题：%s', exc)
            decision = {'action': 'next'}

        if isinstance(decision, dict):
            action = str(decision.get('action', 'next')).strip()
            follow_text = str(decision.get('text', '')).strip()
            ack = str(decision.get('ack', '')).strip()[:20]
            topic = str(decision.get('topic', '')).strip()[:20]

        # 代码强制：追满 2 层就不许再追（不管模型怎么说）。
        # 注意要把模型给的"追问话术"丢掉——那句话是奔着追问去的，
        # 直接拿来当新话题会问得很突兀，改从题库里挑一个没聊过的。
        if action == 'followup' and followup_count >= 2:
            action = 'next'
            follow_text = ''
        # 结束面试的闸门：模型说"聊完了"也要过这一关。
        # · 聊过的话题太少（< MIN_TOPICS）且还有没聊过的 → 继续问
        # · 已经聊到 MAX_TOPICS → 强制收尾，别没完没了
        # ★ 这里原来引用了一个已经被改名的变量（used → used_idx），
        # 一走到"模型想结束"就抛 NameError —— 于是自动结束这条路**从来没生效过**，
        # 面试只能靠用户手动点「结束面试」。现在按编号和文字两路一起判。
        n_topics = len({r['seq'] for r in asked_idx})
        if n_topics >= MAX_TOPICS:
            # 到上限就收尾，不管模型还想不想问——否则它一直说 next，面试没完没了
            return finish(conn, interview_id)
        if action == 'finish':
            if n_topics < MIN_TOPICS and _remaining_topics(bank, used_idx):
                action = 'next'                    # 聊得太少，继续
            elif _remaining_topics(bank, used_idx) and n_topics < MIN_TOPICS + 4:
                action = 'next'                    # 再补几个话题

        if action == 'followup' and follow_text:
            fups = json.loads(turn['followups'] or '[]')
            fups.append({'q': follow_text, 'a': ''})
            conn.execute('UPDATE interview_turn SET followups = ? WHERE id = ?',
                         (json.dumps(fups, ensure_ascii=False), turn['id']))
            return {
                'type': 'followup', 'text': follow_text, 'ack': ack,
                'followup_index': len(fups),
                'reason': str(decision.get('reason', '')) if isinstance(decision, dict) else '',
            }

        if action == 'finish':
            return finish(conn, interview_id)

        # 换话题：模型给的话优先；没给就从题库里挑一个还没聊过的
        nxt_text = follow_text
        if not nxt_text:
            nxt = _next_question_row(conn, interview_id)
            if nxt is None:
                return finish(conn, interview_id)
            nxt_text = nxt['question']

        seq = turn['seq'] + 1
        probe = ''
        if nxt_text:
            row = conn.execute('SELECT probe FROM question WHERE job_id = ? AND question = ?',
                               (interview['job_id'], nxt_text)).fetchone()
            probe = row['probe'] if row else ''
        try:
            topic_idx = int(decision.get('topic_index')) if isinstance(decision, dict) \
                and decision.get('topic_index') is not None else None
            if topic_idx is not None and not (0 <= topic_idx < len(bank)):
                topic_idx = None
        except (TypeError, ValueError):
            topic_idx = None
        if topic_idx is None:
            # 模型没给编号：先按文字精确反查，再按"像不像"兜底
            for i, q in enumerate(bank):
                if q['question'] == nxt_text:
                    topic_idx = i
                    break
            else:
                topic_idx = _match_topic(bank, nxt_text)
        conn.execute(
            'INSERT INTO interview_turn (interview_id, seq, question, probe, topic_index, '
            'created_at) VALUES (?,?,?,?,?,?)',
            (interview_id, seq, nxt_text, probe, topic_idx, db.now_iso()))
        return {'type': 'next', 'text': nxt_text, 'turn': seq, 'ack': ack, 'topic': topic,
                'topic_count': seq, 'min_topics': MIN_TOPICS}
    else:
        # 他跳过了这题（没说话/跳过）：直接换下一个话题
        nxt = _next_question_row(conn, interview_id)
        if nxt is None:
            return finish(conn, interview_id)
        seq = turn['seq'] + 1
        conn.execute(
            'INSERT INTO interview_turn (interview_id, seq, question, probe, created_at) '
            'VALUES (?,?,?,?,?)',
            (interview_id, seq, nxt['question'], nxt['probe'], db.now_iso()))
        return {'type': 'next', 'text': nxt['question'], 'turn': seq, 'ack': ''}


def _remaining_topics(bank: list, used_idx: set) -> list:
    """话题清单里还没聊过的（按编号算）。用来防止面试官过早收尾。"""
    return [q for i, q in enumerate(bank) if i not in used_idx]


def submit_followup_answer(conn: sqlite3.Connection, interview_id: int,
                           text: str) -> dict:
    """回答追问——把答案填进当前 turn 的最后一条追问里。"""
    turn = _current_turn(conn, interview_id)
    if turn is None:
        raise ValueError('面试状态异常')
    fups = json.loads(turn['followups'] or '[]')
    if fups:
        fups[-1]['a'] = text.strip()
        conn.execute('UPDATE interview_turn SET followups = ? WHERE id = ?',
                     (json.dumps(fups, ensure_ascii=False), turn['id']))
    return {'ok': True, 'followup_index': len(fups)}


def finish(conn: sqlite3.Connection, interview_id: int) -> dict:
    """结束面试并生成评分。"""
    interview = conn.execute('SELECT * FROM interview WHERE id = ?',
                             (interview_id,)).fetchone()
    if interview is None:
        raise ValueError('面试不存在')

    now = db.now_iso()
    if interview['status'] == 'running':
        started = interview['started_at']
        try:
            from datetime import datetime
            delta = datetime.strptime(now, '%Y-%m-%d %H:%M:%S') - \
                datetime.strptime(started, '%Y-%m-%d %H:%M:%S')
            duration = max(0, int(delta.total_seconds()))
        except Exception:
            duration = 0
        conn.execute(
            "UPDATE interview SET status='finished', ended_at=?, duration_sec=? WHERE id=?",
            (now, duration, interview_id))

    turns = conn.execute(
        'SELECT * FROM interview_turn WHERE interview_id = ? ORDER BY seq',
        (interview_id,)).fetchall()
    if not turns:
        return {'finished': True, 'scored': False, 'reason': '没有任何问答记录'}

    transcript_lines = []
    for t in turns:
        transcript_lines.append(f"[第 {t['seq']} 题] 问：{t['question']}")
        if t['answer']:
            transcript_lines.append(f"答：{t['answer']}")
        for i, f in enumerate(json.loads(t['followups'] or '[]'), 1):
            transcript_lines.append(f"  追问 {i}：{f.get('q','')}")
            if f.get('a'):
                transcript_lines.append(f"  答：{f['a']}")
    transcript = '\n'.join(transcript_lines)

    fact_base = build_fact_base(conn, limit_points=20, limit_fields=20)
    job_ctx = build_job_context(conn, interview['job_id'])

    prompt = f"""{job_ctx}

# 面试记录（轮次：{ROUND_LABEL.get(interview['round_type'])}，
# 压力等级：{PRESSURE_LABEL.get(interview['pressure'])}）

{transcript[:12000]}

---

# 简历事实背景（用于判断他的回答与简历是否自洽）

{fact_base[:2500]}

---

请给出评估报告。"""

    try:
        review = clients.chat_json(
            [{'role': 'system', 'content': REVIEW_SYSTEM},
             {'role': 'user', 'content': prompt}],
            temperature=0.2)
    except clients.ServiceError as exc:
        return {'finished': True, 'scored': False, 'reason': str(exc)}

    scores = review.get('scores') or {}
    conn.execute(
        'UPDATE interview SET score_json=?, summary=?, highlights=?, dangers=? WHERE id=?',
        (json.dumps(scores, ensure_ascii=False),
         str(review.get('summary', '')).strip(),
         json.dumps(review.get('highlights') or [], ensure_ascii=False),
         json.dumps(review.get('dangers') or [], ensure_ascii=False),
         interview_id))
    conn.execute(
        'UPDATE interview SET review=? WHERE id=?',
        (json.dumps({'advice': review.get('advice') or [],
                     'turn_reviews': review.get('turn_reviews') or []},
                    ensure_ascii=False),
         interview_id))
    return {'finished': True, 'scored': True, 'scores': scores,
            'summary': review.get('summary', ''),
            'turn_reviews': review.get('turn_reviews') or []}


def abort(conn: sqlite3.Connection, interview_id: int) -> dict:
    """用户中途结束。已答的内容保留，直接进入评分。"""
    return finish(conn, interview_id)


def detail(conn: sqlite3.Connection, interview_id: int) -> dict | None:
    row = conn.execute('SELECT * FROM interview WHERE id = ?', (interview_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    for field, fallback in (('score_json', {}), ('highlights', []), ('dangers', [])):
        try:
            out[field] = json.loads(row[field] or 'null') or fallback
        except (json.JSONDecodeError, TypeError):
            out[field] = fallback
    try:
        out['review'] = json.loads(row['review'] or '{}')
    except (json.JSONDecodeError, TypeError):
        out['review'] = {}
    out['round_label'] = ROUND_LABEL.get(row['round_type'], row['round_type'])
    out['pressure_label'] = PRESSURE_LABEL.get(row['pressure'], row['pressure'])
    turns = []
    for t in conn.execute('SELECT * FROM interview_turn WHERE interview_id = ? ORDER BY seq',
                          (interview_id,)).fetchall():
        item = dict(t)
        try:
            item['followups'] = json.loads(t['followups'] or '[]')
        except (json.JSONDecodeError, TypeError):
            item['followups'] = []
        turns.append(item)
    out['turns'] = turns
    job = conn.execute('SELECT company, title FROM job WHERE id = ?',
                       (row['job_id'],)).fetchone()
    out['job_label'] = f'{job["company"]} · {job["title"]}' if job else ''

    # 这些题如果本来就出自题库，把题库里写好的标准答案也带上：
    # 复盘页的「标准回答案例」优先用模型针对他本人写的那份，
    # 没有的话退回题库原文（总比空着强）。
    # 面试官是"用自己的话"问的，所以精确匹配基本对不上——按相似度找。
    bank = pick_questions(conn, row['job_id'], row['resume_id'])
    standards = {}
    for t in turns:
        q = t.get('question') or ''
        if not q:
            continue
        best, best_score = None, 0.0
        for b in bank:
            sc = _similar(b['question'], q)
            if sc > best_score:
                best, best_score = b, sc
        if best is not None and best_score >= SIM_THRESHOLD and (best['standard'] or '').strip():
            standards[q] = best['standard']
    out['bank_standards'] = standards
    return out


def list_interviews(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        'SELECT i.*, j.company, j.title, '
        '  (SELECT COUNT(*) FROM interview_turn t WHERE t.interview_id = i.id) AS turns, '
        "  (SELECT COUNT(*) FROM interview_turn t WHERE t.interview_id = i.id "
        "   AND t.answer != '') AS answered_turns "
        'FROM interview i LEFT JOIN job j ON j.id = i.job_id ORDER BY i.id DESC').fetchall()
    out = []
    for r in rows:
        item = dict(r)
        try:
            item['score_json'] = json.loads(r['score_json'] or '{}')
        except (json.JSONDecodeError, TypeError):
            item['score_json'] = {}
        item['round_label'] = ROUND_LABEL.get(r['round_type'], r['round_type'])
        item['total_score'] = sum(v for v in item['score_json'].values()
                                  if isinstance(v, (int, float)))
        out.append(item)
    return out
