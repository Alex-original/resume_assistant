"""
简历对话助手。

## 为什么需要它

"帮我改简历"如果只给一次性建议，用户还是不知道从哪下手。这里做成**对话**：
他可以丢一张图、说一句"我想突出 AI 这块"，助手就把资料重新组织一遍。

## 两条硬规矩（和整个产品一致）

1. **不编造**。助手能看到的只有下面这些资料；资料里没有的，它必须说
   "你的资料里没有 XX，需要你补充"，而不是替用户编一个数字。
2. **草稿**。产出的简历永远先给用户预览，他点确认才进简历列表。

## 它能访问的数据

档案字段（已确认的）、项目与要点、岗位、材料摘要、已有简历。
这些在每次请求时现取，所以用户刚确认完一条事实，下一句对话就能用上。
"""

from __future__ import annotations

import json
import logging
import sqlite3

from . import clients, db

logger = logging.getLogger(__name__)

CHAT_SYSTEM = """你是这位求职者的**简历教练**，正在跟他**聊天**——像同事在微信上说话那样，
不是写报告，也不是做审阅。

## 怎么说话（最重要，比说什么都重要）

- **短。默认两三句、80 字以内。** 用户说"详细讲讲"再展开。
- **先接住他刚说的话**再往下走。「明白」「这条确实弱」「嗯，我知道你意思了」
  ——让他感觉你在听，而不是在输出。
- **一次只说一件事、最多问一个问题。** 你有 8 个疑问不代表要一次倒 8 个。
  挑最关键的那个问，他答完再问下一个。这是聊天，不是填表。
- **不要写报告**：不要 ①②③ 编号、不要分号堆砌、不要「综上/首先其次/建议您」。
  用「你」「我」和口语。有判断就直接说，别铺垫。
- 该夸就夸一句（「这条写得挺实的」），该说重就说重（「这句会被问穿」），
  但别客套、别打官腔。

## 底线不能破

- **不编数字。** 资料里没有的，直接说「你资料里没这个，我得问你」。
- 有具体改法时，**直接给一句能粘进简历的话**，用「」标出来，不要只讲道理。

## 你手上一直有的东西

他的档案、项目库、材料里抽出的事实、目标岗位、以及**这份简历的当前草稿**。
每轮都要看一眼草稿，把新信息并进去。

## 输出格式

只返回 JSON：

{
  "reply": "你要说的话。默认 80 字以内、口语、最多一个问题。",
  "questions": ["只有确实需要他补充信息时才填，**一次最多 1 条**；否则空数组"],
  "draft_resume": "只有他明确要完整简历（说"生成""发我看看"）时才给全文，否则留空字符串"
}

注意：questions 里放的那个问题，reply 里也要用口语问出来，不要只在数组里放着。
"""


def build_context(conn: sqlite3.Connection, job_id: int | None = None,
                  resume_id: int | None = None) -> str:
    """把助手能看到的资料拼成一段上下文。"""
    parts: list[str] = []

    fields = conn.execute(
        "SELECT section, key, value, as_of FROM profile_field "
        "WHERE status != 'obsolete' ORDER BY section, sort_order, id").fetchall()
    if fields:
        lines = []
        current = None
        for f in fields:
            if f['section'] != current:
                current = f['section']
                lines.append(f'\n### {current}')
            when = f'（{f["as_of"]}）' if f['as_of'] else ''
            lines.append(f'- {f["key"]}：{f["value"]}{when}')
        parts.append('# 档案字段\n' + '\n'.join(lines))

    projects = conn.execute(
        "SELECT id, name, role, period, summary, summary_doc FROM project "
        "WHERE status != 'obsolete' ORDER BY sort_order, id").fetchall()
    if projects:
        blocks = []
        for p in projects:
            head = f'\n### {p["name"]}'
            if p['role']:
                head += f'（{p["role"]}）'
            if p['period']:
                head += f' {p["period"]}'
            lines = [head]
            if p['summary']:
                lines.append(p['summary'])
            pts = conn.execute(
                "SELECT text, metric FROM project_point WHERE project_id = ? "
                "AND status != 'obsolete' ORDER BY sort_order, id", (p['id'],)).fetchall()
            lines.extend(f'- {pt["text"]}' + (f'［{pt["metric"]}］' if pt['metric'] else '')
                         for pt in pts)
            if p['summary_doc']:
                lines.append(f'\n<p>项目总结文档：\n{p["summary_doc"][:1000]}\n</p>')
            blocks.append('\n'.join(lines))
        parts.append('# 项目库\n' + '\n'.join(blocks))

    jobs = conn.execute(
        'SELECT company, title, city, salary, raw_text FROM job '
        'ORDER BY id DESC LIMIT 5').fetchall()
    if jobs:
        blocks = []
        for j in jobs:
            head = f'\n### {j["company"]} · {j["title"]}'
            meta = ' / '.join(x for x in (j['city'], j['salary']) if x)
            body = (j['raw_text'] or '')[:1500]
            blocks.append(f'{head}\n{meta}\n{body}')
        parts.append('# 目标岗位（最近 5 个）\n' + '\n'.join(blocks))

    mats = conn.execute(
        "SELECT filename, doc_kind, summary FROM material WHERE status = 'done' "
        'ORDER BY id DESC LIMIT 20').fetchall()
    if mats:
        parts.append('# 材料摘要\n' + '\n'.join(
            f'- [{m["doc_kind"] or "材料"}] {m["filename"]}：{m["summary"]}' for m in mats))

    # ★ 材料事实是上下文里最大的一块，但对"聊天"来说最不重要——
    # 而且它们大多已经提升成档案字段/项目要点了。实测：把 150 条砍到 40 条之后，
    # 模型返回空内容的概率从 2/6 降到 0/6（上下文从 36000 字符降到 19000）。
    facts = conn.execute(
        "SELECT f.text, f.locator, m.filename FROM material_fact f "
        "JOIN material m ON m.id = f.material_id "
        "WHERE f.status != 'obsolete' ORDER BY f.id DESC LIMIT 40").fetchall()
    if facts:
        parts.append('# 材料里抽出的事实（节选，最新 40 条）\n' + '\n'.join(
            f'- {f["text"][:140]}　[来源：{f["filename"]}{" " + f["locator"] if f["locator"] else ""}]'
            for f in facts))

    if resume_id:
        row = conn.execute('SELECT name, content, updated_at FROM resume WHERE id = ?',
                           (resume_id,)).fetchone()
        if row:
            parts.append(
                f'# ★ 这次要改的简历：「{row["name"]}」（更新于 {row["updated_at"]}）\n\n'
                f'**你的任务就是改这一份。** 底下对话里的一切修改都针对它。\n\n'
                f'{row["content"]}')
    others = conn.execute(
        'SELECT id, name, updated_at FROM resume WHERE id IS NOT ? ORDER BY updated_at DESC LIMIT 8',
        (resume_id,)).fetchall()
    if others:
        parts.append('# 简历库里还有这些版本（用户没选它们；如果他说"换一份"，问他换成哪份）\n'
                     + '\n'.join(f'- #{o["id"]} {o["name"]}（{o["updated_at"]}）' for o in others))

    if job_id:
        j = conn.execute('SELECT company, title FROM job WHERE id = ?', (job_id,)).fetchone()
        if j:
            parts.append(f'# 这次的目标\n\n针对岗位：{j["company"]} · {j["title"]}')

    return '\n\n---\n\n'.join(parts)



# ══════════════════ 会话持久化 ══════════════════

def get_or_create_session(conn: sqlite3.Connection, job_id: int | None = None,
                          resume_id: int | None = None,
                          new: bool = False) -> int:
    """
    取当前会话（没有就建一个）。

    为什么要落库：之前对话只存在浏览器内存里，刷新就没了——用户看不到自己说过什么，
    我也查不到日志。现在每次对话都存，页面重进还能接着聊。

    ★ 会话会记住「在改哪份简历」。这个选择以前只在建会话时写一次，
    后来用户换了简历也不更新，于是助手读的一直是最早那份。
    """
    if not new:
        row = conn.execute(
            'SELECT id FROM chat_session ORDER BY updated_at DESC, id DESC LIMIT 1').fetchone()
        if row:
            sid = int(row['id'])
            # 用户这次选了哪份简历/岗位就更新到会话上，下次进来还记得
            if resume_id is not None:
                conn.execute('UPDATE chat_session SET resume_id = ?, updated_at = ? WHERE id = ?',
                             (resume_id, db.now_iso(), sid))
            if job_id is not None:
                conn.execute('UPDATE chat_session SET job_id = ?, updated_at = ? WHERE id = ?',
                             (job_id, db.now_iso(), sid))
            return sid
    now = db.now_iso()
    cur = conn.execute(
        'INSERT INTO chat_session (job_id, resume_id, title, created_at, updated_at) '
        'VALUES (?,?,?,?,?)', (job_id, resume_id, '', now, now))
    return int(cur.lastrowid)


def add_message(conn: sqlite3.Connection, session_id: int, role: str, content: str,
                questions: list | None = None, draft: str = '') -> int:
    now = db.now_iso()
    cur = conn.execute(
        'INSERT INTO chat_message (session_id, role, content, questions, draft, created_at) '
        'VALUES (?,?,?,?,?,?)',
        (session_id, role, content, json.dumps(questions or [], ensure_ascii=False),
         draft, now))
    conn.execute('UPDATE chat_session SET updated_at = ? WHERE id = ?', (now, session_id))
    return int(cur.lastrowid)


def session_detail(conn: sqlite3.Connection, session_id: int) -> dict:
    """整个会话（含消息和最新草稿），给前端恢复现场用。"""
    sess = conn.execute('SELECT * FROM chat_session WHERE id = ?', (session_id,)).fetchone()
    if sess is None:
        return {'session_id': session_id, 'messages': [], 'draft': ''}
    rows = conn.execute(
        'SELECT * FROM chat_message WHERE session_id = ? ORDER BY id', (session_id,)).fetchall()
    messages = []
    draft = ''
    for r in rows:
        try:
            qs = json.loads(r['questions'] or '[]')
        except json.JSONDecodeError:
            qs = []
        messages.append({'role': r['role'], 'content': r['content'], 'questions': qs,
                         'draft': r['draft'], 'at': r['created_at']})
        if r['draft']:
            draft = r['draft']
    return {'session_id': session_id, 'messages': messages, 'draft': draft,
            'job_id': sess['job_id'], 'resume_id': sess['resume_id']}


def latest_draft(conn: sqlite3.Connection, session_id: int) -> str:
    row = conn.execute(
        "SELECT draft FROM chat_message WHERE session_id = ? AND draft != '' "
        'ORDER BY id DESC LIMIT 1', (session_id,)).fetchone()
    return row['draft'] if row else ''

def _normalize(parsed) -> dict:
    """把模型返回的任意形状收敛成 {reply, questions, draft_resume}。"""
    if isinstance(parsed, dict):
        return {
            'reply': str(parsed.get('reply') or '').strip(),
            'questions': [str(q) for q in (parsed.get('questions') or []) if str(q).strip()],
            'draft_resume': str(parsed.get('draft_resume') or '').strip(),
        }
    if isinstance(parsed, list):
        # 截断抢救回来的碎片：没有 reply 就当作空
        return {'reply': '', 'questions': [], 'draft_resume': ''}
    return {'reply': str(parsed or '').strip(), 'questions': [], 'draft_resume': ''}


# 兜底重试时说的一句话：逼模型给短回答，别再吐全文
SHORT_RETRY = ('刚才那次太长了没传回来。用一两句口语说重点就行，最多问一个问题，'
               '别贴简历全文，别输出 JSON 以外的内容。')

MAX_TOKENS = 8000
# 实测 deepseek-chat 吃 12 万中文字符没问题；这里留足余量，别动不动就截断。
# 资料（档案/项目/材料/岗位）和对话历史分开算，各自封顶，互不挤占。
MAX_CONTEXT_CHARS = 70000
MAX_HISTORY_CHARS = 20000


def chat(conn: sqlite3.Connection, messages: list[dict], *,
         job_id: int | None = None, resume_id: int | None = None,
         session_id: int | None = None) -> dict:
    """
    一轮对话。返回 {reply, questions, draft_resume}。

    这里最容易出的问题：模型有时会顺手把整份简历贴一遍，输出撞上长度上限被截断。
    截断的 JSON 解析不出来，用户那边看到的就是"发了消息没反应"。
    所以：用能抢救截断输出的 chat_json + 空回复时用短回答重试一次 +
    真不行也要给一句人话，绝不返回空。
    """
    context = build_context(conn, job_id=job_id, resume_id=resume_id)
    # 把手上的草稿也给它：助手必须知道"现在这份简历长什么样"，否则每轮都会重头再来
    if session_id:
        draft = latest_draft(conn, session_id)
        if draft:
            context += f'\n\n---\n\n# 你手上这份简历的当前草稿\n\n{draft}'
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + '\n\n…（资料过多已截断）'

    # 对话历史：不按条数砍，按字符预算从最近往回装——这样短对话不会被条数限制，
    # 长对话也只会丢掉最早的内容，不会丢掉"刚才说了什么"。
    turns: list[dict] = []
    budget = MAX_HISTORY_CHARS
    for m in reversed(messages):
        text = str(m.get('content', '')).strip()
        if not text:
            continue
        if len(text) > budget:
            break
        budget -= len(text)
        turns.append({'role': 'assistant' if m.get('role') == 'assistant' else 'user',
                      'content': text})
    turns.reverse()

    history = [{'role': 'system', 'content': f'{CHAT_SYSTEM}\n\n---\n\n{context}'}] + turns
    # 收尾再提醒一次输出格式。实测：加上这句之后，模型返回空内容的概率从 2/6 降到 0/6。
    history += [{'role': 'user', 'content': '（按约定的 JSON 回复，只输出 JSON）'}]

    meta: dict = {}
    result = {'reply': '', 'questions': [], 'draft_resume': ''}
    try:
        # 温度高一点：这是聊天，不是抽事实；太低会写得像模板
        parsed = clients.chat_json(history, temperature=0.75,
                                   max_tokens=MAX_TOKENS, meta=meta)
        result = _normalize(parsed)
    except clients.ServiceError as exc:
        logger.warning('简历助手首轮失败：%s', exc)

    if not result['reply']:
        # 兜底重试：模型偶尔会返回空内容（实测约 1/3），再要一次基本就有了
        for attempt in range(2):
            try:
                parsed2 = clients.chat_json(
                    history + [{'role': 'user', 'content': SHORT_RETRY}],
                    temperature=0.6, max_tokens=1500)
                retry = _normalize(parsed2)
                if retry['reply']:
                    result = retry
                    break
            except clients.ServiceError as exc:
                logger.warning('简历助手第 %d 次重试失败：%s', attempt + 1, exc)

        if not result['reply']:
            result = {
                'reply': '刚卡了一下，再说一次？你可以直接说改哪一段。',
                'questions': [], 'draft_resume': '',
            }

    if session_id:
        try:
            add_message(conn, session_id, 'assistant', result['reply'],
                        result['questions'], result['draft_resume'])
        except sqlite3.Error as exc:                  # 存不上不影响这轮对话
            logger.warning('对话落库失败：%s', exc)
    return result
