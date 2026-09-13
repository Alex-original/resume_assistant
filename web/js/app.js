/** 应用入口：hash 路由 + 页面挂载 + 全局状态。 */

import { api } from './api.js';
import { el, $, toast } from './ui.js';

import dashboard from './pages/dashboard.js';
import jobs from './pages/jobs.js';
import interviewHub from './pages/interview.js';
import resumeHub from './pages/resume.js';
import mine from './pages/mine.js';

/* 页面注册表。key = 路由一级段。 */
const PAGES = {
  dashboard: { title: '工作台', render: dashboard },
  jobs: { title: '岗位', render: jobs },
  interview: { title: '面试', render: interviewHub },
  resume: { title: '简历', render: resumeHub },
  mine: { title: '我的', render: mine },
};

/* 渲染令牌：只让最后一次渲染的结果留在页面上 */
let renderToken = 0;
/* 正在进行的渲染：同一个路由同时只渲染一次。
   ★ 这条是为通话页加的：带 hash 直接打开时，hashchange 和 load 可能各触发一次 render，
   于是 pageCall 跑两遍、**两次都去请求语音合成**——听起来就是两条音轨同时在念题。 */
let inflight = null;
/* 上一次真正渲染过的路由。hash 没变就不要重渲染——
   ★ 带 hash 直接打开页面时，load 和 hashchange 会各触发一次 render，
   两次是**顺序**发生的（不是并发），所以 inflight 拦不住。
   结果 pageCall 跑两遍、两次都请求语音合成 = 两条音轨。 */
let lastRendered = null;

export const state = {
  overview: null,
  /** 全局「返回上一页」栈 */
  backStack: [],
};

/* ── 导航配置 ──
   移动端：底部胶囊标签栏，5 个一级入口。
   桌面端：侧边栏按分组列出全部页面（一级 + 常用二级），屏幕大就该一次点到位。
   icon 是 20×20 viewBox 的描边路径。 */
const TAB_ICONS = {
  dashboard: 'M3 8.5L10 3l7 5.5V17a1 1 0 01-1 1H4a1 1 0 01-1-1V8.5zM8 18v-5h4v5',
  jobs: 'M3 7.5A1.5 1.5 0 014.5 6h11A1.5 1.5 0 0117 7.5V15a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 15V7.5zM7.5 6V4.5A1.5 1.5 0 019 3h2a1.5 1.5 0 011.5 1.5V6M3 10.5h14',
  interview: 'M10 3a2.6 2.6 0 012.6 2.6v4a2.6 2.6 0 11-5.2 0v-4A2.6 2.6 0 0110 3zM4.8 9.4a5.2 5.2 0 0010.4 0M10 14.6V17.5M7 17.5h6',
  resume: 'M5.5 2.5h5.2L15 6.8V17a.5.5 0 01-.5.5h-9a.5.5 0 01-.5-.5V3a.5.5 0 01.5-.5zM10.5 2.6V7h4.4M7.5 11h5M7.5 14h3.5',
  mine: 'M10 6.5a3 3 0 100 6 3 3 0 000-6zM4 16.5c1.2-2.6 3.3-4 6-4s4.8 1.4 6 4',
};
const TABS = [
  { key: 'dashboard', label: '工作台', hash: '#/dashboard' },
  { key: 'jobs', label: '岗位', hash: '#/jobs' },
  { key: 'interview', label: '面试', hash: '#/interview' },
  { key: 'resume', label: '简历', hash: '#/resume' },
  { key: 'mine', label: '我的', hash: '#/mine' },
];

/** 侧边栏结构：**只列一级入口**，二级页面挂在它下面、只展开当前所在的那一节。
    之前把 14 个页面平铺成一组，一级二级混在一起，看着像目录不像导航。 */
const NAV = [
  { key: 'dashboard', label: '工作台', hash: '#/dashboard' },
  { key: 'jobs', label: '岗位', hash: '#/jobs' },
  { key: 'interview', label: '面试', hash: '#/interview' },
  { key: 'resume', label: '简历', hash: '#/resume' },
  { key: 'mine', label: '我的', hash: '#/mine' },
];

function svgIcon(path) {
  return `<svg viewBox="0 0 20 20" fill="none" aria-hidden="true">` +
    `<path d="${path}" stroke="currentColor" stroke-width="1.6" ` +
    `stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

/** 构建底部标签栏 + 桌面侧边栏（只需一次）。 */
function buildNav() {
  $('#tabbar').innerHTML = '<div class="tabbar__pill">' + TABS.map((t) =>
    `<a class="tabbar__item" data-route="${t.key}" href="${t.hash}">` +
    `${svgIcon(TAB_ICONS[t.key])}<span class="tabbar__label">${t.label}</span></a>`
  ).join('') + '</div>';

  const sidebar = $('#sidebar');
  sidebar.innerHTML =
    `<div class="sidebar-brand"><span class="brand-dot"></span>` +
    `<span class="brand-name">求职助手</span></div>` +
    `<nav class="sidebar-nav">` + NAV.map((item) =>
      `<a class="side-link" data-hash="${item.hash}" href="${item.hash}">${item.label}</a>`
    ).join('') + `</nav>` +
    `<div class="sidebar-foot">` +
    `<div class="side-count" id="side-count">待确认 0</div>` +
    `<div class="side-build" id="side-build">构建 —</div></div>`;

  /* 把构建时间写到侧边栏底部：刷新后如果这里出现了时间，
     说明浏览器加载的确实是新代码，不是缓存里的旧 JS。 */
  fetch('/api/version').then((r) => r.json()).then((v) => {
    const box = document.getElementById('side-build');
    if (box) box.textContent = `构建 ${v.build}`;
  }).catch(() => { /* 拿不到就算了，不影响使用 */ });
}

/** 高亮当前页。二级页面（如 #/interview/mock）要高亮它所属的一级入口。 */
function markNav(name) {
  const here = location.hash || '#/dashboard';
  document.querySelectorAll('.side-link').forEach((a) => {
    const h = a.dataset.hash;
    a.classList.toggle('active', h === here || h === `#/${name}`);
  });
}

/** 解析 hash：'#/jobs/12?x=1' → { name, param, query } */
function parseHash() {
  const raw = (location.hash || '#/dashboard').replace(/^#\/?/, '');
  const [pathPart, queryPart] = raw.split('?');
  const segs = pathPart.split('/').filter(Boolean);
  const name = segs[0] || 'dashboard';
  const param = segs[1] || '';
  const query = Object.fromEntries(new URLSearchParams(queryPart || ''));
  return { name, param, query };
}

export function navigate(hash, { replace = false } = {}) {
  const target = hash.startsWith('#') ? hash : `#/${hash.replace(/^\//, '')}`;
  const current = location.hash || '#/dashboard';
  if (current === target) {
    render({ force: true });    // 已经在目标页：这是显式的"原地刷新"
    return;
  }
  if (!replace) state.backStack.push(current);
  location.hash = target;
}

/* 每个二级页面归属的一级页面。
   返回时**优先回一级页**，而不是依赖内存里的 backStack——
   刷新过页面 / 从外部链接直接进来时栈是空的，那时回退会莫名跳到工作台。 */
/**
 * 当前页面的「上一级」。
 *
 * ★ 返回按钮按**层级**走，不按浏览历史走。
 * 踩过的坑：面试结束会跳到复盘页，而复盘页在历史里是"替换"掉了通话页的，
 * 于是按返回 → history.back() → 回到**模拟面试的设置页**，
 * 用户刚出考场又被扔回考场门口。
 * 层级是确定的：复盘页的上一级是面试记录，就这么简单。
 */
export function parentOf(name, param) {
  if (name === 'jobs') return param ? '#/jobs' : null;
  if (name === 'interview') {
    if (!param) return null;
    if (param === 'report') return '#/interview/records';   // 复盘 → 记录列表
    return '#/interview';
  }
  if (name === 'resume') return param ? '#/resume' : null;
  if (name === 'mine') return param ? '#/mine' : null;
  return null;                                              // 工作台是根
}

export function goBack() {
  const { name, param } = parseHash();
  const parent = parentOf(name, param);
  if (parent && parent !== location.hash) {
    state.backStack.length = 0;      // 按层级走，历史栈不再参与
    navigate(parent, { replace: true });
    return;
  }
  const prev = state.backStack.pop();
  if (prev && prev !== location.hash) {
    history.back();
    return;
  }
  navigate('#/dashboard', { replace: true });
}

/** 设置顶部栏。 */
export function setTopbar({ title, back = false, action = null }) {
  $('#topbar-title').textContent = title || '求职助手';
  const btn = $('#btn-back');
  // 一级页面默认不给返回箭头。但如果用户是**从应用内走进来的**
  // （回退栈里有上一页），就给一个——否则他会觉得"进得来出不去"。
  // 工作台是根，永远不给。
  const here = location.hash || '#/dashboard';
  const hasParent = state.backStack.length > 0 && here !== '#/dashboard';
  btn.hidden = !(back || hasParent);
  btn.onclick = goBack;
  const act = $('#btn-status');
  act.onclick = action ? action.handler : showStatusSheet;
  $('#status-dot').className = 'dot' + (state.overview?.pending_total > 0 ? ' warn' : '');
}

/** 右上角状态：待办一览。 */
async function showStatusSheet() {
  const { openSheet } = await import('./ui.js');
  const ov = state.overview || {};
  const rows = [
    ['待确认的档案', ov.fields_unconfirmed ?? 0],
    ['待确认的项目要点', ov.points_unconfirmed ?? 0],
    ['未处理的简历建议', ov.suggestions_pending ?? 0],
    ['待复习的题', ov.questions_review ?? 0],
    ['待复盘的面试', ov.interviews_unreviewed ?? 0],
  ];
  openSheet('当前状态', el('div', {}, rows.map(([k, v]) =>
    el('div', { class: 'dim' }, [
      el('span', { class: 'dim__name', text: k, style: { width: '150px' } }),
      el('span', { class: 'dim__num', text: String(v), style: { width: '40px' } }),
    ]))));
}

/** 渲染当前路由。 */
/**
 * 渲染当前路由。
 * @param {{force?: boolean}} opts force=true 表示"原地刷新"（页面内改完数据要重画时用）
 */
export async function render({ force = false } = {}) {
  const here = location.hash || '#/dashboard';
  if (!force && lastRendered === here) {
    return;                       // 已经渲染过这个路由，别重复渲染
  }
  if (inflight && inflight.hash === here) {
    return inflight.promise;      // 同一路由正在渲染，等它就行
  }

  lastRendered = here;
  const task = renderNow(here);
  inflight = { hash: here, promise: task };
  try {
    await task;
  } finally {
    if (inflight && inflight.promise === task) inflight = null;
  }
}

async function renderNow(here) {
  // ★ 离开通话页时要把那层全屏遮罩收掉。
  // 之前只有"正常结束面试"才会清理：用户按浏览器返回、或者直接改 hash 离开，
  // 那层 .call 就永远留在 DOM 里——再开一次面试就有**两层通话页**，
  // 两层各自在念题，听起来就是"两个声轨同时提问"。
  if (window.__ja_call_cleanup) {
    try { window.__ja_call_cleanup(); } catch { /* 已经清理过 */ }
    window.__ja_call_cleanup = null;
  }
  document.body.classList.remove('in-call');

  const { name, param, query } = parseHash();
  const page = PAGES[name];
  if (!page) {
    // 路由表里没有这个一级段：别静默回退到工作台（那会让"点了没反应"这种
    // bug 永远查不出来），明确报出来再兜底。
    console.warn('[router] 未知路由:', location.hash);
    toast(`页面不存在：${location.hash}`);
  }

  document.querySelectorAll('.tabbar__item').forEach((a) => {
    a.classList.toggle('active', a.dataset.route === name);
  });
  markNav(name);
  const fallback = page || PAGES.dashboard;

  /* ★ 每次渲染用一个**独立容器**，并且只认最后那一次。
     为什么必须这么做：render() 是异步的（页面内部要 await 接口）。
     如果在上一屏还没渲染完时又触发了渲染（手机上的双击最容易触发），
     两次渲染会往同一个 #view 里各追加一份内容——用户看到的就是
     「同一个按钮出现两次」。给每次渲染一个自己的容器之后，
     被取代的那次就算晚点回来，也只是写进了已经脱离文档的节点，看不见。 */
  const token = ++renderToken;
  const view = $('#view');
  view.innerHTML = '';
  view.scrollTop = 0;
  /* ★ 每次渲染一个**独立的 .page 容器**。
     渲染隔离是为了防重复渲染，但上一次我把 box 做成了没有类名的空 div，
     结果所有 `.page > *` 的规则（间距、max-width、双列栅格）全都落空了——
     表现就是"面试记录一条挨着一条粘在一起"。
     现在由这个 box 承担 .page 的布局职责，页面里写的 classList.add('dash') 也能生效。 */
  const box = el('div', { class: 'page' });
  view.appendChild(box);

  try {
    await fallback.render(box, { param, query, setTopbar });
    if (token !== renderToken) {
      // 这次渲染已经被更新的那次取代了：清掉自己，别留下重复内容
      box.remove();
    }
  } catch (err) {
    console.error(err);
    if (token === renderToken) {
      box.appendChild(el('div', { class: 'notice notice--danger' }, [
        el('strong', { text: '页面加载失败' }),
        el('div', { text: err.message || String(err) }),
      ]));
    }
  }
}

/** 刷新全局概览（顶部状态点 + 侧边栏待确认数）。 */
export async function refreshOverview() {
  try {
    state.overview = await api.overview();
    const warn = state.overview.pending_total > 0;
    $('#status-dot').className = 'dot' + (warn ? ' warn' : '');
    /* ★ 侧边栏页脚原来显示「待确认 121 · 待处理 17」。
       121 是 profile_field 里没确认的字段数——但「我的档案」模块已经取消了，
       那个数字既没有意义、也没有地方能处理它（纯粹是漏改）。
       现在直接列真实的待办：每一条都是能点进去处理的。 */
    const side = $('#side-count');
    if (side) {
      const todos = state.overview.todos || [];
      if (!todos.length) {
        side.innerHTML = '<span class="side-none">没有待办</span>';
      } else {
        side.innerHTML = '<div class="side-todo-title">待办 '
          + `<b>${state.overview.pending_total ?? 0}</b></div>`
          + todos.map((t) => `<a class="side-todo" href="${t.action}">`
              + `<span>${t.short || t.label}</span><b>${t.count}</b></a>`).join('');
      }
    }
  } catch { /* 忽略：概览失败不影响主流程 */ }
}

/* 启动 */
buildNav();
window.addEventListener('hashchange', render);
window.addEventListener('load', async () => {
  await refreshOverview();
  await render();
});

/* 暴露给页面内联事件使用 */
window.__ja = { navigate, goBack, render, refreshOverview, toast };
