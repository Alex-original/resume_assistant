"""
材料解析：把上传的截图 / PDF 变成可用的结构化事实。

设计要点：
1. **保留溯源信息**。每条抽取的事实带 locator（页码 / 章节 / 图片位置），
   这样简历建议里能写"依据：《阶段报告.pdf》第 12 页"，用户可以点回去核对。
2. **PDF 优先走文本层**，文本层不可用（扫描件）才走视觉模型——快、便宜、准。
3. **抽取失败不静默**。失败原因写进 material.error，界面直接展示。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from . import clients, config, db

SUPPORTED_IMAGE = {'.png', '.jpg', '.jpeg', '.webp'}
SUPPORTED_DOC = {'.pdf'}
SUPPORTED_ARCHIVE = {'.zip'}

# 归档（zip / GitHub 仓库）读取上限：控制耗时和 token 花费
ARCHIVE_MAX_FILES = 60
ARCHIVE_MAX_CHARS = 24000
ARCHIVE_BATCH_CHARS = 9000
ARCHIVE_MAX_BATCHES = 4

# 读归档时直接跳过的目录 / 文件（依赖、构建产物、二进制）
SKIP_DIRS = {'node_modules', '.git', 'dist', 'build', 'out', 'target', 'vendor',
             '__pycache__', '.venv', 'venv', '.next', '.nuxt', 'coverage',
             'Pods', '.gradle', '.idea', '.vscode', 'assets', 'static', 'public'}
SKIP_SUFFIX = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.svg', '.pdf',
               '.zip', '.tar', '.gz', '.mp3', '.mp4', '.mov', '.wav', '.woff',
               '.woff2', '.ttf', '.otf', '.eot', '.so', '.dylib', '.dll', '.exe',
               '.class', '.jar', '.pyc', '.map', '.lock', '.min.js', '.min.css'}
TEXT_SUFFIX = {'.md', '.markdown', '.txt', '.py', '.js', '.ts', '.tsx', '.jsx',
               '.java', '.kt', '.swift', '.m', '.mm', '.c', '.h', '.cpp', '.hpp',
               '.go', '.rs', '.rb', '.php', '.cs', '.sql', '.json', '.yaml', '.yml',
               '.toml', '.ini', '.cfg', '.conf', '.sh', '.html', '.css', '.scss',
               '.vue', '.proto', '.gradle', '.xml', '.plist', '.properties'}
# 这些文件名优先读（README 最能说明项目是什么）
PRIORITY_NAMES = {'readme.md', 'readme.txt', 'readme', 'package.json',
                  'requirements.txt', 'pyproject.toml', 'cargo.toml',
                  'go.mod', 'pom.xml', 'build.gradle', 'dockerfile'}

EXTRACT_PROMPT = """你在帮一位求职者把他的项目材料整理成事实清单，用于后续写简历和准备面试。

请仔细阅读这份材料，抽取出**可核实的事实**，用于简历和面试。重点关注：

1. **量化数字**（性能提升、用户量、代码行数、耗时、金额、比例等）—— 最重要
2. **技术方案与关键决策**（用了什么技术、为什么这么选、解决了什么问题）
3. **他的角色与职责**（主导 / 独立完成 / 参与，负责哪部分）
4. **工程难点**（踩了什么坑、怎么解决的）

严格要求：
- 只抽取材料里**真实存在**的内容，不要推测、不要补充常识
- 每条事实独立成立，能脱离上下文读懂
- 涉及数字的，把数字原样保留
- 如果是简历类材料，还要抽取：姓名、联系方式、教育背景、工作经历（公司/岗位/时间）

只返回 JSON，不要任何解释：

{
  "summary": "一句话说明这是什么材料",
  "kind": "resume | report | prd | screenshot | jd | other",
  "raw_text": "把材料上的文字**逐字转录**（保留分点、数字、专有名词），不要总结、不要改写。太长可以截断，但不要省略关键要求。",
  "facts": [
    {"text": "抽取到的事实（独立完整的一句话）", "locator": "位置，如：第 12 页 / 顶部表格 / 第 3 节"}
  ]
}

如果材料内容无法识别或与求职无关，返回 {"summary":"...","kind":"other","facts":[]}。"""


def _detect_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_IMAGE:
        return f'image/{suffix.lstrip(".").replace("jpg", "jpeg")}'
    if suffix in SUPPORTED_DOC:
        return 'application/pdf'
    if suffix in SUPPORTED_ARCHIVE:
        return 'application/zip'
    return 'application/octet-stream'


def save_upload(filename: str, content: bytes) -> tuple[Path, str]:
    """把上传的文件落盘，返回 (路径, media_type)。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_IMAGE | SUPPORTED_DOC | SUPPORTED_ARCHIVE:
        raise ValueError(
            f'不支持的文件类型：{suffix}（支持 png / jpg / jpeg / webp / pdf / zip）')
    stored = config.UPLOAD_DIR / f'{uuid.uuid4().hex}{suffix}'
    stored.write_bytes(content)
    return stored, _detect_media_type(stored)


def create_material(conn: sqlite3.Connection, filename: str, content: bytes,
                    project_id: int | None = None, source_url: str = '',
                    kind: str = 'file') -> int:
    """登记一条材料（不解析）。"""
    stored, media_type = save_upload(filename, content)
    if kind == 'file':
        kind = 'archive' if media_type == 'application/zip' else 'file'
    now = db.now_iso()
    cur = conn.execute(
        'INSERT INTO material (filename, stored_path, media_type, size_bytes, '
        'project_id, status, source_url, kind, created_at, updated_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (filename, str(stored), media_type, len(content), project_id,
         db.MATERIAL_PENDING, source_url, kind, now, now))
    return int(cur.lastrowid)


def parse_material(conn: sqlite3.Connection, material_id: int) -> dict:
    """
    解析一条材料：抽取事实并入库。

    这是耗时操作（视觉模型 + 大模型），调用方应放在后台或给足超时。
    """
    row = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
    if row is None:
        raise ValueError('材料不存在')

    path = Path(row['stored_path'])
    if not path.is_file():
        conn.execute("UPDATE material SET status=?, error=?, updated_at=? WHERE id=?",
                     (db.MATERIAL_FAILED, '文件已丢失', db.now_iso(), material_id))
        raise ValueError('材料文件已丢失')

    conn.execute("UPDATE material SET status=?, error='', updated_at=? WHERE id=?",
                 (db.MATERIAL_PARSING, db.now_iso(), material_id))

    # zip / GitHub 仓库走独立通道：先解包成文本摘要，再分批抽事实
    if row['media_type'] == 'application/zip' or row['kind'] in ('archive', 'repo'):
        try:
            return parse_archive(conn, material_id, path)
        except Exception as exc:                       # noqa: BLE001
            message = str(exc)[:400]
            conn.execute("UPDATE material SET status=?, error=?, updated_at=? WHERE id=?",
                         (db.MATERIAL_FAILED, message, db.now_iso(), material_id))
            return {'ok': False, 'error': message}

    try:
        if row['media_type'] == 'application/pdf':
            raw_text, how = clients.read_pdf(path, EXTRACT_PROMPT)
        else:
            raw_text = clients.read_image(path, EXTRACT_PROMPT)
            how = 'vision'
        parsed = clients.parse_json_loose(raw_text)
        if not isinstance(parsed, dict):
            raise clients.ServiceError('解析结果不是对象')
    except Exception as exc:
        message = str(exc)[:400]
        conn.execute("UPDATE material SET status=?, error=?, updated_at=? WHERE id=?",
                     (db.MATERIAL_FAILED, message, db.now_iso(), material_id))
        return {'ok': False, 'error': message}

    facts = parsed.get('facts') or []
    now = db.now_iso()
    conn.execute('DELETE FROM material_fact WHERE material_id = ?', (material_id,))
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        text = str(fact.get('text', '')).strip()
        if len(text) < 4:
            continue
        conn.execute(
            'INSERT INTO material_fact (material_id, text, locator, status, created_at) '
            'VALUES (?,?,?,?,?)',
            (material_id, text, str(fact.get('locator', '')).strip(),
             db.STATUS_UNCONFIRMED, now))

    conn.execute(
        'UPDATE material SET status=?, summary=?, raw_text=?, doc_kind=?, error=?, '
        'updated_at=? WHERE id=?',
        (db.MATERIAL_DONE, str(parsed.get('summary', ''))[:200],
         str(parsed.get('raw_text', '')).strip()[:20000],
         str(parsed.get('kind', 'other'))[:20], '', now, material_id))
    return {
        'ok': True,
        'summary': parsed.get('summary', ''),
        'kind': parsed.get('kind', 'other'),
        'facts': len(facts),
        'via': how,
    }




# ══════════════════ 岗位（JD）解析 ══════════════════

JD_PROMPT = """你在读一份**招聘岗位要求（JD）**的截图或 PDF。

请把上面的文字**逐字转录**，并抽出结构化字段。

严格要求：

- `raw_text` 是**逐字转录**：保留原文的分点编号、数字、专有名词、技术栈名称。
  **不要总结、不要改写成自己的话、不要补充原文没有的内容。**
  这份转录会直接拿去分析匹配度，漏一条要求就等于少判一条。
- 看不清的字用「□」占位，不要猜。
- 招不到的信息（比如没写薪资）就留空字符串，不要编。

只返回 JSON：

{
  "raw_text": "逐字转录的全文",
  "company": "公司名，没写就空",
  "title": "岗位名，没写就空",
  "city": "城市，没写就空",
  "salary": "薪资，没写就空",
  "summary": "一句话说明这是什么岗位",
  "responsibilities": ["岗位职责，逐条，照抄原文"],
  "requirements": ["任职要求，逐条，照抄原文"]
}
"""


def parse_job_material(conn: sqlite3.Connection, material_id: int) -> dict:
    """
    解析一份 JD 材料（截图 / PDF），拿到**完整原文**和结构化字段。

    为什么单独一条链路：岗位分析（四层拆解 + 匹配度）吃的就是 JD 原文。
    通用材料解析只给"一句话摘要 + 若干事实"，拿去做岗位分析会缺一大半要求。
    """
    row = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
    if row is None:
        raise ValueError('材料不存在')
    path = Path(row['stored_path'])
    if not path.is_file():
        raise ValueError('材料文件已丢失')

    conn.execute("UPDATE material SET status=?, error='', updated_at=? WHERE id=?",
                 (db.MATERIAL_PARSING, db.now_iso(), material_id))

    try:
        if row['media_type'] == 'application/pdf':
            # 有文本层就直接用原文（比模型转录更准）
            layer = clients.pdf_text_layer(path)
            if layer:
                text = layer
                parsed = {'raw_text': layer, 'summary': '', 'responsibilities': [],
                          'requirements': [], 'company': '', 'title': '', 'city': '',
                          'salary': ''}
            else:
                text = clients.read_pdf(path, JD_PROMPT)[0]
                parsed = clients.parse_json_loose(text)
        else:
            text = clients.read_image(path, JD_PROMPT)
            parsed = clients.parse_json_loose(text)
        if not isinstance(parsed, dict):
            raise clients.ServiceError('JD 解析结果不是对象')
    except Exception as exc:                       # noqa: BLE001
        message = str(exc)[:400]
        conn.execute("UPDATE material SET status=?, error=?, updated_at=? WHERE id=?",
                     (db.MATERIAL_FAILED, message, db.now_iso(), material_id))
        return {'ok': False, 'error': message}

    raw_text = str(parsed.get('raw_text') or '').strip()
    if not raw_text:
        # 模型没给转录就退回原文（PDF 文本层的情况）
        raw_text = str(text or '').strip()

    now = db.now_iso()
    # 职责 + 要求都作为事实存下来，后续可以提升进档案
    conn.execute('DELETE FROM material_fact WHERE material_id = ?', (material_id,))
    items = list(parsed.get('responsibilities') or []) + list(parsed.get('requirements') or [])
    for i, item in enumerate(items, 1):
        text_i = str(item).strip()
        if len(text_i) < 4:
            continue
        conn.execute(
            'INSERT INTO material_fact (material_id, text, locator, status, created_at) '
            'VALUES (?,?,?,?,?)',
            (material_id, text_i, '岗位要求', db.STATUS_UNCONFIRMED, now))

    conn.execute(
        'UPDATE material SET status=?, summary=?, raw_text=?, doc_kind=?, error=?, '
        'updated_at=? WHERE id=?',
        (db.MATERIAL_DONE, str(parsed.get('summary') or '')[:200], raw_text[:20000],
         'jd', '', now, material_id))

    return {
        'ok': True,
        'raw_text': raw_text,
        'summary': str(parsed.get('summary') or ''),
        'company': str(parsed.get('company') or '').strip(),
        'title': str(parsed.get('title') or '').strip(),
        'city': str(parsed.get('city') or '').strip(),
        'salary': str(parsed.get('salary') or '').strip(),
        'facts': len(items),
        'via': 'vision',
    }


# ══════════════════ 归档 / GitHub 仓库 ══════════════════

def _github_zip_url(url: str) -> tuple[str, str]:
    """
    `https://github.com/owner/repo` → (下载地址, 仓库名)。

    走 codeload 而不是页面（实测 github.com 主站在某些网络下取不到，
    codeload 可以）。分支先试 main，失败再试 master。
    """
    import urllib.parse
    raw = url.strip()
    if raw.endswith('.git'):
        raw = raw[:-4]
    # 用 urlparse 拿 host，别用 split('/')[0]——那是 "https:"，会把合法地址判成非法
    if '://' not in raw:
        raw = 'https://' + raw
    parsed = urllib.parse.urlparse(raw)
    if parsed.netloc.lower() not in ('github.com', 'www.github.com'):
        raise ValueError('只支持 GitHub 仓库地址，例如 https://github.com/owner/repo')
    parts = [p for p in parsed.path.split('/') if p]
    if len(parts) < 2:
        raise ValueError('地址里缺仓库名，应该长这样：https://github.com/owner/repo')
    owner, repo = parts[0], parts[1]
    return f'https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{{branch}}', f'{owner}-{repo}'


def fetch_github_zip(url: str, timeout: int = 90) -> tuple[bytes, str, str]:
    """下载 GitHub 仓库压缩包，返回 (字节, 文件名, 实际使用的地址)。"""
    import urllib.error
    import urllib.request

    template, name = _github_zip_url(url)
    last_error = ''
    for branch in ('main', 'master'):
        real = template.format(branch=branch)
        req = urllib.request.Request(real, headers={'User-Agent': 'job-assistant'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if data[:2] == b'PK':
                return data, f'{name}.zip', real
            last_error = '下载到的不是 zip'
        except urllib.error.HTTPError as exc:
            last_error = f'HTTP {exc.code}（{branch} 分支）'
        except Exception as exc:                       # noqa: BLE001
            last_error = str(exc)
    raise ValueError(f'拉取仓库失败：{last_error}')


def _should_read(name: str) -> bool:
    from pathlib import PurePosixPath
    parts = PurePosixPath(name).parts
    if any(p in SKIP_DIRS or p.startswith('.') for p in parts[:-1]):
        return False
    base = parts[-1].lower()
    if base.startswith('.'):
        return False
    if base.endswith('.min.js') or base.endswith('.min.css'):
        return False
    suffix = PurePosixPath(base).suffix
    if suffix in SKIP_SUFFIX:
        return False
    return suffix in TEXT_SUFFIX or base in PRIORITY_NAMES or suffix == ''


def archive_digest(path: Path, max_files: int = ARCHIVE_MAX_FILES,
                   max_chars: int = ARCHIVE_MAX_CHARS) -> tuple[str, int, int]:
    """
    把 zip / GitHub 仓库压成一段可读文本，返回 (正文, 读了多少文件, 库里共多少文件)。

    挑选策略：README 和配置文件优先，然后按文件大小从小到大（小文件往往是核心逻辑，
    大文件常是生成物）。总量封顶，避免把 token 花在无关代码上。
    """
    import zipfile
    with zipfile.ZipFile(path) as zf:
        entries = [i for i in zf.infolist() if not i.is_dir() and _should_read(i.filename)]
        total = len(entries)
        entries.sort(key=lambda i: (0 if i.filename.split('/')[-1].lower() in PRIORITY_NAMES
                                    else 1, i.file_size))

        chunks: list[str] = []
        used = 0
        used_files = 0
        for info in entries:
            if used >= max_chars or used_files >= max_files:
                break
            try:
                raw = zf.read(info)
            except (KeyError, RuntimeError):
                continue
            if b'\x00' in raw[:1024]:          # 二进制，跳过
                continue
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('utf-8', 'ignore')
            text = text.strip()
            if not text:
                continue
            budget = max_chars - used
            if len(text) > budget:
                text = text[:budget] + '\n…（文件过长已截断）'
            # 去掉仓库压缩包最外层那一级目录名，读起来干净些
            rel = '/'.join(info.filename.split('/')[1:]) or info.filename
            chunks.append(f'\n===== 文件：{rel} =====\n{text}')
            used += len(text)
            used_files += 1

    return ''.join(chunks).strip(), used_files, total


ARCHIVE_PROMPT = """你在帮一位求职者把他的**代码仓库 / 项目归档**整理成事实清单，用于写简历和准备面试。

下面是他项目的文件内容（可能只截取了部分文件）。

请抽取出**可用于简历和面试深挖的事实**，重点关注：

1. **这个项目是什么、解决什么问题**（先从 README 和目录结构判断）
2. **技术栈与架构**（用了哪些语言/框架/中间件，怎么分层的）
3. **量化证据**（代码行数、文件数、测试数、性能指标、用户/数据规模——只写材料里真有的）
4. **工程亮点与难点**（并发、性能优化、数据一致性、容错、部署）
5. **他的角色**（独立完成 / 主导 / 参与）

严格要求：
- 只写材料里**真实存在**的内容，不要推测、不要补常识、不要编数字
- 每条事实独立成句，能脱离上下文读懂
- locator 写文件路径或模块名，便于回查

只返回 JSON：

{
  "summary": "一句话说明这个项目是什么、做到了什么",
  "kind": "repo",
  "facts": [
    {"text": "事实（独立完整的一句话）", "locator": "文件路径 / 模块名"}
  ]
}
"""


def parse_archive(conn: sqlite3.Connection, material_id: int,
                  path: Path) -> dict:
    """解析 zip / GitHub 仓库：分批喂给文本模型，合并事实。"""
    digest, used_files, total_files = archive_digest(path)
    if not digest:
        raise clients.ServiceError(
            '这个压缩包里没有可读的文本代码（可能全是图片/二进制，或者目录被跳过了）')

    batches = [digest[i:i + ARCHIVE_BATCH_CHARS]
               for i in range(0, len(digest), ARCHIVE_BATCH_CHARS)][:ARCHIVE_MAX_BATCHES]

    facts: list[dict] = []
    summaries: list[str] = []
    for idx, batch in enumerate(batches, 1):
        hint = f'（这是第 {idx}/{len(batches)} 批文件内容）' if len(batches) > 1 else ''
        raw = clients.chat(
            [{'role': 'system', 'content': ARCHIVE_PROMPT},
             {'role': 'user', 'content': f'{hint}\n{batch}'}],
            temperature=0.2, json_mode=True, max_tokens=4000)
        try:
            parsed = clients.parse_json_loose(raw)
        except clients.ServiceError:
            continue
        if not isinstance(parsed, dict):
            continue
        for f in (parsed.get('facts') or []):
            if isinstance(f, dict) and str(f.get('text', '')).strip():
                facts.append(f)
        if parsed.get('summary'):
            summaries.append(str(parsed['summary']).strip())

    if not facts:
        raise clients.ServiceError('没能从这个压缩包里读出可用的事实')

    now = db.now_iso()
    conn.execute('DELETE FROM material_fact WHERE material_id = ?', (material_id,))
    seen = set()
    added = 0
    for f in facts[:200]:
        text = str(f.get('text', '')).strip()
        key = text[:40]
        if len(text) < 4 or key in seen:
            continue
        seen.add(key)
        conn.execute(
            'INSERT INTO material_fact (material_id, text, locator, status, created_at) '
            'VALUES (?,?,?,?,?)',
            (material_id, text, str(f.get('locator', '')).strip(),
             db.STATUS_UNCONFIRMED, now))
        added += 1

    note = ''
    if total_files > used_files:
        note = f'（仓库共 {total_files} 个文本文件，读了 {used_files} 个）'
    summary = (summaries[0] if summaries else '项目代码归档') + note
    conn.execute(
        'UPDATE material SET status=?, summary=?, raw_text=?, doc_kind=?, error=?, '
        'updated_at=? WHERE id=?',
        (db.MATERIAL_DONE, summary[:300], digest[:20000], 'repo', '', now, material_id))
    return {'ok': True, 'summary': summary, 'kind': 'repo', 'facts': added,
            'via': f'repo({used_files}/{total_files} 文件)', 'project_id': None}


PROJECT_SUMMARY_PROMPT = """你在帮一位求职者整理**项目总结文档**。这份文档他自己会先看一遍确认，
之后面试官会基于它深挖细节，所以必须**只写真实存在的东西**。

## 输出要求

用 Markdown，固定这几个小节（没有内容的节就写「暂无」）：

### 一句话定位
这个项目是什么、解决什么问题、给谁用。

### 我的角色
独立完成 / 主导 / 参与，具体负责哪部分。

### 技术架构
分层、关键组件、用了什么技术、为什么这么选。

### 量化结果
只写材料里有的数字（用户量、性能、代码量、收入…）。**材料里没有的不要编**，
宁可写「暂无量化数据」。

### 难点与取舍
工程上最难的地方、当时怎么权衡的、代价是什么。

### 面试官可能追问的点
基于这份材料，列出 3-5 个最可能被追问、且材料里**答不上来**的地方
（这是给候选人自己补课用的，不是给面试官的）。

严格要求：
- 事实只能来自下面的材料，不要补充常识、不要推测
- 数字原样保留
- 不确定的地方明确写「材料里没有说明」
"""


def generate_project_summary(conn: sqlite3.Connection, project_id: int) -> dict:
    """
    为某个项目生成/刷新**项目总结文档**，存进 project.summary_doc。

    素材来源：项目要点（已确认的）+ 关联到该项目的材料抽出来的事实。
    这些正是面试官会追问的东西，所以文档里同时列出「可能被追问但材料答不上的点」。
    """
    proj = conn.execute('SELECT * FROM project WHERE id = ?', (project_id,)).fetchone()
    if proj is None:
        raise ValueError('项目不存在')

    points = conn.execute(
        "SELECT text, metric FROM project_point WHERE project_id = ? "
        "AND status != 'obsolete' ORDER BY sort_order, id", (project_id,)).fetchall()
    facts = conn.execute(
        "SELECT f.text, f.locator, m.filename FROM material_fact f "
        "JOIN material m ON m.id = f.material_id "
        "WHERE m.project_id = ? AND f.status != 'obsolete' ORDER BY f.id",
        (project_id,)).fetchall()

    if not points and not facts:
        raise ValueError('这个项目还没有要点或材料，先上传项目材料或确认要点')

    lines = [f'# 项目：{proj["name"]}']
    if proj['role']:
        lines.append(f'角色：{proj["role"]}')
    if proj['period']:
        lines.append(f'时间：{proj["period"]}')
    lines.append('\n## 已知要点')
    lines.extend(f'- {p["text"]}' + (f'（数字：{p["metric"]}）' if p['metric'] else '')
                 for p in points)
    if facts:
        lines.append('\n## 材料里抽出来的事实')
        lines.extend(f'- {f["text"]}　[来源：{f["filename"]}{" " + f["locator"] if f["locator"] else ""}]'
                     for f in facts[:120])

    doc = clients.chat(
        [{'role': 'system', 'content': PROJECT_SUMMARY_PROMPT},
         {'role': 'user', 'content': '\n'.join(lines)[:24000]}],
        temperature=0.3, max_tokens=3000)

    now = db.now_iso()
    conn.execute('UPDATE project SET summary_doc = ?, updated_at = ? WHERE id = ?',
                 (doc.strip(), now, project_id))
    db.log_change(conn, 'project', project_id, 'summarize')
    return {'ok': True, 'chars': len(doc), 'summary_doc': doc.strip(),
            'points': len(points), 'facts': len(facts)}

def list_materials(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        'SELECT m.*, p.name AS project_name, '
        '(SELECT COUNT(*) FROM material_fact f WHERE f.material_id = m.id) AS fact_count '
        'FROM material m LEFT JOIN project p ON p.id = m.project_id '
        'ORDER BY m.id DESC').fetchall()
    return db.rows_to_dicts(rows)


def material_detail(conn: sqlite3.Connection, material_id: int) -> dict | None:
    row = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out['facts'] = db.rows_to_dicts(conn.execute(
        'SELECT * FROM material_fact WHERE material_id = ? ORDER BY id', (material_id,)))
    return out


def promote_facts_to_profile(conn: sqlite3.Connection, material_id: int,
                             fact_ids: list[int]) -> int:
    """
    把材料事实提升为档案字段（用户点了"加入档案"）。

    归类为「材料抽取」段落，并标注来源为材料文件名——
    这样它在档案页里能和其他字段一样被确认或修正。
    """
    material = conn.execute('SELECT * FROM material WHERE id = ?', (material_id,)).fetchone()
    if material is None:
        return 0
    now = db.now_iso()
    added = 0
    for fact_id in fact_ids:
        fact = conn.execute(
            'SELECT * FROM material_fact WHERE id = ? AND material_id = ?',
            (fact_id, material_id)).fetchone()
        if fact is None:
            continue
        key = fact['text'][:40]
        exists = conn.execute(
            "SELECT id FROM profile_field WHERE section='材料抽取' AND key=?", (key,)).fetchone()
        if exists:
            continue
        conn.execute(
            'INSERT INTO profile_field (section, key, value, source, as_of, status, '
            'note, sort_order, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
            ('材料抽取', key, fact['text'], f'材料：{material["filename"]}',
             '', db.STATUS_UNCONFIRMED, fact['locator'],
             int(fact_id), now))
        conn.execute('UPDATE material_fact SET status=? WHERE id=?',
                     (db.STATUS_CONFIRMED, fact_id))
        added += 1
    return added
