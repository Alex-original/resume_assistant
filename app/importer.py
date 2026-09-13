"""
导入器：把 `求职助手/` 里已有的 markdown 资料读进数据库。

## 三条硬规则

1. **导入的一切都是 `unconfirmed`。** 不因为是"之前整理过的"就当成事实。
2. **每条数据都带上 `source` 和 `as_of`。**
   来自去年简历的那批，`as_of` 明确写「2025-06 前后（去年简历，可能已过期）」——
   让用户在界面上第一眼就知道这条可能过期。
3. **只导入"档案事实"，不导入分析结论。**
   `高危表述清单`、`投递定位`、`冲突清单`、`待补清单` 这些是我们一起产出的**判断**，
   不是用户的事实，导入进来只会污染档案。

导入幂等：重复跑不产生重复行。
"""

from __future__ import annotations

import re
import os
import sqlite3
from pathlib import Path

from . import db

# 资料源目录：默认在工作区旁边（本地开发时就是 ../../求职助手）。
# 部署到服务器上时用 ASSISTANT_DIR 环境变量指过去，**不要写死绝对路径**。
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_workspace() -> Path:
    """
    找到含 `求职助手/` 的那一层目录。

    不数"上几级"——我数错过两次，而且服务器上的目录深度不一定和本地一样。
    直接往上找，找不到就退回项目根（那种情况下导入功能本来就无源可导）。
    """
    env = os.environ.get('WORKSPACE_DIR')
    if env:
        return Path(env).resolve()
    for base in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
        if (base / '求职助手').is_dir():
            return base
    return PROJECT_ROOT


WORKSPACE = _find_workspace()
ASSISTANT = Path(os.environ.get('ASSISTANT_DIR') or (WORKSPACE / '求职助手'))

SRC_PROFILE = '求职助手/00-档案/个人档案.md'
SRC_BASELINE = '求职助手/00-档案/简历基线.md'
SRC_CARD = '求职助手/01-项目库/{}'
AS_OF_OLD = '2025-06 前后（去年简历，可能已过期）'

TABLE_ROW = re.compile(r'^\|(.+)\|\s*$')
TABLE_SEP = re.compile(r'^\|[\s:|-]+\|\s*$')
HEADING = re.compile(r'^(#{2,3})\s+(.+?)\s*$')
LIST_ITEM = re.compile(r'^\s*\d+[.、]\s+(.+)$')
BULLET = re.compile(r'^\s*[-*+]\s+(.+)$')
METRIC = re.compile(r'\*\*([^*]+)\*\*|(\d[\d,.]*\s*(?:%|行|篇|万字|个|条|项|次|倍|元|人|张|秒|分钟))')

# ── 段落筛选 ─────────────────────────────────────────────────────────────
# 只保留真正描述"这个人是什么样"的段落。
SECTION_ALLOW = ('基本信息', '求职意向', '个人优势', '核心技术栈', '技能',
                 '教育背景', '工作成果', '工作经历', '运营数据', '量化数字')
# 明确排除：这些是分析结论或元信息，不是档案事实。
SECTION_DENY = ('高危表述', '投递定位', '冲突', '待补', '缺口', '风险',
                '素材', '索引', '改写说明', '复盘', '训练计划', '看板')

# 这些 key 是排版残留或指向别处，不是事实
KEY_DENY = ('项', '值', '状态', '方向', '技能', '类别', '数值', '说明', '来源',
            '数字', '指标', '值（排除本人测试账号）', '#', '出处')


def _normalize_section(raw: str) -> str:
    """把 `一、基本信息（✅ 已确认）` 规范成 `基本信息`。"""
    s = raw.strip()
    s = re.sub(r'^[0-9一二三四五六七八九十]+[、.．]\s*', '', s)   # 去掉序号
    s = re.sub(r'[（(].*?[)）]', '', s)                          # 去掉括号说明
    s = re.sub(r'[✅⚠️🔴📌📋❌🥇🥈🥉]', '', s)                    # 去掉表情
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' -—:：')


def _section_allowed(name: str) -> bool:
    if any(bad in name for bad in SECTION_DENY):
        return False
    return any(good in name for good in SECTION_ALLOW)


def _cells(line: str) -> list[str]:
    m = TABLE_ROW.match(line.strip())
    return [c.strip() for c in m.group(1).split('|')] if m else []


def _clean(text: str) -> str:
    text = re.sub(r'\*\*([^*]*)\*\*', r'\1', text)
    text = text.replace('`', '')
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)   # 链接取文字
    return text.strip()


def _parse_tables(markdown: str) -> list[tuple[str, list[list[str]]]]:
    """按二/三级标题分段，返回 [(规范化段名, 表格数据行), ...]。"""
    out: list[tuple[str, list[list[str]]]] = []
    section = ''
    rows: list[list[str]] = []
    lines = markdown.splitlines()

    def flush() -> None:
        if section and rows:
            out.append((section, list(rows)))
        rows.clear()

    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m:
            flush()
            section = _normalize_section(m.group(2))
            continue
        s = line.strip()
        if not TABLE_ROW.match(s) or TABLE_SEP.match(s):
            continue
        if i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1].strip()):
            continue   # 表头
        cells = _cells(s)
        if cells:
            rows.append(cells)
    flush()
    return out


def import_profile(conn: sqlite3.Connection) -> dict:
    """从个人档案.md 与 简历基线.md 导入字段。"""
    added = updated = skipped = 0
    now = db.now_iso()

    for path, source in ((ASSISTANT / '00-档案' / '个人档案.md', SRC_PROFILE),
                         (ASSISTANT / '00-档案' / '简历基线.md', SRC_BASELINE)):
        if not path.is_file():
            continue
        for order, (section, rows) in enumerate(_parse_tables(path.read_text(encoding='utf-8'))):
            if not _section_allowed(section):
                skipped += len(rows)
                continue
            for r, cells in enumerate(rows):
                if len(cells) < 2:
                    continue
                key, value = _clean(cells[0]), _clean(cells[1])
                # 有些表格第一列是行号（`| # | 成果 | 真实数字 | 出处 |`），
                # 此时真正的键是第二列、值是第三列。
                # 不处理的话，「日活约 1400 人」这种最值钱的数字会被当成备注丢掉。
                if re.fullmatch(r'\d+', key) and len(cells) >= 3:
                    key, value = value, _clean(cells[2])
                    cells = cells[1:]
                if not key or not value or key in KEY_DENY:
                    skipped += 1
                    continue
                if value.startswith('>') or '【待补】' in value:
                    skipped += 1
                    continue
                # 值里残留的文件路径引用去掉
                value = re.sub(r'（[^）]*\.md[^）]*）', '', value).strip()
                note = _clean(cells[2]) if len(cells) > 2 else ''
                cur = conn.execute(
                    'SELECT id, value FROM profile_field WHERE section = ? AND key = ?',
                    (section, key)).fetchone()
                if cur is None:
                    conn.execute(
                        'INSERT INTO profile_field (section, key, value, source, as_of, '
                        'status, note, sort_order, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                        (section, key, value, source, AS_OF_OLD,
                         db.STATUS_UNCONFIRMED, note, order * 100 + r, now))
                    added += 1
                elif cur['value'] != value:
                    conn.execute(
                        'UPDATE profile_field SET value=?, source=?, as_of=?, status=?, '
                        'updated_at=? WHERE id=?',
                        (value, source, AS_OF_OLD, db.STATUS_UNCONFIRMED, now, cur['id']))
                    db.log_change(conn, 'profile_field', cur['id'], 'import',
                                  cur['value'], value)
                    updated += 1
    return {'fields_added': added, 'fields_updated': updated, 'fields_skipped': skipped}


def _extract_role(lines: list[str]) -> str:
    """
    从「事实底稿」的表格里取角色。

    注意：早先的实现直接匹配整行，结果把 `| 我的角色 | 个人主导（…` 整行塞进了 role。
    这里改成只在**表格单元格**里找，并且限制长度。
    """
    for line in lines[:120]:
        if not TABLE_ROW.match(line.strip()):
            continue
        cells = [_clean(c) for c in _cells(line)]
        if len(cells) < 2:
            continue
        if any(k in cells[0] for k in ('角色', '我的角色', '候选人角色')):
            return cells[1][:60]
    return ''


def import_projects(conn: sqlite3.Connection) -> dict:
    """从 01-项目库/ 的项目卡片导入。**只认带「# 项目卡片：」标题的文件。**"""
    added = updated = points = 0
    skipped_files: list[str] = []
    now = db.now_iso()

    for order, card in enumerate(sorted((ASSISTANT / '01-项目库').glob('*.md'))):
        markdown = card.read_text(encoding='utf-8')
        lines = markdown.splitlines()

        name = ''
        for line in lines:
            m = re.match(r'^#\s+项目卡片[：:]\s*(.+)$', line.strip())
            if m:
                name = m.group(1).strip()
                break
        if not name:
            # 不是项目卡片（比如运营数据、证据附录），跳过而不是硬塞成项目
            skipped_files.append(card.name)
            continue

        summary = ''
        for i, line in enumerate(lines):
            if re.match(r'^##\s+一句话定位', line.strip()):
                for j in range(i + 1, min(i + 6, len(lines))):
                    if lines[j].strip():
                        summary = _clean(lines[j])
                        break
                break

        role = _extract_role(lines)
        source = SRC_CARD.format(card.name)
        row = conn.execute('SELECT id FROM project WHERE name = ?', (name,)).fetchone()
        if row is None:
            cur = conn.execute(
                'INSERT INTO project (name, role, period, summary, card_path, source, '
                'as_of, status, sort_order, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (name, role, '', summary, str(card.relative_to(WORKSPACE)),
                 source, AS_OF_OLD, db.STATUS_UNCONFIRMED, order, now))
            pid = cur.lastrowid
            added += 1
        else:
            pid = row['id']
            conn.execute('UPDATE project SET summary=?, role=?, card_path=?, updated_at=? '
                         'WHERE id=?', (summary, role, str(card.relative_to(WORKSPACE)), now, pid))
            updated += 1

        in_points = False
        seq = 0
        for line in lines:
            if re.match(r'^##\s+.*简历可用条目', line.strip()):
                in_points = True
                continue
            if in_points and re.match(r'^##\s', line.strip()):
                break
            if not in_points:
                continue
            m = LIST_ITEM.match(line) or BULLET.match(line)
            if not m:
                continue
            text = _clean(m.group(1))
            if len(text) < 8:
                continue
            metrics = [a or b for a, b in METRIC.findall(text)]
            metric = ' / '.join(dict.fromkeys(metrics)) if metrics else ''
            if conn.execute('SELECT id FROM project_point WHERE project_id=? AND text=?',
                            (pid, text)).fetchone():
                continue
            conn.execute(
                'INSERT INTO project_point (project_id, kind, text, metric, source, '
                'status, sort_order, updated_at) VALUES (?,?,?,?,?,?,?,?)',
                (pid, 'point', text, metric, source, db.STATUS_UNCONFIRMED, seq, now))
            seq += 1
            points += 1

    return {'projects_added': added, 'projects_updated': updated,
            'points_added': points, 'project_files_skipped': skipped_files}


def run(reset: bool = False) -> dict:
    """完整导入。`reset=True` 时先清空（用于重新导入）。"""
    db.init_db()
    with db.session() as conn:
        if reset:
            for t in ('project_point', 'project', 'profile_field', 'change_log'):
                conn.execute(f'DELETE FROM {t}')
        result: dict = {}
        result.update(import_profile(conn))
        result.update(import_projects(conn))
        result['stats'] = db.stats(conn)
    return result


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(run(reset='--reset' in sys.argv), ensure_ascii=False, indent=2))
