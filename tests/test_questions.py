"""题库生成测试（含分批与截断恢复）。"""
from __future__ import annotations

from app import clients, db, questions


def _q(i: int, **over):
    base = {
        'question': f'第 {i} 题：你说这个功能日活 1400 人，是怎么统计的？',
        'standard': '这是功能级别的日活，口径是……',
        'probe': '验证数字真伪',
        'key_points': [{'do': True, 'text': '说清口径'}, {'do': False, 'text': '含糊其辞'}],
        'followups': ['那统计周期呢？', '如果重新统计你会怎么做？'],
        'round_type': 'tech1',
        'is_risk': False,
        'risk_note': '',
    }
    base.update(over)
    return base


class TestGenerate:
    def test_basic(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1), _q(2)]}
        out = questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'],
                                           count=2)
        assert out['total'] == 2
        items = questions.list_questions(conn, seeded['job_id'])
        assert len(items) == 2
        assert items[0]['standard']
        assert len(items[0]['followups']) == 2
        assert items[0]['key_points'][0]['do'] is True

    def test_respects_requested_count(self, conn, seeded, fake_ai):
        """模型多给时应该截断到请求数量——避免 token 浪费。"""
        fake_ai.json_response = {'questions': [_q(i) for i in range(1, 21)]}
        out = questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'],
                                           count=5)
        assert out['total'] == 5

    def test_batches_when_more_than_batch_size(self, conn, seeded, fake_ai):
        """超过一批的量应分多批请求。"""
        fake_ai.json_response = {'questions': [_q(i) for i in range(1, 7)]}
        out = questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'],
                                           count=12)
        assert out['batches'] == 2

    def test_deduplicates(self, conn, seeded, fake_ai):
        """重复的题目只保留一条。"""
        fake_ai.json_response = {'questions': [_q(1), _q(1), _q(1)]}
        out = questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'],
                                           count=5)
        assert out['total'] == 1

    def test_regenerate_replaces(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1)]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=1)
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=1)
        assert len(questions.list_questions(conn, seeded['job_id'])) == 1

    def test_risk_flag_stored(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [
            _q(1, is_risk=True, risk_note='这个数字缺口径')]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=1)
        item = questions.list_questions(conn, seeded['job_id'])[0]
        assert item['is_risk'] == 1
        assert item['risk_note'] == '这个数字缺口径'

    def test_invalid_round_falls_back(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1, round_type='不存在的轮次')]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=1)
        assert questions.list_questions(conn, seeded['job_id'])[0]['round_type'] == 'tech1'

    def test_missing_resume_raises(self, conn, seeded):
        try:
            questions.generate_questions(conn, seeded['job_id'], 999999, count=1)
            assert False
        except ValueError:
            pass

    def test_truncated_output_still_yields_questions(self, conn, seeded, fake_ai,
                                                     monkeypatch):
        """
        ★ 回归测试：模型输出被截断时，应抢救出完整题目而不是整体失败。
        （这是实测踩到的坑：25 道题一次生成超出输出上限。）
        """
        truncated = ('{"questions":['
                     '{"question":"这是一个完整且足够长的问题内容","standard":"A1","probe":"P1",'
                     '"key_points":[],"followups":[],"round_type":"tech1","is_risk":false},'
                     '{"question":"Q2 被截断的问题","stan')
        # 让 chat_json 走真实的抢救逻辑：chat 喂截断文本，chat_json 用真实现
        monkeypatch.setattr(clients, 'chat', lambda *a, **k: truncated)
        monkeypatch.setattr(clients, 'chat_json', fake_ai.real['chat_json'])
        out = questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'],
                                           count=3)
        assert out['total'] >= 1
        assert questions.list_questions(conn, seeded['job_id'])[0]['question']


class TestStatusAndCoverage:
    def test_set_status(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1)]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=1)
        qid = questions.list_questions(conn, seeded['job_id'])[0]['id']

        for st in (db.Q_MASTERED, db.Q_REVIEW, db.Q_SEEN):
            assert questions.set_status(conn, qid, st)['status'] == st

    def test_invalid_status_rejected(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1)]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=1)
        qid = questions.list_questions(conn, seeded['job_id'])[0]['id']
        try:
            questions.set_status(conn, qid, 'nonsense')
            assert False
        except ValueError:
            pass

    def test_coverage(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1), _q(2, is_risk=True)]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=2)
        cov = questions.coverage(conn, seeded['job_id'])
        assert cov['total'] == 2
        assert cov['risk'] == 1

    def test_filter_by_status(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'questions': [_q(1), _q(2)]}
        questions.generate_questions(conn, seeded['job_id'], seeded['resume_id'], count=2)
        items = questions.list_questions(conn, seeded['job_id'])
        questions.set_status(conn, items[0]['id'], db.Q_REVIEW)
        assert len(questions.list_questions(conn, seeded['job_id'], status=db.Q_REVIEW)) == 1
