#!/usr/bin/env python3
"""
端到端链路验证：用真实材料跑通「材料 → 岗位 → 简历 → 建议 → 题库 → 面试 → 评分」。

这是一次真调用（会消耗 API 额度），目的是在写前端之前确认后端 AI 链路是活的。

用法：
    .venv/bin/python tests/e2e_pipeline.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clients, db, interview, jobs, materials, packaging, questions  # noqa: E402

ATTACH = Path('/Users/wjx/.dsh/attachments/v1/objects')
RESUME_IMG = ATTACH / 'b1' / 'b1e7043022741b18a95e0181df25c4c102cfe203b87104fd970aa4665e0be53d'
JD_IMG = ATTACH / '87' / '87c19430f93dcb8c56d625f7e90106f38c1a5cb599dce5ff11e6f864d198cc47'

RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, fn):
    t0 = time.time()
    print(f'\n▶ {name}')
    try:
        out = fn()
        dt = time.time() - t0
        print(f'  ✅ 完成（{dt:.1f}s）: {json.dumps(out, ensure_ascii=False)[:300]}')
        RESULTS.append((name, True, f'{dt:.1f}s'))
        return out
    except Exception as exc:
        dt = time.time() - t0
        print(f'  ❌ 失败（{dt:.1f}s）: {type(exc).__name__}: {exc}')
        RESULTS.append((name, False, f'{type(exc).__name__}: {str(exc)[:120]}'))
        return None


def main() -> int:
    db.init_db()
    # 多用户之后，业务数据是按「当前用户」分库的。这里没有 HTTP 请求，
    # 所以要么显式指定一个用户，要么走 legacy 库。
    # 默认用一个独立的 E2E 用户，免得把真实账号的数据搅乱。
    from app import auth
    user = auth.get_user_by_name('e2e')
    if not user:
        user = auth.create_user('e2e', 'e2e-not-a-real-login', '端到端测试')
    db.set_current_user(user['id'])
    print(f'  （数据写入 E2E 用户的独立库 u{user["id"]}.db，不影响真实数据）')

    # ── 1. 材料：上传简历截图并解析（视觉模型）──
    def _material():
        with db.session() as conn:
            mid = materials.create_material(conn, 'resume.png', RESUME_IMG.read_bytes())
        with db.session() as conn:
            res = materials.parse_material(conn, mid)
        if not res.get('ok'):
            raise RuntimeError(res.get('error'))
        return {'material_id': mid, **res}

    mat = step('1. 材料解析（视觉读简历截图）', _material)
    if not mat:
        return summarise()

    # ── 2. 岗位：从 JD 截图建岗位并分析 ──
    def _job():
        with db.session() as conn:
            jid = materials.create_material(conn, 'jd.png', JD_IMG.read_bytes())
            r = materials.parse_material(conn, jid)
            now = db.now_iso()
            cur = conn.execute(
                'INSERT INTO job (company, title, city, raw_text, material_id, status, '
                'created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
                ('蚂蚁集团', 'AI 工程师（金融智能）', '杭州', r.get('summary', ''),
                 jid, db.JOB_TO_APPLY, now, now))
            job_id = int(cur.lastrowid)
        with db.session() as conn:
            out = jobs.analyze(conn, job_id)
        return {'job_id': job_id, **out}

    job = step('2. 岗位分析（JD 四层拆解 + 匹配度）', _job)
    if not job:
        return summarise()

    # ── 3. 简历：从基线建一份，并确认一部分档案让事实库可用 ──
    def _resume():
        with db.session() as conn:
            conn.execute(
                "UPDATE profile_field SET status='confirmed' WHERE status='unconfirmed'")
            conn.execute(
                "UPDATE project SET status='confirmed' WHERE status='unconfirmed'")
            conn.execute(
                "UPDATE project_point SET status='confirmed' WHERE status='unconfirmed'")
            baseline = Path('/Users/wjx/Agent-100-Days/求职助手/00-档案/简历基线.md')
            text = baseline.read_text(encoding='utf-8')
            i = text.find('## 一、定制简历正文')
            content = text[i:] if i >= 0 else text
            now = db.now_iso()
            cur = conn.execute(
                'INSERT INTO resume (name, job_id, content, status, created_at, updated_at) '
                'VALUES (?,?,?,?,?,?)',
                ('蚂蚁金融智能版', job['job_id'], content, 'draft', now, now))
        return {'resume_id': int(cur.lastrowid), 'chars': len(content)}

    res = step('3. 建立简历（从基线导入并确认档案）', _resume)
    if not res:
        return summarise()

    # ── 4. 简历包装：逐条建议 ──
    def _packaging():
        with db.session() as conn:
            out = packaging.generate_suggestions(conn, job['job_id'], res['resume_id'])
            items = packaging.list_suggestions(conn, res['resume_id'], job['job_id'])
        grades = {}
        for it in items:
            grades[it['grade']] = grades.get(it['grade'], 0) + 1
        no_evidence = [it['id'] for it in items
                       if it['grade'] == 'ok' and not it['evidence']]
        if no_evidence:
            raise RuntimeError(f'有 {len(no_evidence)} 条 ok 级建议缺依据——强制校验失效')
        return {**out, 'grades': grades, 'sample': items[0]['location'] if items else ''}

    step('4. 简历包装（逐条建议 + 分级 + 依据）', _packaging)

    # ── 5. 题库生成 ──
    def _questions():
        with db.session() as conn:
            out = questions.generate_questions(conn, job['job_id'], res['resume_id'],
                                               count=12)
            items = questions.list_questions(conn, job['job_id'])
        # ★ 一道题都没出就是失败。不抛异常的话这一步会假通过，
        #   后面的模拟面试又被跳过，整轮 e2e 看着 5/5 全绿其实核心功能是坏的。
        if out['total'] == 0:
            raise RuntimeError('题库生成为 0 题：' + '；'.join(out.get('warnings') or []))
        risk = sum(1 for q in items if q['is_risk'])
        with_std = sum(1 for q in items if q['standard'])
        with_fu = sum(1 for q in items if q['followups'])
        return {**out, 'risk': risk, 'with_standard': with_std, 'with_followups': with_fu}

    qs = step('5. 题库生成（问题+标准答案+考察点+追问链）', _questions)
    if not qs or qs['total'] == 0:
        return summarise()

    # ── 6. 模拟面试：开始 → 回答 → 结束评分 ──
    def _interview():
        with db.session() as conn:
            start = interview.start(conn, job['job_id'], res['resume_id'],
                                    'tech1', 'strict')
        iid = start['interview_id']
        log = [f"Q1: {start['question'][:60]}"]
        answer = ('这个功能是我负责的交易模块里的市价委托，日活大概一千四百人。'
                  '统计口径是产品侧从埋点里取的当日活跃用户数，我了解到的是按设备去重。')
        with db.session() as conn:
            nxt = interview.answer(conn, iid, answer)
        log.append(f"→ {nxt.get('type')}: {str(nxt.get('text',''))[:60]}")
        # 答一次追问
        if nxt.get('type') == 'followup':
            with db.session() as conn:
                interview.submit_followup_answer(conn, iid, '这是产品侧给的数，我不确定精确口径。')
                nxt2 = interview.answer(conn, iid, '这是产品侧给的数，我不确定精确口径。')
            log.append(f"→ {nxt2.get('type')}: {str(nxt2.get('text',''))[:60]}")
        with db.session() as conn:
            fin = interview.finish(conn, iid)
            detail = interview.detail(conn, iid)
        return {'interview_id': iid, 'scored': fin.get('scored'),
                'total_score': sum(detail.get('score_json', {}).values())
                if detail else 0,
                'turns': len(detail['turns']) if detail else 0,
                'transcript': log}

    step('6. 模拟面试（追问 + 五维评分）', _interview)

    return summarise()


def summarise() -> int:
    print('\n' + '═' * 60)
    ok = sum(1 for _, passed, _ in RESULTS if passed)
    print(f'结果：{ok}/{len(RESULTS)} 步通过')
    for name, passed, note in RESULTS:
        print(f'  {"✅" if passed else "❌"} {name}  —  {note}')
    return 0 if ok == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
