"""
前端冒烟测试。

前端没有构建链（原生 ES Module），所以没有编译这一步能替我们发现低级错误。
这几条断言就是那道防线——它们能挡住的问题都是真实出现过的：

- 页面模块语法错误 → 浏览器里整页白屏，后端接口测试完全发现不了
- 静态资源 404（改名/移动文件忘了同步）→ 白屏
- 漏掉 `#view` / `#tabbar` 挂载点 → 页面渲染不出来
- 设计令牌被误删 → 视觉整体崩掉
- 临时调试文件混进交付物

依赖 node 做 ES Module 语法校验；没装 node 就跳过那一条，不让整套测试挂掉。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'web'
JS_FILES = sorted(WEB.glob('js/*.js')) 
PAGE_FILES = sorted(WEB.glob('js/pages/*.js'))


@pytest.fixture()
def client(temp_db):
    """不带登录态的客户端：主要用来测静态资源和登录页本身。"""
    from app import main
    return TestClient(main.app)


@pytest.fixture()
def auth_client(user):
    """已登录的客户端。测需要鉴权的接口时用它。"""
    from app import main
    c = TestClient(main.app)
    r = c.post('/api/auth/login', json={'username': 'tester', 'password': 'pw123456'})
    assert r.status_code == 200, f'测试登录失败：{r.text}'
    return c


class TestStaticAssets:
    def test_index_requires_login(self, client):
        """★ 未登录必须被挡在门外——公网部署后这是唯一的门。"""
        r = client.get('/', follow_redirects=False)
        assert r.status_code == 302
        assert r.headers['location'] == '/login'

    def test_api_requires_login(self, client):
        r = client.get('/api/overview')
        assert r.status_code == 401
        assert '登录' in r.json()['detail']

    def test_login_page_is_public(self, client):
        r = client.get('/login')
        assert r.status_code == 200
        assert '登录' in r.text

    def test_index_served(self, auth_client):
        client = auth_client
        r = client.get('/')
        assert r.status_code == 200
        assert 'id="view"' in r.text

    def test_index_has_mount_points(self, auth_client):
        client = auth_client
        """挂载点少一个就是白屏。"""
        html = client.get('/').text
        for anchor in ('id="view"', 'id="tabbar"', 'id="sidebar"',
                       'id="topbar-title"', 'id="btn-back"', 'id="toast"', 'id="sheet"'):
            assert anchor in html, f'index.html 缺少 {anchor}'

    @pytest.mark.parametrize('path', [
        '/static/css/style.css',
        '/static/js/app.js', '/static/js/api.js', '/static/js/ui.js',
        '/static/js/pages/dashboard.js', '/static/js/pages/jobs.js',
        '/static/js/pages/interview.js', '/static/js/pages/resume.js',
        '/static/js/pages/mine.js',
    ])
    def test_asset_served(self, client, path):
        assert client.get(path).status_code == 200, f'{path} 取不到'

    def test_no_debug_files_shipped(self):
        """临时调试页不能混进交付物（截图用的 iframe 壳、探针页）。"""
        junk = [p.name for p in WEB.rglob('*') if p.name.startswith('__')]
        assert junk == [], f'web/ 下有临时文件：{junk}'


class TestJsSyntax:
    """ES Module 语法必须能过 node 的解析——否则浏览器只会白屏。"""

    @pytest.mark.skipif(not shutil.which('node'), reason='没装 node，跳过语法校验')
    @pytest.mark.parametrize('path', JS_FILES + PAGE_FILES, ids=lambda p: p.name)
    def test_module_parses(self, path):
        # --check 只支持 CommonJS；ESM 用 import 让它真正解析一遍模块
        proc = subprocess.run(
            ['node', '--input-type=module', '-e', f'import("file://{path}")'],
            capture_output=True, text=True, timeout=30)
        err = (proc.stderr or '') + (proc.stdout or '')
        # 模块能在 node 里跑起来就会因为没有 DOM 而报错，那是预期的；
        # 只要不是语法/解析错误就算通过。
        assert 'SyntaxError' not in err and 'Cannot find module' not in err, \
            f'{path.name} 解析失败：{err[:300]}'

    def test_every_page_module_exports_default(self):
        for p in PAGE_FILES:
            src = p.read_text(encoding='utf-8')
            assert 'export default' in src, f'{p.name} 没有默认导出'


class TestDesignTokens:
    """设计稿的令牌与关键组件必须还在，否则视觉会整体走形。"""

    def test_tokens_present(self):
        css = (WEB / 'css' / 'style.css').read_text(encoding='utf-8')
        for token in ('--primary:     #3D6BFF', '--green:       #22A06B',
                      '--yellow:      #E8A13C', '--red:         #E05B4C',
                      '--dark:        #111318', '--bg:          #F6F7F9'):
            assert token in css, f'缺少设计令牌 {token}'

    def test_hidden_attribute_wins_over_display(self):
        """
        ★ 回归测试：作者样式里的 display 会盖掉 UA 的 [hidden]{display:none}，
        导致 #btn-back 这种 display:flex 的按钮永远藏不住（实测踩到过）。
        """
        css = (WEB / 'css' / 'style.css').read_text(encoding='utf-8')
        assert re.search(r'\[hidden\]\s*\{[^}]*display:\s*none\s*!important', css), \
            '缺少 [hidden]{display:none!important} 兜底'

    def test_responsive_breakpoint(self):
        css = (WEB / 'css' / 'style.css').read_text(encoding='utf-8')
        assert '@media (min-width: 1024px)' in css, '缺少桌面端断点'
        assert '.sidebar' in css and '.tabbar' in css


class TestNoAnswersInMockInterviewUI:
    """模拟面试是「考」，界面上不能出现标准答案。"""

    def test_call_page_has_no_answer_source(self):
        src = (WEB / 'js' / 'pages' / 'interview.js').read_text(encoding='utf-8')
        start = src.index('async function pageCall')
        end = src.index('async function pageRecords')
        call_src = src[start:end]
        for banned in ('renderQuestionBody', 'q.standard', 'key_points', 'followups'):
            assert banned not in call_src, f'通话页里出现了 {banned}，等于把答案递给考生'


class TestSingleEntryPerPage:
    """
    ★ 导航约定：二级页面**只能从它自己的一级页进**，导航栏不重复放入口。

    之前侧边栏把 14 个页面平铺成一组，一级二级混在一起；改成"一级 + 展开二级"
    之后又成了重复入口。现在的约定是：导航只有一级，二级页在它所属的一级页里。
    这条测试保证：任何二级页面都必须有一个页内入口，否则删掉导航入口后
    用户就再也进不去了。
    """

    # 二级页面 → 应该在哪个页面模块里找到它的入口
    SUB_PAGES = {
        '#/jobs/new': 'jobs.js',
        '#/interview/practice': 'interview.js',
        '#/interview/mock': 'interview.js',
        '#/interview/questions': 'interview.js',
        '#/interview/records': 'interview.js',
        '#/resume/pack': 'resume.js',
        '#/resume/chat': 'resume.js',
        '#/mine/projects': 'mine.js',
        '#/mine/materials': 'mine.js',
        '#/mine/board': 'mine.js',
        '#/mine/changelog': 'mine.js',
        '#/mine/settings': 'mine.js',
    }

    @pytest.mark.parametrize('hash_, page', sorted(SUB_PAGES.items()))
    def test_sub_page_has_entry_in_its_parent(self, hash_, page):
        src = (WEB / 'js' / 'pages' / page).read_text(encoding='utf-8')
        found = (f"'{hash_}" in src or f"`{hash_}" in src)
        if not found:
            # 「我的」下面的页面是拼出来的：`navigate(`#/mine/${route}`)` + 菜单表里的
            # ['材料库', '…', 'materials', ICONS.upload]。所以这里要求叶子名出现在
            # **菜单那种数组行**（该行含 `[`），否则路由分发那行 `param === 'materials'`
            # 也会命中，测试就永远绿了（这是实测发现的假绿）。
            section, _, leaf = hash_.lstrip('#/').partition('/')
            if f'#/{section}/' in src:
                found = any(
                    f"'{leaf}'" in line and ('[' in line or 'navigate(' in line)
                    for line in src.splitlines())
        assert found, \
            f'{hash_} 在 {page} 里没有入口：改成"导航只留一级"之后用户就进不去了'

    def test_sidebar_is_top_level_only(self):
        """侧边栏不能再出现二级入口（重复入口会被这条挡住）。"""
        src = (WEB / 'js' / 'app.js').read_text(encoding='utf-8')
        assert 'side-subs' not in src, '侧边栏又出现了二级项'
        assert 'side-link--sub' not in src

    def test_sidebar_has_five_top_level_items(self):
        src = (WEB / 'js' / 'app.js').read_text(encoding='utf-8')
        start = src.index('const NAV = [')
        end = src.index('];', start)
        nav = src[start:end]
        for h in ('#/dashboard', '#/jobs', '#/interview', '#/resume', '#/mine'):
            assert f"'{h}'" in nav, f'侧边栏缺少一级入口 {h}'
        assert nav.count("{ key:") == 5, '侧边栏应该只有 5 个一级入口'


class TestApiClientCoverage:
    """
    ★ 回归：页面里 `api.xxx()` 调的每个方法，api.js 里必须真的定义了。

    这个 bug 真的发生过：给 api.js 加 materialFromUrl 时，替换的锚点没匹配上，
    脚本却照样打印了"已加"——结果就是"拉取 GitHub 仓库"按钮点了报
    `api.materialFromUrl is not a function`。这种错误静态检查一次就能挡住，
    而且必须是自动的：光靠人眼扫 50 多个方法必漏。
    """

    def test_every_api_method_used_is_defined(self):
        api_src = (WEB / 'js' / 'api.js').read_text(encoding='utf-8')
        defined = set(re.findall(r'^\s{2}([A-Za-z_]\w*):', api_src, re.M))
        assert len(defined) > 30, 'api.js 解析异常，方法数太少'

        missing: dict[str, set[str]] = {}
        for f in sorted((WEB / 'js').rglob('*.js')):
            if f.name == 'api.js':
                continue
            src = f.read_text(encoding='utf-8')
            for name in set(re.findall(r'\bapi\.([A-Za-z_]\w*)\s*\(', src)):
                if name not in defined:
                    missing.setdefault(name, set()).add(f.name)
        assert not missing, '这些 api 方法没定义：' + '；'.join(
            f'api.{n}（{", ".join(sorted(fs))} 在用）' for n, fs in sorted(missing.items()))

    def test_no_unused_api_methods(self):
        """反向检查：api.js 里定义了但没人用的方法，多半是改了一半留下的。"""
        api_src = (WEB / 'js' / 'api.js').read_text(encoding='utf-8')
        defined = set(re.findall(r'^\s{2}([A-Za-z_]\w*):', api_src, re.M))
        used = set()
        for f in sorted((WEB / 'js').rglob('*.js')):
            if f.name != 'api.js':
                used |= set(re.findall(r'\bapi\.([A-Za-z_]\w*)\s*\(', f.read_text(encoding='utf-8')))
        unused = sorted(defined - used)
        # 允许少量确实是给页面直接调用、暂时没接上的；超过 5 个说明有问题
        assert len(unused) <= 5, f'有 {len(unused)} 个方法没人用：{unused}'


class TestModuleIdentity:
    """
    ★ 回归：入口和 import 必须用**同一个 URL**。

    踩过的坑：为了防止缓存，我给 index.html 里的入口加了 `?v=构建时间`，
    但页面模块里写的是 `import ... from '../app.js'`（不带参数）。
    ES Module 是按 URL 去重的，`app.js?v=xxx` 和 `app.js` 会被当成**两个模块
    各执行一遍**——于是有两份模块状态、两条渲染管线：页面内容出现两份、
    通话页同时请求两次语音合成（听起来就是两条音轨在念同一道题）。
    防缓存改用响应头（no-cache），这条路就堵死了。
    """

    def test_index_has_no_versioned_module_url(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        import re as _re
        for m in _re.finditer(r'(?:src|href)="(/static/[^"]+)"', html):
            assert '?' not in m.group(1), (
                f'入口资源带了查询参数：{m.group(1)}。'
                '模块 import 用的是不带参数的路径，浏览器会加载两份模块。')

    def test_no_versioned_js_imports(self):
        for f in (WEB / 'js').rglob('*.js'):
            src = f.read_text(encoding='utf-8')
            import re as _re
            for m in _re.finditer(r"from\s+'([^']+)'", src):
                assert '?' not in m.group(1), f'{f.name} 里的 import 带了查询参数：{m.group(1)}'


class TestPageLayoutContract:
    """
    ★ 回归：`.page` 必须落在**每次渲染出来的容器**上。

    踩过的坑：为了防重复渲染，我给每次渲染套了一层空 div，但没给它类名，
    于是所有 `.page > *` 的规则（卡片间距、max-width、桌面双列栅格）全部落空——
    表现是"面试记录一条挨着一条粘在一起"。
    """

    def test_view_is_just_a_mount_point(self):
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        import re as _re
        m = _re.search(r'<main([^>]*)id="view"', html)
        assert m, '找不到 #view'
        assert 'class="page"' not in m.group(1), \
            '#view 不该再带 page 类——page 是每次渲染的容器，不是挂载点'

    def test_render_container_has_page_class(self):
        src = (WEB / 'js' / 'app.js').read_text(encoding='utf-8')
        assert "el('div', { class: 'page' })" in src, \
            '渲染容器必须带 page 类，否则 .page > * 的布局规则全部失效'


class TestBackNavigationContract:
    """
    ★ 回归：返回按钮按**层级**走，不按浏览历史。

    踩过的坑：面试结束跳到复盘页时把通话页 replace 掉了，历史里复盘页的前一步
    是"模拟面试设置页"，于是点返回又被扔回考场门口。
    """

    def test_parent_map_covers_key_subpages(self):
        src = (WEB / 'js' / 'app.js').read_text(encoding='utf-8')
        assert 'function parentOf' in src, '应该用层级表决定返回目标'
        for rule in ("return '#/interview/records'", "return '#/interview'"):
            assert rule in src, f'缺少返回规则：{rule}'

    def test_finish_clears_back_stack(self):
        """面试结束后，「模拟面试设置页」不该再留在返回路径上。"""
        src = (WEB / 'js' / 'pages' / 'interview.js').read_text(encoding='utf-8')
        i = src.index('async function doFinish')
        seg = src[i:i + 900]
        assert 'state.backStack.length = 0' in seg, '结束面试后要清空返回栈'
