"""核心层测试：配置、数据库、导入器、材料。"""
from __future__ import annotations

import importlib

import pytest

from app import clients, config, db, importer, materials


# ══════════════ 配置 ══════════════

class TestConfig:
    def test_key_status_never_leaks_full_key(self, monkeypatch):
        """密钥状态接口只能返回掩码，绝不能回显完整 key。"""
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', 'sk-1234567890abcdef')
        status = config.key_status()
        assert status['DEEPSEEK_API_KEY']['configured'] is True
        assert 'sk-1234567890abcdef' not in status['DEEPSEEK_API_KEY']['masked']
        assert '…' in status['DEEPSEEK_API_KEY']['masked']

    def test_missing_keys_reported(self, monkeypatch):
        monkeypatch.setitem(config.CONFIG, 'DEEPSEEK_API_KEY', '')
        monkeypatch.setitem(config.CONFIG, 'DASHSCOPE_API_KEY', 'x')
        assert config.missing_keys() == ['DEEPSEEK_API_KEY']

    def test_empty_env_value_falls_through(self, tmp_path):
        """空值不应该覆盖已有配置（否则用户清空 .env 会把 key 弄丢）。"""
        f = tmp_path / '.env'
        f.write_text('FOO=\nBAR=value\n', encoding='utf-8')
        parsed = config._parse_env_file(f)
        assert 'FOO' not in parsed
        assert parsed['BAR'] == 'value'

    def test_quotes_stripped(self, tmp_path):
        f = tmp_path / '.env'
        f.write_text('A="quoted"\nB=\'single\'\n', encoding='utf-8')
        parsed = config._parse_env_file(f)
        assert parsed['A'] == 'quoted'
        assert parsed['B'] == 'single'


# ══════════════ JSON 抢救 ══════════════

class TestParseJsonLoose:
    def test_plain(self):
        assert clients.parse_json_loose('{"a":1}') == {'a': 1}

    def test_fenced(self):
        assert clients.parse_json_loose('```json\n{"a":1}\n```') == {'a': 1}

    def test_with_prose(self):
        assert clients.parse_json_loose('好的，结果如下：{"a":1} 以上。') == {'a': 1}

    def test_truncated_array_salvages_complete_objects(self):
        """输出被截断时，应抢救出已完整的对象而不是整体失败。"""
        text = '{"questions":[{"question":"Q1","standard":"A1"},{"question":"Q2","stan'
        out = clients.parse_json_loose(text)
        assert isinstance(out, list)
        assert out[0]['question'] == 'Q1'

    def test_truncated_no_complete_object_raises(self):
        with pytest.raises(clients.ServiceError):
            clients.parse_json_loose('{"questions":[{"question":"Q1","stan')

    def test_empty_raises(self):
        with pytest.raises(clients.ServiceError):
            clients.parse_json_loose('   ')


# ══════════════ 数据库 ══════════════

class TestDatabase:
    def test_all_tables_created(self, temp_db):
        with temp_db.session() as c:
            names = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ('profile_field', 'project', 'project_point', 'material',
                  'material_fact', 'job', 'job_requirement', 'resume',
                  'resume_suggestion', 'question', 'practice_session',
                  'practice_answer', 'interview', 'interview_turn',
                  'application', 'change_log'):
            assert t in names, f'缺少表 {t}'

    def test_stats_empty(self, temp_db):
        with temp_db.session() as c:
            s = temp_db.stats(c)
        assert s['fields_total'] == 0
        assert s['questions_total'] == 0

    def test_foreign_key_cascade(self, conn, temp_db):
        """删项目应级联删要点。"""
        now = temp_db.now_iso()
        cur = conn.execute(
            'INSERT INTO project (name, status, updated_at) VALUES (?,?,?)',
            ('P', 'unconfirmed', now))
        pid = int(cur.lastrowid)
        conn.execute(
            'INSERT INTO project_point (project_id, text, updated_at) VALUES (?,?,?)',
            (pid, 'x', now))
        conn.execute('DELETE FROM project WHERE id=?', (pid,))
        assert conn.execute('SELECT COUNT(*) FROM project_point').fetchone()[0] == 0

    def test_change_log_roundtrip(self, conn, temp_db):
        temp_db.log_change(conn, 'profile_field', 1, 'correct', 'old', 'new')
        row = conn.execute('SELECT * FROM change_log ORDER BY id DESC LIMIT 1').fetchone()
        assert row['before'] == 'old' and row['after'] == 'new'


# ══════════════ 导入器 ══════════════

class TestImporter:
    def test_idempotent(self, conn):
        """重复导入不应产生重复行。"""
        r1 = importer.run()
        r2 = importer.run()
        assert r2['fields_added'] == 0
        assert r2['projects_added'] == 0
        assert r2['stats']['fields_total'] == r1['stats']['fields_total']

    def test_imports_are_never_preconfirmed(self, conn):
        """导入的一切必须是「待确认」——这是产品原则，不能被绕过。"""
        importer.run()
        with db.session() as c:
            pending = c.execute(
                "SELECT COUNT(*) FROM profile_field WHERE status='unconfirmed'").fetchone()[0]
            total = c.execute('SELECT COUNT(*) FROM profile_field').fetchone()[0]
        assert total > 0
        assert pending == total

    def test_meta_sections_not_imported(self, conn):
        """分析结论（高危表述/投递定位/冲突清单）不该混进档案。"""
        importer.run()
        with db.session() as c:
            sections = [r[0] for r in c.execute(
                'SELECT DISTINCT section FROM profile_field')]
        for bad in ('高危表述', '投递定位', '冲突', '待补'):
            assert not any(bad in s for s in sections), f'{bad} 不该出现在档案里'

    def test_non_project_files_skipped(self, conn):
        """运营数据这类文件不该被当成项目。"""
        r = importer.run()
        with db.session() as c:
            names = [x[0] for x in c.execute('SELECT name FROM project')]
        assert not any('运营数据' in n for n in names)


# ══════════════ 材料 ══════════════

class TestMaterials:
    def test_reject_unsupported_type(self):
        with pytest.raises(ValueError):
            materials.save_upload('a.exe', b'x')

    def test_create_and_parse_with_fake_vision(self, conn, temp_db, fake_ai, monkeypatch):
        importlib.reload(materials)
        monkeypatch.setattr(materials.clients, 'read_image', fake_ai.read_image)
        fake_ai.text_response = (
            '{"summary":"一份简历","kind":"resume","facts":['
            '{"text":"手机号 13800000000","locator":"顶部"},'
            '{"text":"查询模块复用率达 90%","locator":"第 2 页"}]}')

        mid = materials.create_material(conn, 'r.png', b'\x89PNG\r\n\x1a\n' + b'0' * 50)
        out = materials.parse_material(conn, mid)
        assert out['ok'] is True
        assert out['facts'] == 2

        detail = materials.material_detail(conn, mid)
        assert len(detail['facts']) == 2
        assert detail['facts'][1]['locator'] == '第 2 页'
        assert detail['status'] == db.MATERIAL_DONE

    def test_parse_failure_is_recorded(self, conn, fake_ai, monkeypatch):
        """解析失败必须写进 error 字段，不能静默。"""
        importlib.reload(materials)
        monkeypatch.setattr(materials.clients, 'read_image', fake_ai.read_image)
        fake_ai.raise_error = clients.ServiceError('模型抽风了')

        mid = materials.create_material(conn, 'r.png', b'\x89PNG\r\n\x1a\n' + b'0' * 50)
        out = materials.parse_material(conn, mid)
        assert out['ok'] is False
        assert '模型抽风了' in out['error']

        row = conn.execute('SELECT status, error FROM material WHERE id=?', (mid,)).fetchone()
        assert row['status'] == db.MATERIAL_FAILED
        assert '模型抽风了' in row['error']

    def test_promote_facts_creates_pending_fields(self, conn, temp_db, fake_ai, monkeypatch):
        importlib.reload(materials)
        monkeypatch.setattr(materials.clients, 'read_image', fake_ai.read_image)
        fake_ai.text_response = ('{"summary":"x","kind":"other","facts":['
                                 '{"text":"自研代码 5459 行","locator":"第 3 节"}]}')
        mid = materials.create_material(conn, 'r.png', b'\x89PNG\r\n\x1a\n' + b'0' * 50)
        materials.parse_material(conn, mid)
        detail = materials.material_detail(conn, mid)
        fid = detail['facts'][0]['id']

        added = materials.promote_facts_to_profile(conn, mid, [fid])
        assert added == 1
        row = conn.execute(
            "SELECT * FROM profile_field WHERE section='材料抽取'").fetchone()
        assert row['value'] == '自研代码 5459 行'
        assert row['status'] == db.STATUS_UNCONFIRMED   # 提升后仍需用户确认
        assert 'r.png' in row['source']
        assert row['note'] == '第 3 节'                 # 溯源信息保留

        # 重复提升不应重复插入
        assert materials.promote_facts_to_profile(conn, mid, [fid]) == 0
