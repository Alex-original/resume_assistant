"""
外部服务客户端：DeepSeek（文本）、DashScope（视觉 / 语音）。

统一用标准库 urllib，不额外引入 HTTP 依赖。所有调用都有超时与明确的错误信息——
AI 调用失败必须让用户看见原因，不能静默吞掉。
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

from . import config

TIMEOUT_LLM = 180
TIMEOUT_VISION = 180
TIMEOUT_ASR = 120
TIMEOUT_TTS = 90


class ServiceError(RuntimeError):
    """外部服务调用失败。消息面向用户，可直接展示。"""


def _post_json(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    if not api_key:
        raise ServiceError('未配置 API Key，请在 .env 中填写后重启服务')
    # 所有走 HTTP 的 AI 调用都从这里出去（对话 / 视觉 / ASR / TTS），
    # 卡在这一处就等于卡住了全部
    _quota_guard()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        _count_usage()
        return data
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8')[:400]
        except Exception:
            pass
        raise ServiceError(f'服务返回 {exc.code}：{detail}') from exc
    except urllib.error.URLError as exc:
        raise ServiceError(f'无法连接服务（{exc.reason}），请检查网络或代理') from exc
    except json.JSONDecodeError as exc:
        raise ServiceError(f'服务返回内容无法解析：{exc}') from exc



# ══════════════════════ 用量配额（保护共用的 API Key） ══════════════════════
#
# 你选了「朋友们共用你的 key」，那这里就是唯一的刹车：
# 每个人每天能发起多少次 AI 请求。不然有人写个脚本，
# 你的额度一晚上就没了。

def _quota_guard() -> None:
    """发起 AI 请求之前先看额度。超额直接拦下，不发请求。"""
    try:
        from . import auth, db
        uid = db.current_user()
    except Exception:
        return                      # 拿不到用户信息就别拦，不能因为记账坏了用不了
    if uid is None:
        return                      # 单用户本地模式，不限制
    user = auth.get_user(uid)
    if not user:
        return
    state = auth.quota_state(user)
    if state['exceeded']:
        raise ServiceError(
            f"今天的 AI 用量已经到上限（{state['used']}/{state['quota']} 次）。"
            '明天会自动重置——如果不够用，让管理员在后台把你的额度调高。')


def _count_usage(kind: str = 'ai') -> None:
    """成功调用之后记一笔。记账失败不影响主流程。"""
    try:
        from . import auth, db
        uid = db.current_user()
        if uid is not None:
            auth.record_usage(uid, kind)
    except Exception:
        pass


# ══════════════════════ DeepSeek · 文本 ══════════════════════

def chat(messages: list[dict], *, temperature: float = 0.3,
         model: str | None = None, json_mode: bool = False,
         max_tokens: int | None = None, allow_truncated: bool = False,
         timeout: int = TIMEOUT_LLM) -> str:
    """
    一次对话补全，返回文本。

    `allow_truncated=True` 时，输出撞上 max_tokens 不再直接报错，而是把**已生成的部分**
    返回给调用方去抢救（题组长这样"数组里一堆完整对象"的场景，前面几道题是好的，
    整批丢掉等于白花 token）。默认 False：单个对象的场景必须报错，不能拿半截结果充数。
    """
    payload: dict[str, Any] = {
        'model': model or config.get('DEEPSEEK_MODEL', 'deepseek-chat'),
        'messages': messages,
        'temperature': temperature,
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    if max_tokens:
        payload['max_tokens'] = max_tokens

    data = _post_json(
        f"{config.get('DEEPSEEK_BASE_URL').rstrip('/')}/chat/completions",
        payload, config.get('DEEPSEEK_API_KEY'), timeout)
    try:
        choice = data['choices'][0]
        content = (choice['message']['content'] or '').strip()
    except (KeyError, IndexError) as exc:
        raise ServiceError(f'DeepSeek 返回结构异常：{str(data)[:300]}') from exc

    # 输出被 max_tokens 截断时明确报错，而不是让下游拿到半截 JSON 莫名其妙地失败
    if choice.get('finish_reason') == 'length':
        if allow_truncated and content:
            return content
        raise ServiceError(
            '模型输出被长度上限截断。请减少一次生成的数量，或调大 max_tokens。')
    return content


def _is_complete_json(text: str) -> bool:
    """整段文本本身就是一个完整 JSON 吗？（用来判断输出有没有被截断）"""
    t = (text or '').strip()
    fenced = re.search(r'```(?:json)?\s*([\s\S]*?)```', t)
    if fenced:
        t = fenced.group(1).strip()
    try:
        json.loads(t)
        return True
    except json.JSONDecodeError:
        return False


def chat_json(messages: list[dict], *, temperature: float = 0.2,
              model: str | None = None, max_tokens: int | None = None,
              timeout: int = TIMEOUT_LLM, meta: dict | None = None) -> Any:
    """
    要求模型返回 JSON 并解析。

    大模型经常把 JSON 包在 ```json 围栏里，或者前后加说明文字，
    所以这里做三层容错：直接解析 → 去围栏 → 截取首个 JSON 结构。
    截断的输出会被尽量抢救（见 parse_json_loose 的截断恢复）。

    传 `meta` 可以带走这次调用的元信息（目前会写入 `truncated`），
    调用方据此决定要不要缩小批次重试。
    """
    text = chat(messages, temperature=temperature, model=model,
                json_mode=True, max_tokens=max_tokens, timeout=timeout,
                allow_truncated=True)
    if meta is not None:
        meta['truncated'] = not _is_complete_json(text)
    return parse_json_loose(text)


def _salvage_objects(text: str) -> list[tuple[int, dict]]:
    """
    从被截断的 JSON 里抢救出所有**完整**的对象。

    题库这种"数组里一堆对象"的场景，截断会让整个数组解析失败，
    但前面几十个对象其实是好的——直接丢弃太浪费（还白花了 token）。

    做法：对每个 `{` 起点尝试括号配平（跳过字符串内部），配平成功就尝试解析。
    关键点是**逐个起点尝试**，而不是只从最外层开始——
    因为截断的往往是外层，内层对象其实是完整的。
    """
    out: list[tuple[int, dict]] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != '{':
            i += 1
            continue
        depth = 0
        in_str = False
        escape = False
        j = i
        balanced = False
        while j < n:
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        balanced = True
                        break
            j += 1

        if not balanced:
            i += 1          # 从这个 { 开始配不平（外层被截断），换下一个起点
            continue

        chunk = text[i:j + 1]
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and obj:
            out.append((i, obj))    # 连带记录起点，用于判断外层是数组还是对象
            i = j + 1       # 跳过已消费的部分
        else:
            i += 1
    return out


def parse_json_loose(text: str) -> Any:
    """尽量从模型输出里把 JSON 抠出来；截断时抢救已完整的对象。"""
    text = (text or '').strip()
    if not text:
        raise ServiceError('模型返回了空内容')

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    for opener, closer in (('[', ']'), ('{', '}')):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    # 走到这里说明输出不完整——尽力抢救
    salvaged = _salvage_objects(text)
    if salvaged:
        # 判断原本应该是数组还是单个对象：
        # 看第一个完整对象的**起点之前**有没有 `[`。
        # 不能简单地比"第一个 [ 和第一个 { 谁在前"——`{"questions":[...]}` 的
        # 外层是对象、内层是数组，截断时外层配不平，真正该还原的是那个数组。
        first_start = salvaged[0][0]
        if text.find('[', 0, first_start) != -1:
            return [obj for _, obj in salvaged]
        if len(salvaged) == 1:
            return salvaged[0][1]
        return {'items': [obj for _, obj in salvaged]}

    raise ServiceError(f'模型返回的不是合法 JSON：{text[:300]}')


# ══════════════════════ DashScope · 视觉 ══════════════════════

def read_image(path: Path, prompt: str, *, timeout: int = TIMEOUT_VISION) -> str:
    """读一张图片，返回模型对它的描述/抽取结果。"""
    suffix = path.suffix.lower().lstrip('.') or 'jpeg'
    mime = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'png': 'png', 'webp': 'webp'}.get(suffix, 'jpeg')
    b64 = base64.b64encode(path.read_bytes()).decode('ascii')
    payload = {
        'model': config.get('VISION_MODEL', 'qwen-vl-max'),
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image_url',
                 'image_url': {'url': f'data:image/{mime};base64,{b64}'}},
                {'type': 'text', 'text': prompt},
            ],
        }],
    }
    data = _post_json(
        f"{config.get('DASHSCOPE_BASE_URL').rstrip('/')}/chat/completions",
        payload, config.get('DASHSCOPE_API_KEY'), timeout)
    try:
        content = data['choices'][0]['message']['content']
    except (KeyError, IndexError) as exc:
        raise ServiceError(f'视觉模型返回结构异常：{str(data)[:300]}') from exc
    # 部分模型返回分段内容
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return str(content).strip()


def _pdf_streams(raw: bytes) -> list[bytes]:
    """
    把 PDF 里所有内容流解出来。

    为什么必须解压：绝大多数 PDF（Word / WPS / Pages / 浏览器打印）的流是
    FlateDecode 压缩的，直接正则扫原文**一个 Tj 都看不到**——之前就是这么写的，
    结果所有 PDF 都被当成扫描件丢给了视觉模型（慢、贵、还可能不准）。
    """
    out: list[bytes] = []
    for match in re.finditer(rb'stream\r?\n(.*?)\r?\nendstream', raw, re.S):
        block = match.group(1)
        try:
            out.append(zlib.decompress(block))
        except zlib.error:
            try:
                out.append(zlib.decompressobj().decompress(block))
            except zlib.error:
                out.append(block)          # 没压缩，原样用
    return out


def _pdf_tounicode(streams: list[bytes]) -> dict[int, str]:
    """
    解析 ToUnicode CMap：把字形编号（CID）映射回真正的字符。

    中文 PDF 几乎都用 Identity-H 编码，文本层里存的是 `<6D45>` 这种**字形编号**，
    不是文字。少了这张映射表，抽出来就是乱码——所以这一步不能省。
    """
    table: dict[int, str] = {}
    for data in streams:
        if b'beginbfchar' not in data and b'beginbfrange' not in data:
            continue
        for block in re.findall(rb'beginbfchar(.*?)endbfchar', data, re.S):
            for src, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
                table[int(src, 16)] = _hex_to_text(dst)
        for block in re.findall(rb'beginbfrange(.*?)endbfrange', data, re.S):
            for lo, hi, dst in re.findall(
                    rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', block):
                start, end = int(lo, 16), int(hi, 16)
                base = int(dst, 16)
                for i in range(min(end - start + 1, 512)):   # 防御：范围异常大时别炸
                    table[start + i] = chr(base + i)
    return table


def _hex_to_text(hex_str: bytes) -> str:
    """`<4F60597D>` → '你好'。"""
    try:
        return bytes.fromhex(hex_str.decode('ascii')).decode('utf-16-be', 'ignore')
    except (ValueError, UnicodeDecodeError):
        return ''


def pdf_text_layer(path: Path) -> str:
    """
    尝试取 PDF 的文本层。取不到就返回空串，调用方回退到视觉模型。

    不引入 PDF 库（标准库 zlib 够用）：解压内容流 → 抽 Tj/TJ → 用 ToUnicode
    映射把字形编号还原成文字。扫描件本来就没有文本层，会自然落到视觉模型。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return ''

    streams = _pdf_streams(raw)
    cmap = _pdf_tounicode(streams)

    def decode_hex(hex_str: bytes) -> str:
        """
        `<6D45>` / `<0102>` → 文字。

        字形编号是**1 字节还是 2 字节由字体决定**，不能写死：
        同一个 PDF 里两种字体混用也很常见（实测两份 PDF，一份是 2 字节 CID，
        另一份是 1 字节子集）。所以两种宽度都试，取命中映射多的那种。
        """
        best = ''
        for width in (2, 1):
            step = width * 2
            if len(hex_str) % step:
                continue
            codes = [int(hex_str[i:i + step], 16) for i in range(0, len(hex_str), step)]
            hits = sum(1 for c in codes if c in cmap)
            text = ''.join(cmap.get(c, '') for c in codes)
            if hits and len(text) > len(best):
                best = text
        return best or _hex_to_text(hex_str)

    def decode(piece: bytes, is_hex: bool) -> str:
        if is_hex:
            return decode_hex(piece)
        # 普通字符串：可能是 UTF-16BE，也可能就是单字节
        try:
            return piece.decode('utf-8')
        except UnicodeDecodeError:
            return piece.decode('latin-1', 'ignore')

    chunks: list[str] = []
    for data in streams:
        if b'Tj' not in data and b'TJ' not in data:
            continue
        # 逐行扫，遇到换行动作就断句，避免整页糊成一行
        for line in data.split(b'\n'):
            if b'Tj' not in line and b'TJ' not in line:
                if b'Td' in line or b'TD' in line or b'T*' in line:
                    chunks.append('\n')
                continue
            for token in re.finditer(rb'<([0-9A-Fa-f\s]+)>\s*Tj|\(((?:[^()\\]|\\.)*)\)\s*Tj',
                                     line):
                if token.group(1) is not None:
                    chunks.append(decode(re.sub(rb'\s', b'', token.group(1)), True))
                else:
                    chunks.append(decode(token.group(2), False))
            for token in re.finditer(rb'\[(.*?)\]\s*TJ', line, re.S):
                body = token.group(1)
                for piece in re.finditer(rb'<([0-9A-Fa-f\s]+)>|\(((?:[^()\\]|\\.)*)\)', body):
                    if piece.group(1) is not None:
                        chunks.append(decode(re.sub(rb'\s', b'', piece.group(1)), True))
                    else:
                        chunks.append(decode(piece.group(2), False))

    text = re.sub(r'[ \t]+', ' ', ''.join(chunks))
    text = re.sub(r'\n{2,}', '\n', text).strip()
    if len(text) <= 40 or not _looks_like_prose(text):
        return ''
    return text


# 中文里出现频率最高的那批字。正常行文里它们能占到 5%~15%，
# 而字形编号映射错位解出来的"乱码"虽然每个字都合法，却几乎不含这些字（实测 0.3%~0.5%）。
_COMMON_HANZI = '的是在有我你不了和就都而及与这那很也还要会能对'


def _looks_like_prose(text: str) -> bool:
    """
    判断抽出来的文本层是不是**真的文字**。

    为什么需要这道闸：中文 PDF 常用子集字体，字形编号是每个字体各编一套。
    如果一个 PDF 里有多个字体而我们没有按字体分别映射，就会解出一堆
    看着像中文、其实是错位字形的乱码（实测踩到过：6091 个"汉字"全是乱的）。
    这种垃圾比"抽不出来"更危险——它会静默流进事实库，把档案写脏。
    所以宁可判为不可用、回退到视觉模型（慢一点但准）。
    """
    hanzi = [c for c in text if '\u4e00' <= c <= '\u9fff']
    if len(hanzi) < 30:
        # 非中文文档（英文简历等）走另一套：看有没有常见英文词
        lower = text.lower()
        return sum(lower.count(w) for w in (' the ', ' and ', ' of ', ' to ', ' a ')) >= 3
    hit = sum(1 for c in hanzi if c in _COMMON_HANZI)
    return hit / len(hanzi) >= 0.03


SCAN_MAX_PAGES = 8          # 扫描件最多读几页（默认值，可在 .env 里用 SCAN_MAX_PAGES 覆盖）


def _scan_max_pages() -> int:
    try:
        return max(1, int(config.get('SCAN_MAX_PAGES', str(SCAN_MAX_PAGES))))
    except (TypeError, ValueError):
        return SCAN_MAX_PAGES


def pdf_pages_to_images(path: Path, *, max_pages: int | None = None,
                        scale: float = 1.6) -> tuple[list[Any], int]:
    """
    把 PDF 每一页渲染成图片，返回 (临时图片路径列表, PDF 总页数)。

    为什么需要：视觉模型**不接受 PDF**——实测把 PDF 塞进 image_url 会直接报
    `InvalidParameter: The image format is illegal`（qwen-vl-max）。所以扫描件
    必须自己先转成图片。用 pypdfium2（自带渲染引擎，不依赖系统 poppler）。
    """
    import tempfile
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:                       # pragma: no cover
        raise ServiceError('缺少 pypdfium2，无法读取扫描版 PDF：'
                           '请运行 pip install pypdfium2 Pillow') from exc

    max_pages = _scan_max_pages() if max_pages is None else max_pages
    pdf = pdfium.PdfDocument(str(path))
    total = len(pdf)
    out: list[Any] = []
    tmpdir = Path(tempfile.mkdtemp(prefix='ja_pdf_'))
    for i in range(min(total, max_pages)):
        img = pdf[i].render(scale=scale).to_pil().convert('RGB')
        dest = tmpdir / f'page{i + 1}.jpg'
        img.save(dest, format='JPEG', quality=85)
        out.append(dest)
    return out, total


def read_pdf(path: Path, prompt: str) -> tuple[str, str]:
    """
    读 PDF。返回 (模型给的 JSON 文本, 方式)。

    三条路，按代价从低到高：

    1. **有文本层** → 抽出文字交给**文本模型**（DeepSeek）。最快最便宜
       （实测 11 页 PDF 3.4 秒 / 23 条事实），而且不会看错字。
    2. **没有文本层**（扫描件）→ 逐页渲染成图片，交给视觉模型。
    3. 页数超过上限时只读前 N 页，并在结果里标注清楚**没读全**，
       不能让人以为整本都读了。

    注意：文本层抽出来的是**原文**，不是模型返回的 JSON。
    必须再让模型按 schema 整理一遍，不能直接当 JSON 用（踩过这个坑）。
    """
    layer = pdf_text_layer(path)
    if layer:
        content = chat(
            [{'role': 'system', 'content': prompt},
             {'role': 'user', 'content': f'以下是 PDF 的文字内容：\n\n{layer[:20000]}'}],
            temperature=0.1, json_mode=True, max_tokens=4000)
        return content, 'text-layer'

    # 扫描件：逐页转图片给视觉模型
    pages, total = pdf_pages_to_images(path)
    if not pages:
        raise ServiceError('这个 PDF 一页都没读出来，可能文件已损坏')

    merged: list[dict] = []
    summary_bits: list[str] = []
    kind = ''
    for idx, img in enumerate(pages, 1):
        raw = read_image(img, f'{prompt}\n\n（这是 PDF 的第 {idx} 页，'
                              f'抽取事实时请在 locator 里标明页码）')
        try:
            parsed = parse_json_loose(raw)
        except ServiceError:
            continue
        if not isinstance(parsed, dict):
            continue
        for fact in (parsed.get('facts') or []):
            if isinstance(fact, dict) and str(fact.get('text', '')).strip():
                # 模型常给个空的 locator 占位；空值也要补页码，
                # 不能只在"键不存在"时才补（setdefault 在这里是错的）
                if not str(fact.get('locator', '')).strip():
                    fact['locator'] = f'第 {idx} 页'
                merged.append(fact)
        if parsed.get('summary'):
            summary_bits.append(str(parsed['summary']).strip())
        kind = kind or str(parsed.get('kind') or '')

    note = ''
    if total > len(pages):
        note = f'（这份 PDF 没有文本层，逐页读图；共 {total} 页，只读了前 {len(pages)} 页）'
    payload = {
        'summary': ' '.join(summary_bits[:2]) + note,
        'kind': kind or 'other',
        'facts': merged,
    }
    return json.dumps(payload, ensure_ascii=False), 'vision-scan'


# ══════════════════════ DashScope · 语音 ══════════════════════

def transcribe(audio_bytes: bytes, *, audio_format: str = 'wav',
               timeout: int = TIMEOUT_ASR) -> str:
    """语音识别：音频字节 → 文本。"""
    b64 = base64.b64encode(audio_bytes).decode('ascii')
    payload = {
        'model': config.get('ASR_MODEL', 'qwen3-asr-flash'),
        'messages': [{
            'role': 'user',
            'content': [{
                'type': 'input_audio',
                'input_audio': {
                    'data': f'data:audio/{audio_format};base64,{b64}',
                    'format': audio_format,
                },
            }],
        }],
    }
    data = _post_json(
        f"{config.get('DASHSCOPE_BASE_URL').rstrip('/')}/chat/completions",
        payload, config.get('DASHSCOPE_API_KEY'), timeout)
    try:
        return str(data['choices'][0]['message']['content'] or '').strip()
    except (KeyError, IndexError) as exc:
        raise ServiceError(f'识别返回结构异常：{str(data)[:300]}') from exc


def synthesize(text: str, voice: str = '', *, timeout: int = TIMEOUT_TTS) -> bytes:
    """语音合成：文本 → 音频字节。"""
    clipped = text.strip()
    if len(clipped) > 900:
        clipped = clipped[:900] + '……'
    payload = {
        'model': config.get('TTS_MODEL', 'qwen3-tts-flash'),
        'input': {
            'text': clipped,
            'voice': voice or config.get('TTS_VOICE', 'Cherry'),
            'language_type': 'Chinese',
        },
    }
    data = _post_json(
        'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation',
        payload, config.get('DASHSCOPE_API_KEY'), timeout)
    url = (data.get('output', {}).get('audio', {}) or {}).get('url', '')
    if not url:
        raise ServiceError(f'合成未返回音频地址：{str(data)[:300]}')
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except Exception as exc:
        raise ServiceError(f'下载合成音频失败：{exc}') from exc


# ══════════════════════ 流式 TTS（低延迟）══════════════════════

TTS_STREAM_URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/realtime'
TTS_STREAM_MODEL = 'qwen3-tts-flash-realtime'
TTS_SAMPLE_RATE = 24000


async def synthesize_stream(text: str, voice: str = '',
                            sample_rate: int = TTS_SAMPLE_RATE):
    """
    流式语音合成：**边生成边吐 PCM**，不等整段合成完。

    为什么要这个：原来的 HTTP 接口要等模型把整段话合成完、返回一个音频 URL、
    再下载整个文件，客户端才听到第一个字。实测一段 59 字的问题：
        整段合成 3.02s + 下载 0.23s  →  3.25s 才出声
    换成 realtime 流式：**0.45s 就出第一块音频**（快 6.7 倍）。
    对话里这 3 秒就是"生硬"和"自然"的分界线。

    逐块 yield 16-bit / 24kHz / 单声道 的原始 PCM。
    """
    _quota_guard()          # 流式 TTS 走 WebSocket，不经过 _post_json，单独卡
    try:
        import websockets
    except ImportError as exc:                       # pragma: no cover
        raise ServiceError('缺少 websockets，无法使用流式语音：pip install websockets') from exc

    key = config.get('DASHSCOPE_API_KEY')
    if not key:
        raise ServiceError('未配置 DASHSCOPE_API_KEY')

    clip = (text or '').strip()
    if not clip:
        return
    if len(clip) > 600:
        clip = clip[:600]

    url = f'{TTS_STREAM_URL}?model={TTS_STREAM_MODEL}'
    import asyncio
    try:
        async with websockets.connect(
                url, additional_headers={'Authorization': f'Bearer {key}'},
                open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps({
                'type': 'session.update',
                'session': {'voice': voice or config.get('TTS_VOICE', 'Cherry'),
                            'response_format': 'pcm', 'sample_rate': sample_rate,
                            'mode': 'server_commit'},
            }))
            await ws.send(json.dumps({'type': 'input_text_buffer.append', 'text': clip}))
            await ws.send(json.dumps({'type': 'input_text_buffer.commit'}))

            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    break
                event = json.loads(raw)
                kind = event.get('type')
                if kind == 'response.audio.delta':
                    delta = event.get('delta') or ''
                    if delta:
                        yield base64.b64decode(delta)
                elif kind == 'error':
                    raise ServiceError(
                        f'流式合成失败：{(event.get("error") or {}).get("message", "")[:200]}')
                elif kind in ('response.done', 'session.finished'):
                    break
    except ServiceError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise ServiceError(f'流式合成连接失败：{type(exc).__name__}: {exc}') from exc


def health() -> dict:
    """给设置页用的连通性概览（只检查 key 是否配置，不发真实请求）。"""
    return {
        'deepseek': bool(config.get('DEEPSEEK_API_KEY')),
        'dashscope': bool(config.get('DASHSCOPE_API_KEY')),
        'models': {
            'text': config.get('DEEPSEEK_MODEL'),
            'vision': config.get('VISION_MODEL'),
            'asr': config.get('ASR_MODEL'),
            'tts': config.get('TTS_MODEL'),
        },
    }
