"""API 集成测试：用 FastAPI TestClient 打真实路由（AI 已 mock）。"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app import clients, db
from app.main import app


@pytest.fixture()
def client(user, temp_db):
    """已登录的客户端：走一次真实的「发码 + 登录」，跟浏览器一样。"""
    from tests.conftest import phone_login
    with TestClient(app) as c:
        phone_login(c)
        yield c


class TestBasicEndpoints:
    def test_health(self, client):
        assert client.get('/health').json() == {'ok': True}

    def test_overview_shape(self, client):
        d = client.get('/api/overview').json()
        for k in ('fields_total', 'jobs_total', 'materials_total', 'questions_total',
                  'interviews_total', 'todos', 'pending_total'):
            assert k in d

    def test_settings_never_leaks_key(self, client, monkeypatch):
        from app import config
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-abcdef1234567890')
        d = client.get('/api/settings').json()
        assert 'sk-abcdef1234567890' not in json.dumps(d)
        assert d['keys']['DEEPSEEK_API_KEY']['configured'] is True

    def test_index_serves_html(self, client):
        r = client.get('/')
        assert r.status_code == 200
        assert '求职助手' in r.text

    def test_static_assets(self, client):
        for p in ('/static/css/style.css', '/static/js/app.js', '/static/js/api.js',
                  '/static/js/ui.js', '/static/js/pages/dashboard.js'):
            assert client.get(p).status_code == 200, p


class TestProfileAPI:
    def test_list_and_patch(self, client, conn, temp_db):
        now = temp_db.now_iso()
        cur = conn.execute(
            'INSERT INTO profile_field (section, key, value, status, updated_at) '
            'VALUES (?,?,?,?,?)', ('基本信息', '城市', '宁波', 'unconfirmed', now))
        fid = int(cur.lastrowid)

        d = client.get('/api/profile').json()
        assert d['total'] == 1 and d['unconfirmed'] == 1

        r = client.patch(f'/api/profile/{fid}', json={'value': '杭州'}).json()
        assert r['value'] == '杭州'
        assert r['status'] == 'corrected'      # 改内容自动变已修正

    def test_confirm_and_obsolete(self, client, conn, temp_db):
        now = temp_db.now_iso()
        cur = conn.execute(
            'INSERT INTO profile_field (section, key, value, status, updated_at) '
            'VALUES (?,?,?,?,?)', ('基本信息', '城市', '宁波', 'unconfirmed', now))
        fid = int(cur.lastrowid)
        assert client.post(f'/api/profile/{fid}/confirm').json()['status'] == 'confirmed'
        assert client.post(f'/api/profile/{fid}/obsolete').json()['status'] == 'obsolete'

    def test_patch_missing_field_404(self, client):
        assert client.patch('/api/profile/999999', json={'value': 'x'}).status_code == 404

    def test_invalid_status_400(self, client, conn, temp_db):
        now = temp_db.now_iso()
        cur = conn.execute(
            'INSERT INTO profile_field (section, key, value, status, updated_at) '
            'VALUES (?,?,?,?,?)', ('s', 'k', 'v', 'unconfirmed', now))
        r = client.patch(f'/api/profile/{int(cur.lastrowid)}', json={'status': 'bogus'})
        assert r.status_code == 400

    def test_batch_confirm(self, client, conn, temp_db):
        now = temp_db.now_iso()
        ids = []
        for i in range(3):
            cur = conn.execute(
                'INSERT INTO profile_field (section, key, value, status, updated_at) '
                'VALUES (?,?,?,?,?)', ('s', f'k{i}', 'v', 'unconfirmed', now))
            ids.append(int(cur.lastrowid))
        r = client.post('/api/batch/status',
                        json={'table': 'profile_field', 'ids': ids, 'status': 'confirmed'})
        assert r.json()['updated'] == 3
        assert client.get('/api/profile').json()['unconfirmed'] == 0

    def test_batch_rejects_bad_table(self, client):
        r = client.post('/api/batch/status',
                        json={'table': 'sqlite_master', 'ids': [1], 'status': 'confirmed'})
        assert r.status_code == 400


class TestMaterialAPI:
    def test_upload_rejects_empty(self, client):
        r = client.post('/api/materials',
                        files={'file': ('a.png', io.BytesIO(b''), 'image/png')})
        assert r.status_code == 400

    def test_upload_rejects_bad_type(self, client):
        r = client.post('/api/materials',
                        files={'file': ('a.exe', io.BytesIO(b'MZ'), 'application/x-msdownload')})
        assert r.status_code == 400

    def test_upload_and_parse(self, client, fake_ai):
        fake_ai.text_response = ('{"summary":"简历","kind":"resume","facts":'
                                 '[{"text":"手机 13800000000","locator":"顶部"}]}')
        r = client.post('/api/materials',
                        files={'file': ('r.png', io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 40),
                                        'image/png')})
        assert r.status_code == 200
        mid = r.json()['id']

        p = client.post(f'/api/materials/{mid}/parse').json()
        assert p['ok'] is True and p['facts'] == 1

        d = client.get(f'/api/materials/{mid}').json()
        assert d['status'] == 'done' and len(d['facts']) == 1

        pr = client.post(f'/api/materials/{mid}/promote',
                         json={'fact_ids': [d['facts'][0]['id']]}).json()
        assert pr['added'] == 1

    def test_parse_failure_returns_400_with_reason(self, client, fake_ai):
        from app import clients
        fake_ai.raise_error = clients.ServiceError('视觉模型不可用')
        r = client.post('/api/materials',
                        files={'file': ('r.png', io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 40),
                                        'image/png')})
        mid = r.json()['id']
        resp = client.post(f'/api/materials/{mid}/parse')
        assert resp.status_code == 400
        assert '视觉模型不可用' in resp.json()['detail']
        fake_ai.raise_error = None


class TestJobAPI:
    def test_create_requires_content(self, client):
        assert client.post('/api/jobs', json={}).status_code == 400

    def test_create_and_detail(self, client):
        jid = client.post('/api/jobs', json={
            'company': '蚂蚁集团', 'title': 'AI 工程师',
            'raw_text': '1. 有 Agent 经验', 'city': '杭州',
        }).json()['id']
        d = client.get(f'/api/jobs/{jid}').json()
        assert d['title'] == 'AI 工程师'
        assert d['score'] == -1            # 未分析
        assert d['requirements'] == []

    def test_analyze(self, client, seeded, fake_ai):
        fake_ai.json_response = {
            'gate': [{'text': '本科及以上', 'weight': 'high', 'evidence': '本科',
                      'strength': 'strong', 'strategy': ''}],
            'duty': [{'text': '支付与计费经验', 'weight': 'high',
                      'evidence': 'Video Note 支付宝接入', 'strength': 'strong',
                      'strategy': ''}],
            'plus': [], 'hidden': [],
            'score': 72, 'verdict': '可投，重点补强 Agent 部分',
        }
        r = client.post(f"/api/jobs/{seeded['job_id']}/analyze").json()
        assert r['score'] == 72
        assert r['total'] == 2

        d = client.get(f"/api/jobs/{seeded['job_id']}").json()
        assert d['score'] == 72
        assert len(d['requirements']) == 2
        assert d['requirements'][0]['layer'] == 'gate'

    def test_analyze_without_jd_400(self, client):
        jid = client.post('/api/jobs', json={'title': 'x', 'raw_text': 'y'}).json()['id']
        # 清空原文后再分析
        from app import db as dbm
        with dbm.session() as c:
            c.execute('UPDATE job SET raw_text=? WHERE id=?', ('', jid))
        assert client.post(f'/api/jobs/{jid}/analyze').status_code == 400

    def test_patch_status(self, client, seeded):
        r = client.patch(f"/api/jobs/{seeded['job_id']}", json={'status': 'applied'})
        assert r.json()['status'] == 'applied'
        assert client.patch(f"/api/jobs/{seeded['job_id']}",
                            json={'status': 'bogus'}).status_code == 400


class TestResumeAndPackagingAPI:
    def test_create_resume(self, client):
        r = client.post('/api/resumes', json={'name': '测试版'}).json()
        assert r['id']
        assert client.get(f"/api/resumes/{r['id']}").json()['name'] == '测试版'

    def test_patch_resume(self, client):
        rid = client.post('/api/resumes', json={'name': 'A'}).json()['id']
        r = client.patch(f'/api/resumes/{rid}',
                         json={'content': '# 新内容', 'name': 'B'}).json()
        assert r['name'] == 'B' and r['content'] == '# 新内容'

    def test_packaging_flow(self, client, seeded, fake_ai):
        fake_ai.json_response = {
            'overall': '整体匹配',
            'suggestions': [{
                'location': '项目经历 · 第 1 条',
                'before': '接入支付宝网站支付，实现充值和退款功能',
                'after': '接入支付宝网站支付，实现 RSA2 验签、幂等充值、防篡改与退款',
                'reason': 'JD 第 1 条点名支付与风控',
                'grade': 'ok',
                'evidence': ['岗位要求第 1 条', '项目卡：Video Note'],
            }],
        }
        r = client.post('/api/packaging/generate',
                        json={'job_id': seeded['job_id'],
                              'resume_id': seeded['resume_id']}).json()
        assert r['total'] == 1 and r['ok'] == 1

        lst = client.get('/api/packaging/suggestions',
                         params={'resume_id': seeded['resume_id'],
                                 'job_id': seeded['job_id']}).json()
        assert lst['total'] == 1
        sid = lst['suggestions'][0]['id']
        assert lst['suggestions'][0]['evidence']

        d = client.post(f'/api/packaging/suggestions/{sid}/decide',
                        json={'decision': 'accepted'}).json()
        assert d['decision'] == 'accepted'

        a = client.post('/api/packaging/apply',
                        json={'resume_id': seeded['resume_id']}).json()
        assert a['applied'] == 1

    def test_packaging_no_evidence_downgrade(self, client, seeded, fake_ai):
        """★ 端到端验证「无依据强制降级」。"""
        fake_ai.json_response = {'suggestions': [{
            'location': 'L1', 'before': 'a', 'after': 'b', 'reason': 'r',
            'grade': 'ok', 'evidence': [],
        }]}
        client.post('/api/packaging/generate',
                    json={'job_id': seeded['job_id'], 'resume_id': seeded['resume_id']})
        lst = client.get('/api/packaging/suggestions',
                         params={'resume_id': seeded['resume_id']}).json()
        assert lst['suggestions'][0]['grade'] == 'need'
        assert lst['counts']['need'] == 1


def _seed_questions(conn=None, seeded=None, client=None, fake_ai=None, n=2):
    """通过接口造 n 道题（测试里多处要用）。"""
    fake_ai.json_response = {'questions': [
        {'question': f'这是第 {i} 个足够长的问题吗？', 'standard': 'A', 'probe': '',
         'key_points': [], 'followups': [], 'round_type': 'tech1',
         'is_risk': False, 'risk_note': ''} for i in range(1, n + 1)]}
    client.post('/api/questions/generate',
                json={'job_id': seeded['job_id'], 'resume_id': seeded['resume_id'],
                      'count': n})


class TestQuestionAndPracticeAPI:
    def test_generate_and_list(self, client, seeded, fake_ai):
        fake_ai.json_response = {'questions': [{
            'question': '你说日活 1400 人，怎么统计的？',
            'standard': '这是功能级日活……',
            'probe': '验证数字真伪',
            'key_points': [{'do': True, 'text': '说清口径'}],
            'followups': ['统计周期呢？'],
            'round_type': 'tech1', 'is_risk': False, 'risk_note': '',
        }]}
        r = client.post('/api/questions/generate',
                        json={'job_id': seeded['job_id'],
                              'resume_id': seeded['resume_id'], 'count': 1}).json()
        assert r['total'] == 1

        lst = client.get('/api/questions',
                         params={'job_id': seeded['job_id']}).json()
        assert lst['total'] == 1
        assert lst['questions'][0]['standard']
        assert lst['questions'][0]['followups'] == ['统计周期呢？']
        assert lst['rounds']['tech1'] == '技术一面'

    def test_question_status(self, client, seeded, fake_ai):
        fake_ai.json_response = {'questions': [{
            'question': '这是一个足够长的问题吗？', 'standard': 'A', 'probe': 'P',
            'key_points': [], 'followups': [], 'round_type': 'tech1',
            'is_risk': False, 'risk_note': ''}]}
        client.post('/api/questions/generate',
                    json={'job_id': seeded['job_id'],
                          'resume_id': seeded['resume_id'], 'count': 1})
        qid = client.get('/api/questions',
                         params={'job_id': seeded['job_id']}).json()['questions'][0]['id']
        assert client.patch(f'/api/questions/{qid}',
                            json={'status': 'review'}).json()['status'] == 'review'
        assert client.patch(f'/api/questions/{qid}',
                            json={'status': 'bad'}).status_code == 400

    def test_practice_flow(self, client, seeded, fake_ai):
        fake_ai.json_response = {'questions': [{
            'question': '请介绍一下你的项目经历？', 'standard': 'A1', 'probe': 'P1',
            'key_points': [], 'followups': [], 'round_type': 'tech1',
            'is_risk': False, 'risk_note': ''}]}
        client.post('/api/questions/generate',
                    json={'job_id': seeded['job_id'],
                          'resume_id': seeded['resume_id'], 'count': 1})
        qid = client.get('/api/questions',
                         params={'job_id': seeded['job_id']}).json()['questions'][0]['id']

        s = client.post('/api/practice/start',
                        json={'job_id': seeded['job_id'],
                              'resume_id': seeded['resume_id']}).json()
        assert s['total'] == 1

        client.post('/api/practice/answer', json={
            'session_id': s['session_id'], 'question_id': qid,
            'answer': '我的回答', 'input_mode': 'voice', 'looked_first': False})

        out = client.post(f"/api/practice/{s['session_id']}/finish").json()
        assert out['answered'] == 1 and out['looked'] == 0

        # 答过之后状态应从未练变为已练
        assert client.get('/api/questions',
                          params={'job_id': seeded['job_id']}).json()[
            'questions'][0]['status'] in ('seen', 'mastered', 'review')

    def test_practice_requires_bank(self, client, seeded):
        assert client.post('/api/practice/start',
                           json={'job_id': seeded['job_id'],
                                 'resume_id': seeded['resume_id']}).status_code == 400


class TestInterviewAPI:
    def _make_questions(self, client, seeded, fake_ai, n=2):
        fake_ai.json_response = {'questions': [
            {'question': f'这是第 {i} 个足够长的问题吗？', 'standard': f'A{i}', 'probe': 'P',
             'key_points': [], 'followups': [], 'round_type': 'tech1',
             'is_risk': False, 'risk_note': ''} for i in range(1, n + 1)]}
        client.post('/api/questions/generate',
                    json={'job_id': seeded['job_id'],
                          'resume_id': seeded['resume_id'], 'count': n})
        return [q['id'] for q in client.get('/api/questions',
                                            params={'job_id': seeded['job_id']}).json()['questions']]

    def test_full_interview_flow(self, client, seeded, fake_ai):
        self._make_questions(client, seeded, fake_ai, 2)
        bank = {q['question'] for q in client.get(
            '/api/questions', params={'job_id': seeded['job_id']}).json()['questions']}

        s = client.post('/api/interviews', json={
            'job_id': seeded['job_id'], 'resume_id': seeded['resume_id'],
            'round_type': 'tech1', 'pressure': 'strict'}).json()
        # 第一题必须来自题库，且**不能**把标准答案一起吐给前端（模拟面试不给答案）
        assert s['question'] in bank
        assert 'standard' not in json.dumps(s, ensure_ascii=False)

        iid = s['interview_id']
        fake_ai.json_response = {'action': 'next', 'text': ''}
        r1 = client.post(f'/api/interviews/{iid}/answer',
                         json={'text': '第一个回答', 'input_mode': 'voice'}).json()
        assert r1['type'] == 'next'

        fake_ai.json_response = {
            'scores': {'tech': 6, 'structure': 6, 'honesty': 8, 'tradeoff': 5,
                       'communication': 6},
            'summary': '总评', 'highlights': [], 'dangers': [], 'advice': ['a']}
        r2 = client.post(f'/api/interviews/{iid}/answer',
                         json={'text': '第二个回答'}).json()
        assert r2.get('finished') is True

        d = client.get(f'/api/interviews/{iid}').json()
        assert d['status'] == 'finished'
        assert sum(d['score_json'].values()) == 31
        assert len(d['turns']) == 2

        lst = client.get('/api/interviews').json()
        assert len(lst['interviews']) == 1
        assert lst['interviews'][0]['total_score'] == 31

    def test_interview_requires_bank(self, client, seeded):
        assert client.post('/api/interviews', json={
            'job_id': seeded['job_id'], 'resume_id': seeded['resume_id'],
        }).status_code == 400


class TestApplicationAPI:
    def test_upsert_and_stats(self, client, seeded):
        r = client.post('/api/applications',
                        json={'job_id': seeded['job_id'], 'status': 'applied'}).json()
        assert r['status'] == 'applied'

        # 再次提交应更新而不是新建
        client.post('/api/applications',
                    json={'job_id': seeded['job_id'], 'status': 'interview'})
        d = client.get('/api/applications').json()
        assert len(d['applications']) == 1
        assert d['applications'][0]['status'] == 'interview'
        assert d['stats']['interviewed'] == 1


class TestVoiceAPI:
    def test_status(self, client):
        d = client.get('/api/voice/status').json()
        assert 'deepseek' in d and 'dashscope' in d and 'models' in d

    def test_tts(self, client, fake_ai):
        r = client.post('/api/voice/tts', json={'text': '你好'})
        assert r.status_code == 200
        assert r.headers['content-type'].startswith('audio/')

    def test_tts_failure_400(self, client, fake_ai):
        from app import clients
        fake_ai.raise_error = clients.ServiceError('合成服务不可用')
        r = client.post('/api/voice/tts', json={'text': '你好'})
        assert r.status_code == 400
        assert '合成服务不可用' in r.json()['detail']
        fake_ai.raise_error = None

    def test_asr(self, client, fake_ai):
        fake_ai.text_response = '你好，我是张三'
        r = client.post('/api/voice/asr',
                        files={'file': ('a.wav', io.BytesIO(b'RIFF0000WAVE'), 'audio/wav')})
        assert r.json()['text'] == '你好，我是张三'

    def test_asr_empty_400(self, client):
        r = client.post('/api/voice/asr',
                        files={'file': ('a.wav', io.BytesIO(b''), 'audio/wav')})
        assert r.status_code == 400


class TestDeleteAndCreate:
    """删除/新建类接口：这些按钮点下去必须真的生效（用户报过"删除没反应"）。"""

    def test_delete_profile_field(self, client, seeded):
        fields = client.get('/api/profile').json()['sections'][0]['fields']
        fid = fields[0]['id']
        before = client.get('/api/profile').json()['total']
        assert client.delete(f'/api/profile/{fid}').json()['ok'] is True
        assert client.get('/api/profile').json()['total'] == before - 1
        assert client.delete('/api/profile/999999').status_code == 404

    def test_delete_project_point(self, client, seeded):
        proj = client.get('/api/projects').json()['projects'][0]
        pid = proj['points'][0]['id']
        n = len(proj['points'])
        assert client.delete(f'/api/points/{pid}').json()['ok'] is True
        after = client.get('/api/projects').json()['projects'][0]
        assert len(after['points']) == n - 1

    def test_create_and_delete_project(self, client):
        r = client.post('/api/projects', json={'name': '测试项目', 'role': '独立开发'})
        assert r.status_code == 200
        pid = r.json()['id']
        # 重名要挡住
        assert client.post('/api/projects', json={'name': '测试项目'}).status_code == 400
        names = [p['name'] for p in client.get('/api/projects').json()['projects']]
        assert '测试项目' in names
        assert client.delete(f'/api/projects/{pid}').json()['ok'] is True
        names = [p['name'] for p in client.get('/api/projects').json()['projects']]
        assert '测试项目' not in names

    def test_delete_material_removes_facts(self, client, seeded):
        import io as _io
        fake = ('{"summary":"简历","kind":"resume","facts":'
                '[{"text":"一条会被删掉的事实","locator":"顶部"}]}')
        # 用打桩的方式建一条材料
        r = client.post('/api/materials',
                        files={'file': ('r.png', _io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 40),
                                        'image/png')})
        assert r.status_code == 200
        mid = r.json()['id']
        out = client.delete(f'/api/materials/{mid}').json()
        assert out['ok'] is True
        ids = [m['id'] for m in client.get('/api/materials').json()['materials']]
        assert mid not in ids
        assert client.delete(f'/api/materials/{mid}').status_code == 404

    def test_delete_project_keeps_materials(self, client, seeded):
        """删项目不能把材料也删了——材料是原始证据。"""
        r = client.post('/api/projects', json={'name': '带材料的项目'})
        pid = r.json()['id']
        before = len(client.get('/api/materials').json()['materials'])
        client.delete(f'/api/projects/{pid}')
        assert len(client.get('/api/materials').json()['materials']) == before


class TestStreamingVoice:
    """
    ★ 流式语音合成：对话的"接话感"全靠它。

    老的整段接口要等模型把整句话合成完、再下载整个文件，客户端才听到第一个字
    （实测 2.2s 起）。流式接口首字节 ~0.4s，快 5 倍以上。
    """

    def test_stream_endpoint_exists(self, client, fake_ai):
        fake_ai.raise_error = clients.ServiceError('测试里不真连语音服务')
        r = client.post('/api/voice/tts-stream', json={'text': '你好'})
        # 已经开流了改不了状态码，但接口必须存在且不 5xx
        assert r.status_code in (200, 400)

    def test_stream_returns_pcm_with_sample_rate_header(self, client, monkeypatch):
        async def fake_stream(text, voice='', sample_rate=24000):
            yield b'\x00\x01' * 100
        monkeypatch.setattr(clients, 'synthesize_stream', fake_stream)
        r = client.post('/api/voice/tts-stream', json={'text': '你好'})
        assert r.status_code == 200
        assert r.headers.get('x-sample-rate') == '24000'
        assert len(r.content) == 200


class TestJobFromScreenshot:
    """
    ★ 回归：上传 JD 截图后，岗位要求必须真的被解析出来。

    踩过的坑：`create_job` 只把 material_id 存下来、**从来不解析**，
    界面上却写着"保存后会一起解析 JD 内容"。结果是 job.raw_text 为空，
    做匹配度分析时拿不到岗位要求（直接报"没有 JD 原文"，或者只拿一句摘要充数）。
    """

    def _upload(self, client, name='jd.png'):
        return client.post('/api/materials?doc_kind=jd',
                           files={'file': (name, io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 60),
                                           'image/png')}).json()['id']

    def test_parse_jd_endpoint_returns_raw_text(self, client, fake_ai):
        fake_ai.text_response = json.dumps({
            'raw_text': '岗位职责：1. 设计 AI 产品方案\n任职要求：1. 熟悉大模型',
            'company': '蚂蚁集团', 'title': 'AI 产品经理', 'city': '杭州', 'salary': '30-60K',
            'summary': '一份 JD', 'responsibilities': ['设计 AI 产品方案'],
            'requirements': ['熟悉大模型'],
        }, ensure_ascii=False)
        mid = self._upload(client)
        out = client.post(f'/api/materials/{mid}/parse-jd').json()
        assert out['ok'] is True
        assert '熟悉大模型' in out['raw_text']
        assert out['company'] == '蚂蚁集团'
        assert out['title'] == 'AI 产品经理'
        assert out['city'] == '杭州'
        assert out['facts'] == 2

    def test_create_job_parses_material_when_no_text(self, client, fake_ai):
        """表单没给原文 + 材料没解析过 → 建岗位时自动解析并回填。"""
        fake_ai.text_response = json.dumps({
            'raw_text': '任职要求：1. 三年以上经验\n2. 熟悉分布式',
            'company': '', 'title': '', 'city': '', 'salary': '',
            'summary': 'JD', 'responsibilities': [], 'requirements': ['三年以上经验'],
        }, ensure_ascii=False)
        mid = self._upload(client)
        r = client.post('/api/jobs', json={'company': '某公司', 'title': '后端', 'material_id': mid})
        assert r.status_code == 200
        assert r.json()['raw_text_chars'] > 0
        job = client.get(f"/api/jobs/{r.json()['id']}").json()
        assert '熟悉分布式' in job['raw_text'], 'JD 原文没落到岗位上'

    def test_create_job_skips_reparse_when_material_parsed(self, client, fake_ai):
        """材料已经解析过就别再花 13 秒重解析一遍。"""
        fake_ai.text_response = json.dumps({
            'raw_text': '任职要求：熟悉 Python', 'company': '', 'title': '',
            'city': '', 'salary': '', 'summary': 'JD',
            'responsibilities': [], 'requirements': ['熟悉 Python'],
        }, ensure_ascii=False)
        mid = self._upload(client)
        client.post(f'/api/materials/{mid}/parse-jd')
        before = len(fake_ai.calls)
        r = client.post('/api/jobs', json={'company': 'X', 'title': 'Y', 'material_id': mid})
        assert r.json()['parsed'] is False, '不该重复解析'
        assert len(fake_ai.calls) == before

    def test_analyze_works_with_parsed_jd(self, client, fake_ai):
        fake_ai.text_response = json.dumps({
            'raw_text': '任职要求：熟悉 Python、分布式系统',
            'company': 'X', 'title': 'Y', 'city': '', 'salary': '', 'summary': 'JD',
            'responsibilities': ['负责后端'], 'requirements': ['熟悉 Python'],
        }, ensure_ascii=False)
        mid = self._upload(client)
        jid = client.post('/api/jobs', json={'company': 'X', 'title': 'Y',
                                             'material_id': mid}).json()['id']
        fake_ai.json_response = {'gate': [{'text': '熟悉 Python', 'weight': 'high',
                                           'strength': 'strong', 'evidence': '项目里有'}],
                                 'duty': [], 'plus': [], 'hidden': [],
                                 'score': 70, 'verdict': '值得投'}
        out = client.post(f'/api/jobs/{jid}/analyze').json()
        assert out['score'] == 70
        assert client.get(f'/api/jobs/{jid}').json()['requirements'][0]['text'] == '熟悉 Python'


class TestAnalyzeUsesResume:
    """
    ★ 回归：岗位匹配度要**对着简历**算，不能只对着档案事实库算。

    踩过的坑：analyze 只用 build_fact_base（档案字段 + 项目要点），
    简历里改过、新写的表述它完全看不见——用户新建/更新了简历再分析，
    结果一点不变，看着就像"没用我的简历"。
    """

    def _job_with_jd(self, client):
        return client.post('/api/jobs', json={
            'company': '某公司', 'title': '后端工程师',
            'raw_text': '任职要求：1. 三年以上 Go 经验\n2. 熟悉分布式系统'}).json()['id']

    def test_resume_content_reaches_the_model(self, client, seeded, fake_ai):
        jid = self._job_with_jd(client)
        rid = client.post('/api/resumes', json={
            'name': '测试简历', 'content': '我写过一套独特的风控引擎，代号 SENTINEL-X'}).json()['id']
        fake_ai.json_response = {'gate': [], 'duty': [], 'plus': [], 'hidden': [],
                                 'score': 60, 'verdict': 'ok'}
        out = client.post(f'/api/jobs/{jid}/analyze', json={'resume_id': rid}).json()
        assert out['resume_id'] == rid
        prompt = json.dumps([c for c in fake_ai.calls if c[0] == 'json'][-1],
                            ensure_ascii=False)
        assert 'SENTINEL-X' in prompt, '简历内容没进分析上下文'

    def test_defaults_to_latest_resume(self, client, seeded, fake_ai):
        jid = self._job_with_jd(client)
        client.post('/api/resumes', json={'name': '旧简历', 'content': '旧内容 OLD-ONE'})
        newest = client.post('/api/resumes',
                             json={'name': '新简历', 'content': '新内容 NEW-TWO'}).json()['id']
        fake_ai.json_response = {'gate': [], 'duty': [], 'plus': [], 'hidden': [],
                                 'score': 60, 'verdict': 'ok'}
        out = client.post(f'/api/jobs/{jid}/analyze', json={}).json()
        assert out['resume_id'] == newest, '不指定时应该用最近更新的那份'

    def test_records_which_resume_was_used(self, client, seeded, fake_ai):
        jid = self._job_with_jd(client)
        rid = client.post('/api/resumes', json={'name': '记录用简历', 'content': 'x'}).json()['id']
        fake_ai.json_response = {'gate': [], 'duty': [], 'plus': [], 'hidden': [],
                                 'score': 60, 'verdict': 'ok'}
        client.post(f'/api/jobs/{jid}/analyze', json={'resume_id': rid})
        job = client.get(f'/api/jobs/{jid}').json()
        assert job['analyzed_resume_id'] == rid
        assert job['analyzed_resume_name'] == '记录用简历'

    def test_reports_resume_stats(self, client, seeded, fake_ai):
        """返回里要说清"用了哪份、多少字"，界面才能显示出来。"""
        jid = self._job_with_jd(client)
        rid = client.post('/api/resumes',
                          json={'name': '统计用简历', 'content': '一二三四五'}).json()['id']
        fake_ai.json_response = {'gate': [], 'duty': [], 'plus': [], 'hidden': [],
                                 'score': 55, 'verdict': 'ok'}
        out = client.post(f'/api/jobs/{jid}/analyze', json={'resume_id': rid}).json()
        assert out['score'] == 55
        assert out['resume_name'] == '统计用简历'
        assert out['resume_chars'] == 5


class TestResumeJobLink:
    """简历可以指定/取消目标岗位——包装建议和题库都靠这个关联定向。"""

    def test_link_and_unlink(self, client, seeded):
        rid = client.post('/api/resumes', json={'name': '通用简历'}).json()['id']
        assert client.get('/api/resumes').json()['resumes'][0]['job_id'] is None

        linked = client.patch(f'/api/resumes/{rid}',
                              json={'job_id': seeded['job_id']}).json()
        assert linked['job_id'] == seeded['job_id']

        # 显式传 null = 取消关联；不传 = 不动它
        assert client.patch(f'/api/resumes/{rid}', json={'job_id': None}).json()['job_id'] is None
        client.patch(f'/api/resumes/{rid}', json={'job_id': seeded['job_id']})
        assert client.patch(f'/api/resumes/{rid}',
                            json={'name': '只改名'}).json()['job_id'] == seeded['job_id']

    def test_rejects_unknown_job(self, client, seeded):
        rid = client.post('/api/resumes', json={'name': 'x'}).json()['id']
        assert client.patch(f'/api/resumes/{rid}', json={'job_id': 999999}).status_code == 400


class TestChatReadsChosenResume:
    """
    ★ 回归：对话助手必须读**指定的那份简历**，而不是只有材料里那批事实。

    踩过的坑：前端一直传 resume_id=null，build_context 里的简历分支就永远不执行。
    助手看到的只有材料事实（也就是最早那张简历截图的解析结果），
    所以怎么改都像在改最开始那一份。
    """

    def test_chosen_resume_goes_into_context(self, client, seeded, fake_ai):
        rid = client.post('/api/resumes', json={
            'name': '被选中的简历', 'content': '第一段写的是 ROCKET-PANDA 项目'}).json()['id']
        fake_ai.json_response = {'reply': '好', 'questions': [], 'draft_resume': ''}
        client.post('/api/resume-chat',
                    json={'messages': [{'role': 'user', 'content': '看看我的简历'}],
                          'resume_id': rid})
        prompt = json.dumps([c for c in fake_ai.calls if c[0] == 'json'][-1],
                            ensure_ascii=False)
        assert 'ROCKET-PANDA' in prompt, '选中的简历没进上下文'
        assert '被选中的简历' in prompt, '没说清在改哪一份'

    def test_session_remembers_the_choice(self, client, seeded, fake_ai):
        rid = client.post('/api/resumes', json={'name': '记住我', 'content': 'x'}).json()['id']
        fake_ai.json_response = {'reply': '好', 'questions': [], 'draft_resume': ''}
        client.post('/api/resume-chat',
                    json={'messages': [{'role': 'user', 'content': 'hi'}], 'resume_id': rid})
        assert client.get('/api/resume-chat/session').json()['resume_id'] == rid

    def test_switching_resume_updates_session(self, client, seeded, fake_ai):
        a = client.post('/api/resumes', json={'name': 'A', 'content': 'A 内容'}).json()['id']
        b = client.post('/api/resumes', json={'name': 'B', 'content': 'B 内容'}).json()['id']
        fake_ai.json_response = {'reply': '好', 'questions': [], 'draft_resume': ''}
        client.post('/api/resume-chat',
                    json={'messages': [{'role': 'user', 'content': 'hi'}], 'resume_id': a})
        client.post('/api/resume-chat',
                    json={'messages': [{'role': 'user', 'content': 'hi'}], 'resume_id': b})
        assert client.get('/api/resume-chat/session').json()['resume_id'] == b

    def test_lists_other_resumes_so_it_can_ask(self, client, seeded, fake_ai):
        """库里还有别的版本时要告诉助手，用户说"换一份"它才知道有什么可换。"""
        client.post('/api/resumes', json={'name': '别的版本 ZZZ', 'content': 'x'})
        rid = client.post('/api/resumes', json={'name': '当前这份', 'content': 'y'}).json()['id']
        fake_ai.json_response = {'reply': '好', 'questions': [], 'draft_resume': ''}
        client.post('/api/resume-chat',
                    json={'messages': [{'role': 'user', 'content': 'hi'}], 'resume_id': rid})
        prompt = json.dumps([c for c in fake_ai.calls if c[0] == 'json'][-1],
                            ensure_ascii=False)
        assert '别的版本 ZZZ' in prompt


class TestOverviewMetricsAreReal:
    """
    ★ 工作台上的每个数字都必须是真的。

    踩过的坑：「已掌握题目」用 total - review 算，26 道题全是未练却显示「26/26 已掌握」；
    「档案完成度」在档案模块取消后还留着；「面试场次」把只看了个开场就退出的也算进去。
    """

    def test_mastered_counts_actual_status(self, client, seeded, fake_ai):
        jid = seeded['job_id']
        fake_ai.json_response = {'questions': [
            {'question': f'这是第 {i} 个足够长的问题吗？', 'standard': 'A', 'probe': '',
             'key_points': [], 'followups': [], 'round_type': 'tech1',
             'is_risk': False, 'risk_note': ''} for i in range(3)]}
        client.post('/api/questions/generate',
                    json={'job_id': jid, 'resume_id': seeded['resume_id'], 'count': 3})
        ov = client.get('/api/overview').json()
        assert ov['questions_total'] == 3
        assert ov['questions_mastered'] == 0, '一道都没练，不能显示已掌握'

        qid = client.get('/api/questions', params={'job_id': jid}).json()['questions'][0]['id']
        client.patch(f'/api/questions/{qid}', json={'status': 'mastered'})
        assert client.get('/api/overview').json()['questions_mastered'] == 1

    def test_interview_count_excludes_abandoned(self, client, seeded, fake_ai):
        _seed_questions(conn=None, seeded=seeded, client=client, fake_ai=fake_ai, n=2)
        # 开一场但一个字都不答
        client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                             'resume_id': seeded['resume_id']})
        ov = client.get('/api/overview').json()
        assert ov['interviews_total'] == 1
        assert ov['interviews_finished'] == 0
        assert ov['interviews_abandoned'] == 1

    def test_cleanup_only_removes_untouched(self, client, seeded, fake_ai):
        _seed_questions(conn=None, seeded=seeded, client=client, fake_ai=fake_ai, n=4)
        # 一场一个字没答
        client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                             'resume_id': seeded['resume_id']})
        # 一场答了
        s = client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                                 'resume_id': seeded['resume_id']}).json()
        fake_ai.json_response = {'action': 'next', 'text': ''}
        client.post(f"/api/interviews/{s['interview_id']}/answer",
                    json={'text': '我答了内容'})
        out = client.delete('/api/interviews/abandoned').json()
        assert out['removed'] == 1, '只该清掉没答过的那场'
        left = client.get('/api/interviews').json()['interviews']
        assert len(left) == 1

    def test_todo_no_longer_points_to_removed_module(self, client, seeded):
        """待办里不能再出现指向已取消模块（#/mine/profile）的链接。"""
        for t in client.get('/api/overview').json()['todos']:
            assert '#/mine/profile' not in t['action'], f'待办指向了已取消的模块：{t}'


class TestSidebarAndReviewed:
    """
    ★ 回归：侧边栏页脚的数字要真、待复盘要能清掉。

    两个坑：
    1. 页脚显示的是 profile_field 里没确认的字段数——「我的档案」模块取消后，
       那个数字既没意义也没地方处理它（工作台卡片改了，页脚漏改）。
    2. `reviewed_at` 字段**从来没有被写过**，所以「待复盘」只会越涨越多、永远清不掉。
    """

    def test_todos_have_short_labels(self, client, seeded):
        """侧边栏要用短标签，每条都得有，且 action 是真路由。"""
        for t in client.get('/api/overview').json()['todos']:
            assert t.get('short'), f'待办缺 short 标签：{t}'
            assert t['action'].startswith('#/')

    def test_finished_interview_counts_as_unreviewed_then_clears(self, client, seeded, fake_ai):
        _seed_questions(seeded=seeded, client=client, fake_ai=fake_ai, n=2)
        s = client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                                 'resume_id': seeded['resume_id']}).json()
        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': ''}
        client.post(f"/api/interviews/{s['interview_id']}/answer", json={'text': '回答'})
        fake_ai.json_response = {'scores': {'tech': 6}, 'summary': 'x', 'highlights': [],
                                 'dangers': [], 'advice': []}
        client.post(f"/api/interviews/{s['interview_id']}/finish")

        ov = client.get('/api/overview').json()
        assert ov['interviews_unreviewed'] == 1
        assert any(t['type'] == 'interview' for t in ov['todos'])

        assert client.post(f"/api/interviews/{s['interview_id']}/reviewed").json()['ok'] is True
        assert client.get('/api/overview').json()['interviews_unreviewed'] == 0

    def test_mark_reviewed_is_idempotent(self, client, seeded, fake_ai):
        _seed_questions(seeded=seeded, client=client, fake_ai=fake_ai, n=2)
        s = client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                                 'resume_id': seeded['resume_id']}).json()
        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': ''}
        client.post(f"/api/interviews/{s['interview_id']}/answer", json={'text': '回答'})
        fake_ai.json_response = {'scores': {'tech': 6}, 'summary': 'x', 'highlights': [],
                                 'dangers': [], 'advice': []}
        client.post(f"/api/interviews/{s['interview_id']}/finish")
        iid = s['interview_id']
        first = client.post(f'/api/interviews/{iid}/reviewed').json()
        assert first['ok'] is True
        assert client.post(f'/api/interviews/{iid}/reviewed').json()['ok'] is True
        assert client.post('/api/interviews/999999/reviewed').status_code == 404


class TestCleanupMatchesWhatUiShows:
    """
    ★ 回归：清理按钮删掉的条数，必须和界面上说的条数一致。

    踩过的坑：界面把「running 且无评分」都算成"半途退出 N 场"，
    但清理接口只删「一个字都没答过」的。你要是那 5 场都答过一点，
    点清理删掉 0 条、界面纹丝不动——看着就是"清理不生效"。
    """

    def test_abandoned_stat_matches_cleanup(self, client, seeded, fake_ai):
        _seed_questions(seeded=seeded, client=client, fake_ai=fake_ai, n=6)
        # 一场一个字没答
        client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                             'resume_id': seeded['resume_id']})
        # 一场答了一句但没结束
        s = client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                                 'resume_id': seeded['resume_id']}).json()
        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': ''}
        client.post(f"/api/interviews/{s['interview_id']}/answer", json={'text': '我答了'})

        ov = client.get('/api/overview').json()
        assert ov['interviews_abandoned'] == 1, '统计口径应该是"一个字都没答过"'

        removed = client.delete('/api/interviews/abandoned').json()['removed']
        assert removed == ov['interviews_abandoned'], \
            f'界面说 {ov["interviews_abandoned"]} 场，实际删了 {removed} 场'
        # 答过的那场必须留着
        left = client.get('/api/interviews').json()['interviews']
        assert len(left) == 1
        assert left[0]['answered_turns'] == 1

    def test_list_exposes_answered_turns(self, client, seeded, fake_ai):
        """列表要给出"答过几题"，界面才能自己分清两种半途退出。"""
        _seed_questions(seeded=seeded, client=client, fake_ai=fake_ai, n=4)
        s = client.post('/api/interviews', json={'job_id': seeded['job_id'],
                                                 'resume_id': seeded['resume_id']}).json()
        fake_ai.json_response = {'action': 'next', 'text': '', 'ack': ''}
        client.post(f"/api/interviews/{s['interview_id']}/answer", json={'text': '答了'})
        item = client.get('/api/interviews').json()['interviews'][0]
        assert item['answered_turns'] == 1
        assert item['turns'] >= 1
