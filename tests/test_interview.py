"""模拟面试测试：追问上限、状态流转、评分、不留答案。"""
from __future__ import annotations

import json

from app import clients, db, interview, questions


def _seed_questions(conn, seeded, n=5):
    now = db.now_iso()
    ids = []
    for i in range(1, n + 1):
        cur = conn.execute(
            'INSERT INTO question (job_id, resume_id, question, standard, probe, '
            'key_points, followups, round_type, is_risk, risk_note, status, created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (seeded['job_id'], seeded['resume_id'], f'第 {i} 题？', f'标准答案 {i}',
             '考察点', '[]', '[]', 'tech1', 0, '', db.Q_UNSEEN, now))
        ids.append(int(cur.lastrowid))
    return ids


class TestStart:
    def test_requires_question_bank(self, conn, seeded):
        try:
            interview.start(conn, seeded['job_id'], seeded['resume_id'])
            assert False, '没有题库时应该拒绝开始'
        except ValueError as e:
            assert '题库' in str(e)

    def test_start_returns_first_question(self, conn, seeded, fake_ai):
        """
        开场白和第一问由模型生成（真人不会"第一题：……"这么开口）；
        模型挂掉时必须退回题库第一题，不能开不起面试。
        """
        _seed_questions(conn, seeded, 3)
        fake_ai.json_response = {'greeting': '我是这次的技术面试官。',
                                 'question': '先聊聊你最近这个项目？'}
        out = interview.start(conn, seeded['job_id'], seeded['resume_id'],
                              'tech1', 'strict')
        assert out['interview_id']
        assert out['turn'] == 1
        assert out['greeting'] == '我是这次的技术面试官。'
        assert out['question'] == '先聊聊你最近这个项目？'
        assert out['round_label'] == '技术一面'
        assert out['pressure_label'] == '偏严'
        assert out['total_questions'] == 3

    def test_start_falls_back_to_bank_when_model_fails(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 3)
        fake_ai.raise_error = clients.ServiceError('模型挂了')
        out = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        assert out['question'] == '第 1 题？', '开场白失败也要能开面试'
        fake_ai.raise_error = None

    def test_start_does_not_leak_answer(self, conn, seeded):
        """★ 面试开始返回的载荷里**不能包含标准答案或考察点**。"""
        _seed_questions(conn, seeded, 2)
        out = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        payload = json.dumps(out, ensure_ascii=False)
        assert '标准答案' not in payload
        assert '考察点' not in payload


class TestAnswerFlow:
    def test_followup_then_next(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']

        fake_ai.json_response = {'action': 'followup', 'text': '那统计周期呢？'}
        r1 = interview.answer(conn, iid, '日活大概一千四。')
        assert r1['type'] == 'followup'
        assert r1['text'] == '那统计周期呢？'

        interview.submit_followup_answer(conn, iid, '一个月。')

        fake_ai.json_response = {'action': 'followup', 'text': '第二个追问'}
        r2 = interview.answer(conn, iid, '一个月。')
        assert r2['type'] == 'followup'

        interview.submit_followup_answer(conn, iid, '第二答')

        # ★ 第三次必须进入下一题，不能无限追问
        fake_ai.json_response = {'action': 'followup', 'text': '第三个追问（不该出现）'}
        r3 = interview.answer(conn, iid, '第三答')
        assert r3['type'] == 'next'
        assert '第三个追问' not in json.dumps(r3, ensure_ascii=False)

    def test_max_two_followups_enforced(self, conn, seeded, fake_ai):
        """追问上限由代码强制，不依赖模型自觉。"""
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'followup', 'text': '追问'}
        interview.answer(conn, iid, 'a1')
        interview.submit_followup_answer(conn, iid, 'a2')
        interview.answer(conn, iid, 'a2')
        interview.submit_followup_answer(conn, iid, 'a3')
        r = interview.answer(conn, iid, 'a3')
        assert r['type'] == 'next'

    def test_next_moves_to_second_question(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'next', 'text': ''}
        r = interview.answer(conn, s['interview_id'], '不知道。')
        assert r['type'] == 'next'
        assert r['text'] == '第 2 题？'
        assert r['turn'] == 2

    def test_last_question_finishes(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 1)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'next', 'text': ''}
        r = interview.answer(conn, s['interview_id'], '答完了。')
        assert r.get('finished') is True

    def test_answer_after_finish_rejected(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 1)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'scores': {'tech': 6}, 'summary': 'x',
                                 'highlights': [], 'dangers': [], 'advice': []}
        interview.finish(conn, s['interview_id'])
        try:
            interview.answer(conn, s['interview_id'], '还想说')
            assert False
        except ValueError as e:
            assert '结束' in str(e)

    def test_ai_failure_falls_through_to_next(self, conn, seeded, fake_ai):
        """追问判定失败时不应卡死，应直接进入下一题。"""
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.raise_error = db_now_error()
        r = interview.answer(conn, s['interview_id'], '回答了。')
        assert r['type'] == 'next'
        fake_ai.raise_error = None


def db_now_error():
    from app import clients
    return clients.ServiceError('模拟 AI 故障')


class TestFinish:
    def test_scores_stored(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'next', 'text': ''}
        interview.answer(conn, s['interview_id'], '第一个回答')
        fake_ai.json_response = {
            'scores': {'tech': 7, 'structure': 6, 'honesty': 9, 'tradeoff': 5,
                       'communication': 6},
            'summary': '整体还行但深度不足',
            'highlights': [{'turn': 1, 'quote': '他说了统计口径', 'why': '诚实'}],
            'dangers': [{'turn': 2, 'problem': '没有数字', 'better': '应该补上'}],
            'advice': ['补充数字口径', '练习结构化表达', '复习 Agent 架构'],
        }
        out = interview.finish(conn, s['interview_id'])
        assert out['scored'] is True

        d = interview.detail(conn, s['interview_id'])
        assert d['status'] == 'finished'
        assert d['score_json']['tech'] == 7
        assert sum(d['score_json'].values()) == 33
        assert len(d['highlights']) == 1
        assert len(d['dangers']) == 1
        assert d['review']['advice'][0] == '补充数字口径'
        assert d['summary']

    def test_turns_recorded_with_followups(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 1)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'followup', 'text': '追问内容'}
        interview.answer(conn, s['interview_id'], '我的回答')
        interview.submit_followup_answer(conn, s['interview_id'], '追问的回答')
        fake_ai.json_response = {'scores': {}, 'summary': '', 'highlights': [],
                                 'dangers': [], 'advice': []}
        interview.finish(conn, s['interview_id'])
        d = interview.detail(conn, s['interview_id'])
        assert d['turns'][0]['answer'] == '我的回答'
        assert d['turns'][0]['followups'][0]['q'] == '追问内容'
        assert d['turns'][0]['followups'][0]['a'] == '追问的回答'

    def test_score_failure_does_not_crash(self, conn, seeded, fake_ai):
        """评分失败时面试仍应正常结束，只是标记未评分。"""
        _seed_questions(conn, seeded, 1)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'next', 'text': ''}
        interview.answer(conn, s['interview_id'], '答')
        fake_ai.raise_error = db_now_error()
        out = interview.finish(conn, s['interview_id'])
        assert out['finished'] is True
        assert out['scored'] is False
        assert interview.detail(conn, s['interview_id'])['status'] == 'finished'
        fake_ai.raise_error = None

    def test_finish_without_turns(self, conn, seeded):
        now = db.now_iso()
        cur = conn.execute(
            "INSERT INTO interview (job_id, resume_id, status, started_at) VALUES (?,?,?,?)",
            (seeded['job_id'], seeded['resume_id'], 'running', now))
        out = interview.finish(conn, int(cur.lastrowid))
        assert out['scored'] is False


class TestList:
    def test_list_interviews(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 1)
        interview.start(conn, seeded['job_id'], seeded['resume_id'])
        items = interview.list_interviews(conn)
        assert len(items) == 1
        assert items[0]['title'] == 'AI 工程师（金融智能）'
        assert items[0]['round_label'] == '技术一面'


class TestInterviewerListens:
    """
    ★ 回归：面试官必须"听得见"候选人说了什么。

    踩过的坑：追问判断只把「问题 + 最后一句回答」喂给模型，没给这一题
    已经聊过的追问链，也没给之前问过的题。结果候选人讲了一大段、核心点
    已经答到了，面试官还在重复问"这个数据怎么统计的"——像没在听。
    """

    def test_prompt_includes_whole_followup_chain(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']

        fake_ai.json_response = {'action': 'followup', 'text': '那按设备还是按账号？'}
        interview.answer(conn, iid, '口径是当日有成交的独立用户。')
        interview.submit_followup_answer(conn, iid, '按设备去重。')

        # 回答追问：必须带 is_followup=True（API 层就是这么调的），
        # 否则这句会覆盖掉主问题的回答
        fake_ai.json_response = {'action': 'next', 'text': ''}
        interview.answer(conn, iid, '换个手机会被算两次，误差 3% 以内。',
                         is_followup=True)

        prompt = None
        for kind, messages, _kw in reversed(fake_ai.calls):
            if kind == 'json':
                prompt = json.dumps(messages, ensure_ascii=False)
                break
        assert prompt, '没有捕获到追问判断的 prompt'
        assert '当日有成交的独立用户' in prompt, '没把候选人的回答带上'
        assert '按设备还是按账号' in prompt, '没把已经追问过的问题带上'
        assert '按设备去重' in prompt, '没把候选人对追问的回答带上'

    def test_prompt_includes_previous_questions(self, conn, seeded, fake_ai):
        """之前问过的题要带上，否则面试官会绕回去重复问。"""
        _seed_questions(conn, seeded, 3)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']

        fake_ai.json_response = {'action': 'next', 'text': ''}
        r = interview.answer(conn, iid, '答第一题')
        second_q = r['text']

        fake_ai.json_response = {'action': 'next', 'text': ''}
        interview.answer(conn, iid, '答第二题')

        prompt = json.dumps([c for c in fake_ai.calls if c[0] == 'json'][-1],
                            ensure_ascii=False)
        assert second_q[:20] in prompt, '没把之前问过的题带上'

    def test_ack_returned_and_capped(self, conn, seeded, fake_ai):
        """模型给的简短回应要透传给前端（真人面试不会你答完直接蹦下一题）。"""
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']

        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': '好，口径清楚了。'}
        r = interview.answer(conn, iid, '口径是当日有成交的独立用户。')
        assert r['ack'] == '好，口径清楚了。'

        # 追问分支也要带 ack
        _seed_questions(conn, seeded, 2)
        s2 = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'followup', 'text': '那统计周期呢？', 'ack': '嗯'}
        r2 = interview.answer(conn, s2['interview_id'], '大概一千四。')
        assert r2['type'] == 'followup' and r2['ack'] == '嗯'

    def test_ack_truncated(self, conn, seeded, fake_ai):
        """模型偶尔会把 ack 写成一句话，截断掉，别把 UI 撑爆。"""
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        fake_ai.json_response = {'action': 'next', 'text': '',
                                 'ack': '好' * 100}
        r = interview.answer(conn, s['interview_id'], '答案')
        assert len(r['ack']) <= 20


class TestAnswerNotClobbered:
    """
    ★ 回归：追问的回答**不能覆盖**主问题的回答。

    踩过的坑：interview_turn.answer 只有一个字段，回答追问时也被写进了这里。
    结果是面试记录变成「问：日活怎么统计？答：换个手机会被算两次」——
    驴唇不对马嘴，而且评分模型读的就是这份错位的记录。
    """

    def test_main_answer_survives_followup(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']

        fake_ai.json_response = {'action': 'followup', 'text': '那按设备还是按账号？'}
        interview.answer(conn, iid, '主问题的回答：口径是当日有成交的独立用户。')
        interview.submit_followup_answer(conn, iid, '按设备去重。')
        fake_ai.json_response = {'action': 'next', 'text': ''}
        interview.answer(conn, iid, '追问的回答：换个手机可能被算两次。',
                         is_followup=True)

        turn = conn.execute(
            'SELECT * FROM interview_turn WHERE interview_id=? ORDER BY seq LIMIT 1',
            (iid,)).fetchone()
        assert '主问题的回答' in turn['answer'], '主问题的回答被追问覆盖了'
        assert '追问的回答' not in turn['answer']

        # 追问的回答存在 followups[].a 里（由 submit_followup_answer 写入，
        # API 层在调 answer 之前会先调它）
        fups = json.loads(turn['followups'])
        assert fups[0]['q'] == '那按设备还是按账号？'
        assert fups[0]['a'] == '按设备去重。'

    def test_transcript_keeps_qa_in_order(self, conn, seeded, fake_ai):
        """评分用的逐字稿里，主问题、主回答、追问、追问回答的顺序不能乱。"""
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']

        fake_ai.json_response = {'action': 'followup', 'text': '那统计周期呢？'}
        interview.answer(conn, iid, '主回答ABC')
        interview.submit_followup_answer(conn, iid, '追问回答XYZ')

        fake_ai.json_response = {'scores': {'tech': 6}, 'summary': 'x',
                                 'highlights': [], 'dangers': [], 'advice': []}
        interview.finish(conn, iid)

        prompt = None
        for kind, messages, _kw in reversed(fake_ai.calls):
            if kind == 'json':
                prompt = messages[1]['content']
                break
        assert prompt and '主回答ABC' in prompt and '追问回答XYZ' in prompt
        assert prompt.index('主回答ABC') < prompt.index('那统计周期呢？')
        assert prompt.index('那统计周期呢？') < prompt.index('追问回答XYZ')


class TestQuestionBankFallback:
    """
    ★ 回归：删掉简历之后，岗位的题库不能变成孤儿。

    题库是按 (岗位, 简历) 生成的，简历删了会把题的 resume_id 置空。
    如果取题时严格按 resume_id 匹配，就会出现「明明有 12 道题，
    却提示这个岗位还没有题库」——实测踩到过。
    """

    def test_questions_survive_resume_deletion(self, conn, seeded):
        _seed_questions(conn, seeded, 3)
        # 把题的 resume_id 置空，模拟简历被删（ON DELETE SET NULL 的效果）
        conn.execute('UPDATE question SET resume_id = NULL WHERE job_id = ?',
                     (seeded['job_id'],))
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        assert s['total_questions'] == 3, '岗位级题库应该还能用'

    def test_job_level_questions_are_merged_in(self, conn, seeded):
        """这份简历的题 + 岗位级（孤儿）的题合并使用——都是同一个岗位的题，不浪费。"""
        _seed_questions(conn, seeded, 2)
        conn.execute(
            "INSERT INTO question (job_id, resume_id, question, standard, probe, "
            "key_points, followups, round_type, is_risk, status, created_at) "
            "VALUES (?, NULL, '岗位级孤儿题？', '', '', '[]', '[]', 'tech1', 0, 'unseen', ?)",
            (seeded['job_id'], '2026-01-01 00:00:00'))
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        assert s['total_questions'] == 3

    def test_pick_questions_helper(self, conn, seeded):
        _seed_questions(conn, seeded, 2)
        rows = interview.pick_questions(conn, seeded['job_id'], seeded['resume_id'])
        assert len(rows) == 2
        assert interview.pick_questions(conn, seeded['job_id'], 99999) == []


class TestNoRepeatedTopics:
    """
    ★ 回归：面试官不许反复问同一件事。

    踩过的坑：判重拿"模型问出来的原话"去和题库原文比，而模型每次都会换个说法，
    结果题库里每一条永远显示「未聊」——同一道题被反复问
    （实测 Q1 和 Q3 都问「90% 怎么算的」，Q4 和 Q5 都问「7000 行转换」）。
    """

    def test_similarity_separates_same_from_different(self):
        same = [
            ('你在同花顺做交易查询重构，说查询模块复用率达到 90%，这个 90% 是怎么算出来的？',
             '换个话题——你简历里写重构交易查询页面用了代理模式加工厂模式，'
             '说查询模块复用率达到 90%，这个 90% 具体是怎么算出来的'),
            ('你简历里写用 Claude 把 iOS 代码转成 Android 代码，7000 行，这个转换怎么做的？',
             '你简历里写用 Claude 把 iOS 代码转成 Android 代码、7000 行，这个转换怎么实现的'),
        ]
        diff = [
            ('你在同花顺做交易查询重构，说复用率达到 90%，这个 90% 怎么算的？',
             '你之前主要是 iOS 开发，我们要求 Java/Go/C++ 至少一种，你怎么看'),
            ('你在同花顺做交易查询重构，说复用率达到 90%，这个 90% 怎么算的？',
             '你说复用率达到 90%，但重构前后代码量、编译时间有没有变化，'
             '你怎么证明这次重构值得'),
            ('Video Note 的付费转化率 11.6% 是怎么来的？',
             'Trade Master 的行情取数优化，-95.8% 这个数字怎么算的？'),
        ]
        for a, b in same:
            assert interview._similar(a, b) >= interview.SIM_THRESHOLD, f'该判为同一件事：{a[:20]}'
        for a, b in diff:
            assert interview._similar(a, b) < interview.SIM_THRESHOLD, f'不该判为同一件事：{a[:20]}'

    def test_match_topic_finds_paraphrase(self):
        bank = [{'question': '你在同花顺做交易查询重构，说查询模块复用率达到 90%，这个 90% 是怎么算出来的？'},
                {'question': '你之前主要是 iOS 开发，我们要求 Java/Go/C++ 至少一种服务端语言'}]
        got = interview._match_topic(
            bank, '换个话题——你简历里写重构交易查询页面用了代理模式加工厂模式，'
                  '说查询模块复用率达到 90%，这个 90% 具体是怎么算出来的')
        assert got == 0, '换了个说法的同一道题应该对回编号 0'

    def test_asked_index_recorded_even_without_model_index(self, conn, seeded, fake_ai):
        """模型不返回 topic_index 时，也要靠相似度把编号记下来。"""
        now = db.now_iso()
        conn.execute(
            'INSERT INTO question (job_id, resume_id, question, standard, probe, key_points, '
            'followups, round_type, is_risk, risk_note, status, created_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (seeded['job_id'], seeded['resume_id'],
             '你在同花顺做交易查询重构，说查询模块复用率达到 90%，这个 90% 是怎么算出来的？',
             '标准答案', '考察点', '[]', '[]', 'tech1', 0, '', db.Q_UNSEEN, now))
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        # 模型换了个说法问同一道题，而且不返回 topic_index
        fake_ai.json_response = {
            'action': 'next',
            'text': '换个话题——你简历里写重构交易查询页面用了代理模式，'
                    '说查询模块复用率达到 90%，这个 90% 具体是怎么算出来的',
            'ack': ''}
        interview.answer(conn, iid, '随便答一句')
        rows = conn.execute(
            'SELECT topic_index FROM interview_turn WHERE interview_id=? ORDER BY seq',
            (iid,)).fetchall()
        assert len(rows) == 2
        assert rows[1]['topic_index'] == 0, '换了个说法也要能对回题库编号'

    def test_used_topics_are_marked_in_prompt(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 3)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'next', 'text': '换一个话题，聊聊你的项目', 'ack': ''}
        interview.answer(conn, iid, '答一句')
        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': ''}
        interview.answer(conn, iid, '再答一句')
        prompt = json.dumps([c for c in fake_ai.calls if c[0] == 'json'][-1],
                            ensure_ascii=False)
        assert '已聊过' in prompt, '话题清单里要标出已聊过的'
        assert '不许再问同一件事' in prompt or '绝对不许重复问' in prompt


class TestModelAnswerInReview:
    """复盘页的「标准回答案例」：评审要逐题给，题库原文兜底。"""

    def test_review_produces_turn_reviews(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': ''}
        interview.answer(conn, iid, '我的回答')
        fake_ai.json_response = {
            'scores': {'tech': 6}, 'summary': '总评', 'highlights': [], 'dangers': [],
            'advice': ['a'],
            'turn_reviews': [{'seq': 1, 'wanted': '想听口径', 'gap': '没说口径',
                              'model_answer': '标准回答案例ABC'}]}
        out = interview.finish(conn, iid)
        assert out['scored'] is True
        assert out['turn_reviews'][0]['model_answer'] == '标准回答案例ABC'
        detail = interview.detail(conn, iid)
        assert detail['review']['turn_reviews'][0]['seq'] == 1

    def test_bank_standard_available_as_fallback(self, conn, seeded, fake_ai):
        _seed_questions(conn, seeded, 2)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        detail = interview.detail(conn, iid)
        # 题库里的题带标准答案，应该能匹配上（面试官问的就是题库原话）
        assert detail['bank_standards'], '题库标准答案没匹配上'


class TestInterviewEndConditions:
    """
    ★ 回归：面试必须能**自动结束**，不能只靠用户手动点。

    踩过的坑：结束闸门里引用了一个已经被改名的变量（used → used_idx），
    只要模型说"聊完了"就抛 NameError —— 自动结束这条路从来没生效过。
    更隐蔽的是第二个坑：一句提问会把**所有**相似度超阈值的题都标成已聊，
    题库里如果有几道长得很像，剩余话题瞬间清零，面试刚开场就结束。
    """

    def _bank(self, conn, seeded, questions):
        now = db.now_iso()
        for q in questions:
            conn.execute(
                'INSERT INTO question (job_id, resume_id, question, standard, probe, '
                'key_points, followups, round_type, is_risk, status, created_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (seeded['job_id'], seeded['resume_id'], q, '标准答案', '考察点',
                 '[]', '[]', 'tech1', 0, db.Q_UNSEEN, now))

    REAL = [
        '你在同花顺做交易查询重构，复用率 90% 这个数怎么算出来的？',
        'Video Note 的付费转化率 11.6% 是怎么来的？',
        'Trade Master 的行情取数优化，-95.8% 怎么算的？',
        '你之前主要是 iOS 开发，服务端语言这块你打算怎么补？',
        '慢计划快执行里那个 500 毫秒是怎么测的？',
        '68 项离线回归和 228 项离线回归分别覆盖什么？',
    ]

    def test_model_can_end_the_interview(self, conn, seeded, fake_ai):
        """模型说"聊完了"，闸门放行后必须真的能结束（不能再抛 NameError）。"""
        self._bank(conn, seeded, self.REAL)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'finish', 'text': '', 'ack': '好'}
        out = None
        for _ in range(10):
            out = interview.answer(conn, iid, '我的回答内容够长')
            if out.get('finished'):
                break
        assert out.get('finished'), '聊完话题后应该能自动结束'

    def test_does_not_end_before_min_topics(self, conn, seeded, fake_ai):
        """聊得太少时，模型说结束也不许结束。"""
        self._bank(conn, seeded, self.REAL)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'finish', 'text': '', 'ack': '好'}
        r = interview.answer(conn, iid, '只答了一题')
        assert not r.get('finished'), f'才聊 1 个话题不该结束（MIN={interview.MIN_TOPICS}）'
        assert r['type'] == 'next'

    def test_similar_bank_questions_not_all_marked_used(self, conn, seeded, fake_ai):
        """
        题库里有长得很像的题时，一句提问只能认领最像的那一条。
        否则剩余话题会瞬间清零、面试刚开场就结束。
        """
        self._bank(conn, seeded, [f'这是第 {i} 个足够长的问题吗？' for i in range(1, 9)])
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'finish', 'text': '', 'ack': '好'}
        r = interview.answer(conn, iid, '第一次回答')
        assert not r.get('finished'), '相似题库不该一次就被全部标成已聊'

    def test_max_topics_forces_finish(self, conn, seeded, fake_ai):
        """聊到上限就强制收尾，别没完没了。"""
        self._bank(conn, seeded, self.REAL)
        s = interview.start(conn, seeded['job_id'], seeded['resume_id'])
        iid = s['interview_id']
        fake_ai.json_response = {'action': 'next', 'text': '再来一个问题吧', 'ack': ''}
        out = None
        for _ in range(interview.MAX_TOPICS + 3):
            out = interview.answer(conn, iid, '回答')
            if out.get('finished'):
                break
        assert out.get('finished'), f'聊到 {interview.MAX_TOPICS} 个话题应该强制结束'
