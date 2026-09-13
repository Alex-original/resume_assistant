"""
PDF 文本层解析测试。

为什么单独一个文件：中文 PDF 的文本层是最容易**静默出错**的地方——
解出来的东西看着像中文，其实全是错位字形。垃圾文本比抽不出来更危险，
它会悄悄写进事实库。所以这里既测"能抽出来"，也测"抽歪了要能识别出来"。
"""

from __future__ import annotations

import zlib

import pytest

from app import clients


def make_pdf(text_codes: dict[int, str], glyphs: bytes, width: int = 2) -> bytes:
    """
    造一个最小可用 PDF：内容流 FlateDecode 压缩 + 带 ToUnicode 映射。

    text_codes: {字形编号: 字符}
    glyphs:     内容流里那串字形编号（bytes，按 width 字节一组）
    """
    bfchar = b''.join(
        f'<{code:0{width * 2}X}> <{ord(ch):04X}>'.encode() for code, ch in text_codes.items())
    cmap = (b'/CIDInit /ProcSet findresource begin\n'
            b'12 dict begin begincmap\n'
            b'1 beginbfchar\n' + bfchar + b'\nendbfchar\n'
            b'endcmap end end')
    cmap_z = zlib.compress(cmap)

    hexed = glyphs.hex().upper().encode()
    content = b'BT /F1 20 Tf 72 700 Td <' + hexed + b'> Tj ET'
    content_z = zlib.compress(content)

    return (b'%PDF-1.4\n'
            b'1 0 obj << /Type /Font /Subtype /Type0 /ToUnicode 2 0 R >> endobj\n'
            b'2 0 obj << /Length ' + str(len(cmap_z)).encode() + b' >>\nstream\n'
            + cmap_z + b'\nendstream endobj\n'
            b'3 0 obj << /Length ' + str(len(content_z)).encode() + b' >>\nstream\n'
            + content_z + b'\nendstream endobj\n'
            b'%%EOF\n')


class TestStreamDecompress:
    def test_decompresses_flate_stream(self):
        """★ 回归：不解压的话，压缩 PDF 一个 Tj 都看不到，会被误判成扫描件。"""
        raw = make_pdf({1: '你'}, b'\x00\x01')
        streams = clients._pdf_streams(raw)
        blob = b''.join(streams)
        assert b'Tj' in blob, '内容流没有解压出来'

    def test_tolerates_uncompressed_stream(self):
        raw = (b'%PDF-1.4\n1 0 obj << >>\nstream\nBT <01> Tj ET\nendstream endobj\n')
        assert b'BT <01> Tj ET' in b''.join(clients._pdf_streams(raw))


class TestToUnicodeMap:
    def test_parses_bfchar(self):
        raw = make_pdf({0x4F60: '你', 0x597D: '好'}, b'\x4F\x60\x59\x7D')
        table = clients._pdf_tounicode(clients._pdf_streams(raw))
        assert table[0x4F60] == '你'
        assert table[0x597D] == '好'

    def test_hex_to_text(self):
        assert clients._hex_to_text(b'4F60597D') == '你好'
        assert clients._hex_to_text(b'ZZZZ') == ''


class TestTextLayer:
    def test_extracts_two_byte_glyph_pdf(self):
        """2 字节字形编号（Identity-H，中文 PDF 最常见）。"""
        text = '这是一个可以用于测试的PDF文本层内容，需要有足够的长度才能通过四十个字符的门槛。'
        codes = {0x1000 + i: ch for i, ch in enumerate(text)}
        glyphs = b''.join(c.to_bytes(2, 'big') for c in codes)
        raw = make_pdf(codes, glyphs)
        out = clients.pdf_text_layer(_write(raw))
        assert out == text

    def test_extracts_one_byte_glyph_pdf(self):
        """★ 回归：1 字节字形编号（子集字体）。写死 2 字节会把这类 PDF 解成乱码。"""
        text = '这是一个可以用于测试的PDF文本层内容，需要有足够的长度才能通过四十个字符的门槛。'
        codes = {(i % 200) + 1: ch for i, ch in enumerate(text)}
        glyphs = bytes(codes.keys())
        raw = make_pdf(codes, glyphs, width=2)   # 映射表按 2 字节写，正文 1 字节
        out = clients.pdf_text_layer(_write(raw))
        assert out == text

    def test_scanned_pdf_returns_empty(self):
        """没有文本层（扫描件）→ 空串，调用方回退视觉模型。"""
        raw = b'%PDF-1.4\n1 0 obj << /Subtype /Image >> endobj\n%%EOF\n'
        assert clients.pdf_text_layer(_write(raw)) == ''

    def test_garbage_file_returns_empty(self):
        assert clients.pdf_text_layer(_write(b'MZ\x90\x00 not a pdf at all')) == ''


class TestProseGate:
    """
    ★ 核心安全网：字形映射错位时解出来的是"看着像中文的乱码"。
    这种文本必须被识别出来并丢弃，否则会静默污染事实库。
    """

    def test_real_chinese_passes(self):
        assert clients._looks_like_prose(
            '这是一个用于验证闸门的句子，我的意思是它应该被判为可信的文本内容，'
            '因为里面包含了很多常用字，和正常的行文习惯是一致的。' * 2)

    def test_scrambled_glyphs_rejected(self):
        scrambled = ('道址箱七道址箱非秒响址七七非安强敏密严隔响秒响安强敏离杜严隔绝久严隔死循'
                     '埋九安强划基严隔础续音多轮杂杜安强历史杜隔严非安强绩归法') * 3
        assert not clients._looks_like_prose(scrambled), '错位乱码必须被判为不可信'

    def test_english_passes(self):
        assert clients._looks_like_prose(
            'Senior iOS Engineer with five years of experience in the fintech industry '
            'and a track record of shipping products to the App Store.' * 2)

    def test_short_snippet_rejected(self):
        assert not clients._looks_like_prose('太短了')


def _write(data: bytes):
    """写到临时文件（pdf_text_layer 收 Path）。"""
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / 'x.pdf'
    p.write_bytes(data)
    return p


class TestScannedPdfPath:
    """
    ★ 回归：视觉模型**不接受 PDF**（实测报 InvalidParameter:
    The image format is illegal），所以扫描件必须自己先渲染成图片。
    """

    def test_pages_render_to_images(self, tmp_path):
        pdfium = pytest.importorskip('pypdfium2')
        pytest.importorskip('PIL')
        doc = pdfium.PdfDocument.new()
        for _ in range(3):
            doc.new_page(200, 200)
        src = tmp_path / 'scan.pdf'
        doc.save(str(src))

        pages, total = clients.pdf_pages_to_images(src)
        assert total == 3
        assert len(pages) == 3
        for p in pages:
            assert p.exists() and p.stat().st_size > 500
            assert p.read_bytes()[:3] == b'\xff\xd8\xff'      # JPEG 魔数

    def test_respects_page_cap(self, tmp_path):
        """页数超上限时只读前 N 页——但必须让调用方知道总共多少页。"""
        pdfium = pytest.importorskip('pypdfium2')
        pytest.importorskip('PIL')
        doc = pdfium.PdfDocument.new()
        for _ in range(5):
            doc.new_page(200, 200)
        src = tmp_path / 'big.pdf'
        doc.save(str(src))

        pages, total = clients.pdf_pages_to_images(src, max_pages=2)
        assert total == 5 and len(pages) == 2

    def test_read_pdf_uses_text_layer_when_available(self, monkeypatch):
        """有文本层就不该走视觉（快 10 倍且省钱）。"""
        called = {'vision': 0}

        def fake_read_image(path, prompt):
            called['vision'] += 1
            return '{}'

        monkeypatch.setattr(clients, 'pdf_text_layer', lambda p: '正文' * 50)
        monkeypatch.setattr(clients, 'read_image', fake_read_image)
        monkeypatch.setattr(clients, 'chat', lambda *a, **k: '{"facts":[]}')
        out, how = clients.read_pdf(_write(b'x'), 'prompt')
        assert how == 'text-layer'
        assert called['vision'] == 0

    def test_read_pdf_falls_back_to_vision_for_scan(self, monkeypatch):
        monkeypatch.setattr(clients, 'pdf_text_layer', lambda p: '')
        monkeypatch.setattr(clients, 'pdf_pages_to_images',
                            lambda p, **k: ([_write(b'img')], 1))
        monkeypatch.setattr(clients, 'read_image',
                            lambda path, prompt: '{"summary":"s","kind":"k",'
                                                 '"facts":[{"text":"一条事实","locator":""}]}')
        out, how = clients.read_pdf(_write(b'x'), 'prompt')
        assert how == 'vision-scan'
        import json as _json
        data = _json.loads(out)
        assert data['facts'][0]['text'] == '一条事实'
        assert data['facts'][0]['locator'] == '第 1 页'      # 自动补页码

    def test_read_pdf_notes_when_pages_truncated(self, monkeypatch):
        """只读了前几页就必须在摘要里写清楚，不能让人以为读全了。"""
        monkeypatch.setattr(clients, 'pdf_text_layer', lambda p: '')
        monkeypatch.setattr(clients, 'pdf_pages_to_images',
                            lambda p, **k: ([_write(b'img')], 9))
        monkeypatch.setattr(clients, 'read_image',
                            lambda path, prompt: '{"summary":"摘要","kind":"k","facts":[]}')
        out, _ = clients.read_pdf(_write(b'x'), 'prompt')
        import json as _json
        assert '只读了前 1 页' in _json.loads(out)['summary']
