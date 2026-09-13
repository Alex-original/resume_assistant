/** 工作台：进度概览 + 待办清单 + 快捷入口（对齐设计稿 home 屏） */
import { api } from '../api.js';
import { el, empty, loading, block, blockHead, chip, icon, ICONS } from '../ui.js';
import { refreshOverview, state, navigate } from '../app.js';

const QUICK = [
  ['录入岗位', '#3D6BFF', '#EEF2FF', ICONS.briefcase, '#/jobs/new'],
  ['传项目材料', '#E8A13C', '#FFF7E8', ICONS.upload, '#/mine/materials?tab=project'],
  ['模拟面试', '#22A06B', '#E8F7F0', ICONS.micCircle, '#/interview/mock'],
  ['简历包装', '#8B5CF6', '#F3EEFF', ICONS.doc, '#/resume'],
];

export default async function dashboard(view, { setTopbar }) {
  setTopbar({ title: '工作台' });
  view.appendChild(loading('加载中…'));

  let ov;
  let me = '';
  try {
    // 问候语里的名字从档案里取（不写死在代码里，公开仓库里不该有真名）
    const [o, prof] = await Promise.all([api.overview(), api.profile().catch(() => null)]);
    ov = o;
    me = prof?.sections?.flatMap((sec) => sec.fields)
      .find((f) => ['姓名', '名字'].includes(f.key))?.value || '';
  } catch { ov = null; }
  view.innerHTML = '';
  view.classList.add('dash');   // 桌面端变双列（窄屏无效果）

  if (!ov) {
    view.appendChild(empty('⚠️', '加载失败，请检查服务是否在运行'));
    return;
  }
  state.overview = ov;

  /* ── 问候 ── */
  const h = new Date().getHours();
  const greet = h < 6 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好'
    : h < 18 ? '下午好' : '晚上好';
  const bits = [];
  if (ov.jobs_total) bits.push(`已录入 ${ov.jobs_total} 个岗位`);
  if (ov.applications_total) bits.push(`在投 ${ov.applications_total} 个`);
  if (ov.questions_total) bits.push(`题库 ${ov.questions_total} 题`);
  view.appendChild(el('div', { class: 'home-header' }, [
    el('div', { class: 'home-greet', text: me ? `${greet}，${me}` : greet }),
    el('div', { class: 'home-sub', text: bits.join(' · ') || '还没有录入岗位，从「岗位 → 录入岗位」开始' }),
  ]));

  /* ── 进度概览 ──
     每个数字都必须是真的、而且点了有地方可去：
     - 「档案完成度」删掉：档案模块已经取消了，这个百分比没有任何意义
     - 「已掌握」以前用 total - review 算，26 道全未练却显示「26/26 已掌握」，
       现在老老实实数 status='mastered' */
  const stat = (value, label, cls = '') => el('div', { class: 'stat' }, [
    el('b', { class: cls, text: String(value) }), el('span', { text: label }),
  ]);
  view.appendChild(block([
    blockHead('进度概览', null, 'strong'),
    el('div', { class: 'stat-row stat-row--wrap' }, [
      stat(ov.jobs_total, '目标岗位'),
      stat(ov.resumes_total, '简历'),
      stat(ov.questions_mastered, `已掌握题目／共 ${ov.questions_total} 题`,
        ov.questions_mastered ? 'c-green' : ''),
      stat(ov.interviews_finished, ov.interviews_unreviewed
        ? `已面试／${ov.interviews_unreviewed} 场待复盘` : '已面试场次',
        ov.interviews_unreviewed ? 'c-blue' : ''),
    ]),
  ]));

  /* ── 待办清单 ── */
  const todos = ov.todos || [];
  const todoBlock = el('div', { class: 'block' }, [
    blockHead('待办清单', todos.length
      ? el('span', { class: 'count-badge', text: String(ov.pending_total) }) : null, 'strong'),
  ]);
  if (!todos.length) {
    todoBlock.appendChild(el('p', { class: 'body-txt', text: '当前没有待办，去录一个岗位或刷两道题。' }));
  } else {
    for (const t of todos) {
      todoBlock.appendChild(el('div', {
        class: 'todo-row',
        onclick: () => { navigate(t.action); },
      }, [
        el('i', { class: 'dot', style: { background: 'var(--primary)' } }),
        el('span', { text: `${t.label}：${t.count}` }),
        icon(ICONS.chevron, 16),
      ]));
    }
  }
  view.appendChild(todoBlock);

  /* ── 快捷入口 ── */
  const quick = el('div', { class: 'block' }, [blockHead('快捷入口', null, 'strong')]);
  const row = el('div', { class: 'quick-row' });
  for (const [label, color, bg, path, hash] of QUICK) {
    row.appendChild(el('button', {
      class: 'quick-item', style: { background: bg },
      onclick: () => { navigate(hash); },
    }, [
      el('span', {
        class: 'quick-icon', style: { background: color },
        html: `<svg width="16" height="16" viewBox="0 0 20 20" fill="none">` +
          `<path d="${path}" stroke="#fff" stroke-width="1.8" stroke-linecap="round" ` +
          `stroke-linejoin="round"/></svg>`,
      }),
      el('span', { text: label }),
    ]));
  }
  quick.appendChild(row);
  view.appendChild(quick);

  /* ── 材料总览：只留两样（岗位要求材料 / 简历·项目材料）──
     材料是这个产品的燃料：档案、题库、模拟面试全靠它。
     所以这里不堆数字，而是告诉用户"还缺什么、传了有什么用"。 */
  const jd = ov.materials_jd || 0;
  const resumeM = ov.materials_resume || 0;
  const projectM = ov.materials_project || 0;

  const jdCard = el('div', { class: 'block' }, [
    blockHead('岗位要求材料', chip(`${jd} 份`, jd ? 'blue' : 'yellow'), 'strong'),
    el('p', { class: 'body-txt',
      text: jd
        ? '招聘 JD 的截图或 PDF。岗位分析、匹配度矩阵、题库都从这里来。'
        : '还没有。把招聘 App 里的 JD 截图传上来，才能分析匹配度、生成有针对性的题。' }),
    el('button', {
      class: 'btn btn--primary btn--block', text: jd ? '再传一份 JD' : '上传 JD 截图',
      onclick: () => navigate('#/jobs/new'),
    }),
  ]);

  const projCard = el('div', { class: 'block' }, [
    blockHead('简历 / 项目材料', chip(`${resumeM + projectM} 份`, (resumeM + projectM) ? 'green' : 'yellow'), 'strong'),
    el('div', { class: 'stat-row', style: { marginTop: '-2px' } }, [
      el('div', { class: 'stat' }, [el('b', { text: String(resumeM) }), el('span', { text: '简历材料' })]),
      el('div', { class: 'stat' }, [el('b', { text: String(projectM) }), el('span', { text: '项目材料' })]),
    ]),
    el('div', { class: 'notice notice--info', style: { margin: '2px 0 0' } }, [
      el('strong', { text: '传项目材料，面试官才挖得动' }),
      el('div', { text: '模拟面试会照着材料里的细节追问。只传简历，面试官只能问简历上的几行字；' +
        '传了项目材料（GitHub 仓库、zip 源码包、项目文档），它才能问出「这个缓存为什么设 60 秒」这种真问题。' }),
    ]),
    el('div', { class: 'btn-row' }, [
      el('button', {
        class: 'btn btn--primary', text: '上传简历材料',
        onclick: () => navigate('#/mine/materials'),
      }),
      el('button', {
        class: 'btn btn--soft', text: '传项目材料',
        onclick: () => navigate('#/mine/materials?tab=project'),
      }),
    ]),
  ]);

  view.append(jdCard, projCard);

  // 首次使用时引导
  if (!ov.materials_total && !ov.jobs_total) {
    view.appendChild(el('div', { class: 'notice notice--info' }, [
      el('strong', { text: '从这里开始' }),
      el('div', { text: '① 到「我的 → 材料库」上传简历截图或项目文档；' }),
      el('div', { text: '② 到「岗位」录入一个目标岗位；' }),
      el('div', { text: '③ 生成题库后就能练习和模拟面试了。' }),
    ]));
  }

  await refreshOverview();
}
