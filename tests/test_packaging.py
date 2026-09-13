"""
简历包装测试。

这个模块的核心约束是「**没有依据不许生成**」，
所以测试重点是：**强制降级机制真的生效**，而不是只写在提示词里。
"""
from __future__ import annotations

from app import db, packaging


class TestFactBase:
    def test_only_confirmed_facts_enter(self, conn, temp_db, seeded):
        """事实库只收已确认的内容——未确认的草稿不能拿去改简历。"""
        now = temp_db.now_iso()
        conn.execute(
            'INSERT INTO profile_field (section, key, value, source, as_of, status, '
            'note, sort_order, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            ('测试', '未确认项', '不该出现的值', 'x', '', 'unconfirmed', '', 99, now))
        base = packaging.build_fact_base(conn)
        assert '不该出现的值' not in base
        assert '日活约 1400 人' in base

    def test_pending_points_are_labeled(self, conn, temp_db, seeded):
        """未确认的项目要点可以给 AI 看，但必须标注「尚未确认」。"""
        now = temp_db.now_iso()
        conn.execute(
            'INSERT INTO project_point (project_id, kind, text, status, sort_order, '
            'updated_at) VALUES (?,?,?,?,?,?)',
            (seeded['project_id'], 'point', '待确认的要点XYZ', 'unconfirmed', 99, now))
        base = packaging.build_fact_base(conn)
        assert '待确认的要点XYZ' in base
        assert '尚未确认' in base


class TestSuggestionGeneration:
    def test_requires_evidence_or_downgraded(self, conn, seeded, fake_ai):
        """
        ★ 核心测试：标了「可直接采纳」但没有依据的建议，必须被强制降级为「需要补充」。
        这条是「不编造」的产品原则在代码层的落点。
        """
        fake_ai.json_response = {
            'overall': '测试',
            'suggestions': [
                {
                    'location': '项目经历 · 第 1 条',
                    'before': '接入支付宝网站支付，实现充值和退款功能',
                    'after': '接入支付宝网站支付，实现 RSA2 验签、幂等充值',
                    'reason': '对应 JD 第 1 条',
                    'grade': 'ok',
                    'evidence': [],          # ← 没依据
                },
            ],
        }
        out = packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        assert out['downgraded'] == 1
        items = packaging.list_suggestions(conn, seeded['resume_id'], seeded['job_id'])
        assert items[0]['grade'] == db.GRADE_NEED
        assert items[0]['missing']          # 必须告诉用户缺什么

    def test_evidence_kept_when_present(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'suggestions': [{
            'location': 'L1', 'before': 'a', 'after': 'b', 'reason': 'r',
            'grade': 'ok', 'evidence': ['岗位要求第 1 条', '项目卡：Video Note'],
        }]}
        out = packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        assert out['downgraded'] == 0
        items = packaging.list_suggestions(conn, seeded['resume_id'], seeded['job_id'])
        assert items[0]['grade'] == db.GRADE_OK
        assert len(items[0]['evidence']) == 2

    def test_risk_grade_strips_after_text(self, conn, seeded, fake_ai):
        """风险题不给「改成什么」，避免用户误采纳。"""
        fake_ai.json_response = {'suggestions': [{
            'location': 'L1', 'before': '优化了性能', 'after': '性能提升 300%',
            'reason': '没有依据', 'grade': 'risk', 'evidence': [],
        }]}
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        items = packaging.list_suggestions(conn, seeded['resume_id'])
        assert items[0]['grade'] == db.GRADE_RISK
        assert items[0]['after_text'] == ''

    def test_regenerate_replaces_previous(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'suggestions': [{
            'location': 'L1', 'before': 'a', 'after': 'b', 'reason': 'r',
            'grade': 'ok', 'evidence': ['e'],
        }]}
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        items = packaging.list_suggestions(conn, seeded['resume_id'])
        assert len(items) == 1, '重新生成应替换而不是堆积'

    def test_empty_suggestions_ok(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'overall': '很匹配', 'suggestions': []}
        out = packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        assert out['total'] == 0

    def test_skips_entries_without_text(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'suggestions': [
            {'location': 'L1', 'before': '', 'after': '', 'grade': 'ok'},
            {'location': 'L2', 'before': 'a', 'after': 'b', 'grade': 'ok',
             'evidence': ['e']},
        ]}
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        assert len(packaging.list_suggestions(conn, seeded['resume_id'])) == 1


class TestDecisions:
    def _make(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'suggestions': [{
            'location': 'L1', 'before': 'a', 'after': 'b', 'reason': 'r',
            'grade': 'ok', 'evidence': ['e'],
        }]}
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        return packaging.list_suggestions(conn, seeded['resume_id'])[0]

    def test_accept(self, conn, seeded, fake_ai):
        s = self._make(conn, seeded, fake_ai)
        out = packaging.decide_suggestion(conn, s['id'], db.SUGGESTION_ACCEPTED)
        assert out['decision'] == 'accepted'

    def test_edit_requires_text(self, conn, seeded, fake_ai):
        s = self._make(conn, seeded, fake_ai)
        try:
            packaging.decide_suggestion(conn, s['id'], db.SUGGESTION_EDITED, '   ')
            assert False, '应该拒绝空文本'
        except ValueError:
            pass

    def test_edit_stores_new_text(self, conn, seeded, fake_ai):
        s = self._make(conn, seeded, fake_ai)
        out = packaging.decide_suggestion(conn, s['id'], db.SUGGESTION_EDITED,
                                          '我自己改的版本')
        assert out['decision'] == 'edited'
        assert out['after_text'] == '我自己改的版本'

    def test_invalid_decision_rejected(self, conn, seeded, fake_ai):
        s = self._make(conn, seeded, fake_ai)
        try:
            packaging.decide_suggestion(conn, s['id'], 'whatever')
            assert False, '非法 decision 应被拒绝'
        except ValueError:
            pass

    def test_decision_is_logged(self, conn, seeded, fake_ai):
        s = self._make(conn, seeded, fake_ai)
        packaging.decide_suggestion(conn, s['id'], db.SUGGESTION_ACCEPTED)
        n = conn.execute(
            "SELECT COUNT(*) FROM change_log WHERE entity='resume_suggestion'").fetchone()[0]
        assert n == 1


class TestApply:
    def test_apply_appends_accepted_only(self, conn, seeded, fake_ai):
        fake_ai.json_response = {'suggestions': [
            {'location': 'L1', 'before': 'a', 'after': 'AAA', 'grade': 'ok',
             'evidence': ['e']},
            {'location': 'L2', 'before': 'b', 'after': 'BBB', 'grade': 'ok',
             'evidence': ['e']},
        ]}
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        items = packaging.list_suggestions(conn, seeded['resume_id'])
        packaging.decide_suggestion(conn, items[0]['id'], db.SUGGESTION_ACCEPTED)
        packaging.decide_suggestion(conn, items[1]['id'], db.SUGGESTION_REJECTED)

        out = packaging.apply_accepted(conn, seeded['resume_id'])
        assert out['applied'] == 1
        content = conn.execute('SELECT content FROM resume WHERE id=?',
                               (seeded['resume_id'],)).fetchone()['content']
        assert 'AAA' in content
        assert 'BBB' not in content

    def test_apply_idempotent_no_duplicate_section(self, conn, seeded, fake_ai):
        """重复应用不应把同一节复制多份。"""
        fake_ai.json_response = {'suggestions': [{
            'location': 'L1', 'before': 'a', 'after': 'AAA', 'grade': 'ok',
            'evidence': ['e']}]}
        packaging.generate_suggestions(conn, seeded['job_id'], seeded['resume_id'])
        s = packaging.list_suggestions(conn, seeded['resume_id'])[0]
        packaging.decide_suggestion(conn, s['id'], db.SUGGESTION_ACCEPTED)

        packaging.apply_accepted(conn, seeded['resume_id'])
        packaging.apply_accepted(conn, seeded['resume_id'])
        content = conn.execute('SELECT content FROM resume WHERE id=?',
                               (seeded['resume_id'],)).fetchone()['content']
        assert content.count('## 已采纳的修改') == 1
