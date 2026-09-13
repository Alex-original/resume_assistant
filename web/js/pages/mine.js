/** 我的：档案 / 项目 / 材料库 / 投递看板 / 设置 */
import { api } from '../api.js';
import {
  el, empty, loading, toast, chip, block, blockHead, icon, ICONS,
  openSheet, closeSheet, clip, timeAgo, confirmSheet,
} from '../ui.js';
import { navigate, refreshOverview, render } from '../app.js';

const STATUS_TEXT = {
  unconfirmed: '待确认', confirmed: '已确认', corrected: '已修正', obsolete: '已失效',
};
const APP_STATUS = {
  to_apply: '待投递', applied: '已投递', interview: '面试中', closed: '已结束',
};

export default async function mine(view, ctx) {
  const { param } = ctx;
  // 「我的档案」已经取消：事实就在它来源的那份材料里确认，不需要单独一个模块。
  // 老链接直接送到材料库，不让用户撞见一个空页面。
  if (param === 'profile') {
    toast('「我的档案」已合并到材料库——事实在它来源的材料里确认');
    return navigate('#/mine/materials', { replace: true });
  }
  if (param === 'projects') return pageProjects(view, ctx);
  if (param === 'materials') return pageMaterials(view, ctx);
  if (param === 'board') return pageBoard(view, ctx);
  if (param === 'settings') return pageSettings(view, ctx);
  if (param === 'changelog') return pageChangelog(view, ctx);
  return pageMenu(view, ctx);
}

/* ══════════ 菜单 ══════════ */
async function pageMenu(view, { setTopbar }) {
  setTopbar({ title: '我的' });

  const GROUPS = [
    ['资料', [
      ['材料库', '上传简历 / 岗位 / 项目材料并解析', 'materials', ICONS.upload],
      ['项目库', '项目要点与总结文档', 'projects', ICONS.briefcase],
    ]],
    ['求职', [
      ['投递看板', '投递进度与转化率', 'board', ICONS.right],
    ]],
    ['其他', [
      ['设置与密钥', '', 'settings', ICONS.check],
      ['变更记录', '', 'changelog', ICONS.doc],
    ]],
  ];

  for (const [group, items] of GROUPS) {
    view.appendChild(el('div', { class: 'group-title', text: group }));
    const card = el('div', { class: 'block', style: { padding: '6px 8px', gap: '0' } });
    for (const [label, sub, route, p] of items) {
      card.appendChild(el('div', {
        class: 'todo-row', style: { padding: '12px 8px', borderBottom: '1px solid var(--line)' },
        onclick: () => navigate(`#/mine/${route}`),
      }, [
        el('span', { style: { display: 'flex', color: 'var(--primary)' }, html:
          `<svg width="18" height="18" viewBox="0 0 20 20" fill="none"><path d="${p}" ` +
          `stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
        el('span', { style: { minWidth: '0' } }, [
          el('div', { text: label, style: { fontWeight: '500' } }),
          sub ? el('div', { style: { fontSize: '12px', color: 'var(--muted)' }, text: sub }) : null,
        ]),
        icon(ICONS.chevron, 16),
      ]));
    }
    // 去掉最后一行的分隔线
    const rows = card.querySelectorAll('.todo-row');
    if (rows.length) rows[rows.length - 1].style.borderBottom = '0';
    view.appendChild(card);
  }
}

// 原地重画：必须 force，否则会被"hash 未变不重渲染"挡掉
const reload = () => render({ force: true });

/* ══════════ 项目 ══════════ */
async function pageProjects(view, { setTopbar }) {
  setTopbar({ title: '项目库', back: true });
  view.appendChild(loading());
  const { projects } = await api.projects();
  view.innerHTML = '';

  view.appendChild(el('button', {
    class: 'btn btn--primary btn--block btn--lg', text: '＋ 新建项目',
    onclick: () => newProjectSheet(),
  }));
  view.appendChild(el('p', { class: 'body-txt',
    style: { fontSize: '12px', color: 'var(--muted)' },
    text: '项目要点从材料里抽出来，**确认过的**才会进入简历和面试出题。' }));

  if (!projects.length) {
    view.appendChild(empty('📁', '还没有项目。可以在「材料库」上传项目文档后自动抽取，也可以手动新建。'));
    return;
  }

  for (const p of projects) {
    // 已删除（obsolete）的要点不再展示——之前是全都渲染，点了删除条目还在，
    // 用户看到的现象就是"删除没生效"。
    const live = p.points.filter((pt) => pt.status !== 'obsolete');
    const removed = p.points.length - live.length;
    const doc = p.summary_doc || '';
    const docBox = el('div', { hidden: true,
      style: { whiteSpace: 'pre-wrap', fontSize: '13px', lineHeight: '21px',
               background: 'var(--bg)', borderRadius: '12px', padding: '14px' },
      text: doc });
    const docBtn = el('button', {
      class: 'btn btn--soft', text: doc ? '预览项目总结' : '生成项目总结',
      onclick: async (ev) => {
        const b = ev.currentTarget;
        if (doc) {
          docBox.hidden = !docBox.hidden;
          b.textContent = docBox.hidden ? '预览项目总结' : '收起';
          return;
        }
        b.disabled = true; b.textContent = '生成中…（约 10 秒）';
        try {
          const out = await api.projectSummary(p.id);
          docBox.textContent = out.summary_doc;
          docBox.hidden = false;
          b.disabled = false; b.textContent = '收起';
          toast(`已生成 ${out.chars} 字的项目总结`);
        } catch (e) {
          toast(`生成失败：${e.message}`);
          b.disabled = false; b.textContent = '生成项目总结';
        }
      },
    });

    view.appendChild(el('div', { class: 'block' }, [
      blockHead(p.name, chip(`${live.length} 条要点`,
        p.status === 'confirmed' ? 'green' : 'yellow'), 'strong'),
      p.role ? el('p', { class: 'body-txt', style: { color: 'var(--muted)' }, text: p.role }) : null,
      p.summary ? el('p', { class: 'body-txt', text: clip(p.summary, 160) }) : null,
      removed ? el('p', { class: 'body-txt',
        style: { fontSize: '12px', color: 'var(--muted)' },
        text: `已删除 ${removed} 条（不再参与简历和面试）` }) : null,
      el('p', { class: 'body-txt', style: { fontSize: '12px', color: 'var(--muted)' },
        text: doc ? '项目总结文档已生成，可预览'
                  : '生成一份项目总结：面试官会照着它挖细节，先自己看一遍' }),
      docBtn,
      docBox,
      el('div', { class: 'sug-actions' }, [
        el('button', {
          class: 'btn btn--ghost', text: '删除项目',
          onclick: () => confirmSheet('删除项目',
            `确定删除「${p.name}」吗？项目要点会一起删掉；上传的材料会保留（只解除关联）。`,
            async () => {
              try { await api.deleteProject(p.id); toast('已删除'); reload(); }
              catch (e) { toast(`删除失败：${e.message}`); }
            }),
        }),
      ]),
    ]));

    if (!live.length) {
      view.appendChild(el('p', { class: 'body-txt', style: { color: 'var(--muted)' },
        text: '这个项目已经没有要点。可以在「材料库」重新解析项目文档补回来。' }));
    }
    for (const pt of live.slice(0, 12)) {
      view.appendChild(el('div', { class: 'fact-card' }, [
        el('div', { text: pt.text }),
        el('span', { text: [pt.metric ? `数字：${pt.metric}` : '',
                            STATUS_TEXT[pt.status] || pt.status].filter(Boolean).join(' · ') }),
        el('div', { class: 'sug-actions', style: { marginTop: '4px' } }, [
          // 「确认」的作用：确认过的要点才会进简历和面试出题。
          // 已确认的就不再显示这个按钮，省得让人猜它是干嘛的。
          pt.status === 'confirmed' ? chip('已确认可用', 'green') : el('button', {
            class: 'btn btn--primary btn--sm', text: '确认可用',
            onclick: async () => { await api.patchPoint(pt.id, { status: 'confirmed' }); toast('已确认'); reload(); },
          }),
          el('button', {
            class: 'btn btn--ghost btn--sm', text: '删除',
            onclick: () => confirmSheet('删除要点', `确定删除「${clip(pt.text, 40)}」吗？`,
              async () => {
                try { await api.deletePoint(pt.id); toast('已删除'); reload(); }
                catch (e) { toast(`删除失败：${e.message}`); }
              }),
          }),
        ]),
      ]));
    }
  }
}

function newProjectSheet() {
  const name = el('input', { type: 'text', placeholder: '如：Trade Master 智能量化助手' });
  const role = el('input', { type: 'text', placeholder: '如：独立开发 / 主导后端' });
  const period = el('input', { type: 'text', placeholder: '如：2025.03 - 2025.08' });
  const summary = el('textarea', { placeholder: '一句话说明这个项目做了什么（可选）', rows: 3 });
  openSheet('新建项目', el('div', {}, [
    el('label', { class: 'fld' }, [el('span', { text: '项目名' }), name]),
    el('label', { class: 'fld' }, [el('span', { text: '我的角色' }), role]),
    el('label', { class: 'fld' }, [el('span', { text: '时间' }), period]),
    el('label', { class: 'fld' }, [el('span', { text: '简介' }), summary]),
    el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '创建',
      onclick: async () => {
        if (!name.value.trim()) { toast('先写个项目名'); return; }
        try {
          await api.createProject({
            name: name.value.trim(), role: role.value.trim(),
            period: period.value.trim(), summary: summary.value.trim(),
          });
          closeSheet(); toast('已创建'); reload();
        } catch (e) { toast(e.message); }
      },
    }),
  ]));
}

/* ══════════ 材料库 ══════════ */
async function pageMaterials(view, { setTopbar, query }) {
  setTopbar({ title: '材料库', back: true });
  view.appendChild(loading());
  const TAB = ['jd', 'resume', 'project'].includes(query.tab) ? query.tab : 'resume';

  /* 材料分三类存、也分三类看：
     岗位材料（JD）/ 简历材料 / 项目材料。
     之前只有两个页签、而且**页签根本不过滤列表**——点了还是全都在，
     用户当然会觉得"没分开存"。 */
  function classify(m) {
    if (m.doc_kind === 'jd' || m.job_id) return 'jd';
    if (m.doc_kind === 'resume') return 'resume';
    if (m.kind === 'archive' || m.kind === 'repo') return 'project';
    if (['report', 'prd', 'repo'].includes(m.doc_kind)) return 'project';
    if (m.project_id) return 'project';
    return 'project';        // 认不出来的按项目材料放，项目材料本来就最杂
  }

  async function draw() {
    const { materials } = await api.materials();
    const grouped = { jd: [], resume: [], project: [] };
    for (const m of materials) grouped[classify(m)].push(m);
    const list = grouped[TAB];
    view.innerHTML = '';

    view.appendChild(el('div', { class: 'seg-bar' }, [
      ['jd', '岗位材料'], ['resume', '简历材料'], ['project', '项目材料'],
    ].map(([key, label]) => el('button', {
      class: `seg${TAB === key ? ' active' : ''}`,
      text: `${label} ${grouped[key].length}`,
      onclick: () => navigate(`#/mine/materials?tab=${key}`),
    }))));

    const HINT = {
      jd: ['岗位材料就是招聘 JD 的截图或 PDF', '岗位分析、匹配度矩阵、面试题都从这里来。传得越准，后面的建议越贴岗位。'],
      resume: ['简历材料是各种版本的简历', '截图或 PDF 都行。解析出的事实进档案库，简历包装和出题都会用。'],
      project: ['项目材料决定面试官能不能往深里问',
        '传 GitHub 仓库地址或 zip 源码包，系统会自己读代码、抽出技术方案和量化指标。' +
        '模拟面试会拿这些细节追问，和只对着简历问完全是两回事。'],
    };
    view.appendChild(el('div', { class: 'notice notice--info' }, [
      el('strong', { text: HINT[TAB][0] }),
      el('div', { text: HINT[TAB][1] }),
    ]));

    /* 上传区 */
    const accept = TAB === 'project'
      ? 'application/zip,.zip,image/*,application/pdf'
      : 'image/*,application/pdf';
    const fileInput = el('input', { type: 'file', accept, hidden: true });
    const box = el('div', { class: 'dropzone' }, [
      el('span', { html: `<svg width="40" height="40" viewBox="0 0 20 20" fill="none">` +
        `<circle cx="10" cy="10" r="10" fill="#EEF2FF"/>` +
        `<path d="${ICONS.upload}" stroke="#3D6BFF" stroke-width="1.6" ` +
        `stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
      el('div', { class: 'up-title', text: {
        jd: '上传 JD 截图或 PDF',
        resume: '上传简历截图或 PDF',
        project: '上传项目文档 / zip 源码包',
      }[TAB] }),
      el('div', { class: 'up-sub', text: TAB === 'project'
        ? '支持 zip / png / jpg / pdf；zip 会自动解包读代码'
        : '支持 png / jpg / jpeg / webp / pdf' }),
    ]);
    box.onclick = () => fileInput.click();
    fileInput.onchange = async () => {
      const f = fileInput.files[0];
      if (!f) return;
      box.textContent = '上传中…';
      try {
        const { id } = await api.uploadMaterial(f, null, TAB);
        toast('已上传，开始解析…');
        await draw();
        const cards = view.querySelectorAll('[data-mid]');
        const target = [...cards].find((c) => c.dataset.mid === String(id));
        if (target) target.querySelector('button')?.click();
      } catch (e) { toast(`上传失败：${e.message}`); await draw(); }
    };
    view.append(fileInput, box);

    /* GitHub 仓库：贴地址就能读代码，比让用户自己打包省事 */
    const urlInput = el('input', {
      type: 'text', placeholder: 'https://github.com/用户名/仓库名',
    });
    const urlBtn = el('button', {
      class: 'btn btn--primary', text: '拉取仓库',
      onclick: async (ev) => {
        const b = ev.currentTarget;
        const u = urlInput.value.trim();
        if (!u) { toast('先贴一个仓库地址'); return; }
        b.disabled = true; b.textContent = '拉取中…';
        try {
          const out = await api.materialFromUrl(u);
          toast(`已拉取 ${(out.bytes / 1024).toFixed(0)} KB，开始解析…`);
          await draw();
          const cards = view.querySelectorAll('[data-mid]');
          const target = [...cards].find((c) => c.dataset.mid === String(out.id));
          if (target) target.querySelector('button')?.click();
        } catch (e) {
          toast(`拉取失败：${e.message}`);
          b.disabled = false; b.textContent = '拉取仓库';
        }
      },
    });
    if (TAB === 'project') {
      view.appendChild(el('label', { class: 'fld' }, [
        el('span', { text: '或者贴 GitHub 仓库地址' }),
        el('div', { class: 'btn-row' }, [urlInput, el('div', { style: { flex: '0 0 104px' } }, [urlBtn])]),
      ]));
    }

    if (!list.length) {
      view.appendChild(empty('📎', {
        jd: '还没有岗位材料。去「岗位 → 录入岗位」传一张 JD 截图，或者直接在这里上传。',
        resume: '还没有简历材料。传一份简历截图或 PDF 就行。',
        project: '还没有项目材料。贴一个 GitHub 仓库地址，或者传 zip 源码包。',
      }[TAB]));
      return;
    }

    view.appendChild(el('div', { class: 'group-title', text: `${list.length} 份` }));
    for (const m of list) {
      const st = { pending: '待解析', parsing: '解析中', done: '已解析', failed: '解析失败' }[m.status];
      const stKind = { pending: 'none', parsing: 'need', done: 'ok', failed: 'risk' }[m.status];
      const card = el('div', { class: 'block', dataset: { mid: String(m.id) } }, [
        el('div', { class: 'queue-item' }, [
          el('span', { class: 'file-icon', html:
            `<svg width="18" height="18" viewBox="0 0 20 20" fill="none">` +
            `<path d="${ICONS.doc}" stroke="#3D6BFF" stroke-width="1.6" ` +
            `stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
          el('div', { class: 'file-info' }, [
            el('b', { text: clip(m.filename, 24) }),
            el('span', { text: m.fact_count ? `${st} · 发现 ${m.fact_count} 条事实` : st }),
          ]),
          chip(st, stKind === 'ok' ? 'green' : stKind === 'risk' ? 'red' : stKind === 'need' ? 'yellow' : ''),
        ]),
        m.summary ? el('p', { class: 'body-txt', text: m.summary }) : null,
        m.error ? el('div', { class: 'notice notice--danger', style: { marginBottom: 0 } }, [
          el('div', { text: m.error }),
        ]) : null,
        /* 一份材料一条「确认」：确认的单位是材料，不是 121 条事实。
           以前要把事实提升进「我的档案」再逐条点确认，没人受得了。 */
        m.status === 'done' ? el('div', { class: 'sug-actions' }, [
          m.doc_reviewed
            ? chip('已核对', 'green')
            : el('button', {
                class: 'btn btn--primary', text: `确认这批事实（${m.fact_count || 0} 条）`,
                onclick: async (ev) => {
                  const b = ev.currentTarget;
                  b.disabled = true; b.textContent = '确认中…';
                  try {
                    const out = await api.confirmMaterialFacts(m.id);
                    toast(`已确认 ${out.confirmed} 条事实`);
                    draw();
                  } catch (e) {
                    toast(`确认失败：${e.message}`);
                    b.disabled = false; b.textContent = '重试';
                  }
                },
              }),
          m.fact_count ? el('button', {
            class: 'btn btn--soft', text: '逐条看',
            onclick: () => showFacts(m.id),
          }) : null,
        ]) : null,
        el('div', { class: 'sug-actions' }, [
          el('button', {
            class: 'btn btn--soft',
            text: m.status === 'done' ? '重新解析' : '解析',
            onclick: async (ev) => {
              const b = ev.currentTarget;
              b.disabled = true; b.textContent = '解析中…（约 30 秒）';
              try {
                const r = await api.parseMaterial(m.id);
                toast(`解析完成：${r.facts} 条事实`);
              } catch (e) { toast(`解析失败：${e.message}`); }
              await draw();
            },
          }),
          el('button', {
            class: 'btn btn--ghost', text: '删除',
            onclick: () => confirmSheet('删除材料',
              `确定删除「${m.filename}」吗？它抽出来的 ${m.fact_count || 0} 条事实也会一起删掉。`,
              async () => {
                try {
                  const out = await api.deleteMaterial(m.id);
                  toast(`已删除（连带 ${out.facts_removed} 条事实）`);
                  draw();
                } catch (e) { toast(`删除失败：${e.message}`); }
              }),
          }),
        ]),
      ]);
      view.appendChild(card);
    }
  }

  await draw();
}

async function showFacts(materialId) {
  const m = await api.material(materialId);
  if (!m.facts.length) {
    openSheet('材料事实', el('div', { class: 'empty', text: '这份材料没有抽取到事实' }));
    return;
  }
  const ids = [];
  const list = el('div', {});
  for (const f of m.facts) {
    const cb = el('input', { type: 'checkbox' });
    cb.checked = f.status === 'unconfirmed';
    cb.onchange = () => {
      const i = ids.indexOf(f.id);
      if (cb.checked && i < 0) ids.push(f.id);
      if (!cb.checked && i >= 0) ids.splice(i, 1);
    };
    if (cb.checked) ids.push(f.id);
    list.appendChild(el('label', {
      class: 'fact-card', style: { display: 'flex', gap: '10px', alignItems: 'flex-start',
                                   cursor: 'pointer', marginBottom: '8px' },
    }, [
      cb,
      el('span', {}, [
        el('div', { text: f.text }),
        f.locator ? el('span', { text: `出处：${f.locator}` }) : null,
      ]),
    ]));
  }
  openSheet(`${m.filename} · ${m.facts.length} 条事实`, el('div', {}, [
    el('div', { class: 'notice notice--info' }, [
      el('div', { text: '勾选你认可的，点下面按钮加入档案。加入后可以在「我的档案」里继续编辑。' }),
    ]),
    list,
    el('button', {
      class: 'btn btn--primary btn--block', text: '加入档案',
      onclick: async () => {
        try {
          const out = await api.promoteFacts(materialId, ids);
          toast(`已加入 ${out.added} 条`);
          closeSheet();
        } catch (e) { toast(e.message); }
      },
    }),
  ]));
}

/* ══════════ 投递看板 ══════════ */
async function pageBoard(view, { setTopbar }) {
  setTopbar({ title: '投递看板', back: true });
  view.appendChild(loading());
  const [board, jobs] = await Promise.all([api.applications(), api.jobs()]);
  view.innerHTML = '';

  const s = board.stats;
  view.appendChild(el('div', { class: 'kpi-card' }, [
    el('div', { class: 'kpi' }, [el('b', { text: String(s.applied) }), el('span', { text: '在投' })]),
    el('div', { class: 'kpi' }, [el('b', { text: `${s.interview_rate}%` }), el('span', { text: '进面率' })]),
    el('div', { class: 'kpi' }, [el('b', { text: String(board.applications.length) }),
      el('span', { text: '总投递' })]),
  ]));

  const groups = { to_apply: [], applied: [], interview: [], closed: [] };
  for (const a of board.applications) (groups[a.status] || groups.to_apply).push(a);

  for (const [key, label] of Object.entries(APP_STATUS)) {
    const list = groups[key] || [];
    const body = el('div', { class: 'board-group__body' });
    if (!list.length) {
      body.appendChild(el('p', { class: 'body-txt', style: { color: 'var(--muted)' }, text: '暂无' }));
    }
    for (const a of list) {
      body.appendChild(el('div', { class: 'job-card', style: { cursor: 'pointer' },
        onclick: () => navigate(`#/jobs/${a.job_id}`) }, [
        el('div', { class: 'qitem-head' }, [
          el('span', { class: 'job-card-company', text: a.company || '未命名公司' }),
          chip(label, key === 'interview' ? 'green' : key === 'closed' ? '' : 'blue'),
        ]),
        el('div', { class: 'job-card-title', text: a.title || '' }),
        el('div', { class: 'job-card-meta' }, [
          el('span', { text: [a.city, a.salary].filter(Boolean).join(' · ') }),
          a.applied_at ? el('span', { text: `投递 ${a.applied_at.slice(0, 10)}` }) : null,
        ]),
      ]));
    }
    const head = el('div', { class: 'kanban-header' }, [
      el('span', { text: `${label}（${list.length}）` }),
      icon(ICONS.chevron, 16),
    ]);
    head.onclick = () => { body.hidden = !body.hidden; };
    view.appendChild(el('div', { class: 'kanban-group' }, [head, body]));
  }

  /* 未纳入看板的岗位 */
  const inBoard = new Set(board.applications.map((a) => a.job_id));
  const missing = jobs.jobs.filter((j) => !inBoard.has(j.id));
  if (missing.length) {
    view.appendChild(el('div', { class: 'group-title', text: '未加入看板的岗位' }));
    for (const j of missing) {
      view.appendChild(el('div', { class: 'job-card' }, [
        el('div', { class: 'job-card-company', text: j.title || '未命名' }),
        el('div', { class: 'job-card-title', text: j.company || '' }),
        el('button', {
          class: 'btn btn--soft btn--sm', text: '加入看板',
          onclick: async () => { await api.saveApplication({ job_id: j.id }); reload(); },
        }),
      ]));
    }
  }
}

/* ══════════ 设置 ══════════ */
async function pageSettings(view, { setTopbar }) {
  setTopbar({ title: '设置', back: true });
  view.appendChild(loading());
  let cfg;
  try { cfg = await api.settings(); } catch (e) {
    view.innerHTML = ''; view.appendChild(empty('⚠️', e.message)); return;
  }
  view.innerHTML = '';

  /* 账号 + 今日用量：多人共用一个 key 的时候，让他随时知道还剩多少额度 */
  try {
    const me = await api.me();
    view.appendChild(el('div', { class: 'block' }, [
      blockHead('我的账号', chip(me.user.is_admin ? '管理员' : '成员',
        me.user.is_admin ? 'blue' : ''), 'strong'),
      el('p', { class: 'body-txt', text: `${me.user.display_name}（${me.user.username}）` }),
      el('p', { class: 'body-txt', style: { fontSize: '12px', color: 'var(--muted)' },
        text: me.usage.quota > 0
          ? `今日 AI 用量 ${me.usage.used} / ${me.usage.quota} 次（明天自动重置）`
          : `今日 AI 用量 ${me.usage.used} 次（不限量）` }),
      el('button', {
        class: 'btn btn--soft btn--block', text: '退出登录',
        onclick: () => confirmSheet('退出登录', '确定要退出吗？', async () => {
          await api.logout();
          location.href = '/login';
        }),
      }),
    ]));
  } catch { /* 取不到就算了，不影响设置页其他内容 */ }

  view.appendChild(el('div', { class: 'group-title', text: '密钥状态' }));
  view.appendChild(el('div', { class: 'block', style: { gap: '14px' } },
    Object.entries(cfg.keys).map(([k, v]) => el('div', { class: 'queue-item' }, [
      el('div', { class: 'file-info' }, [
        el('b', { text: k }),
        el('span', { style: { fontFamily: 'monospace' }, text: v.masked }),
      ]),
      chip(v.configured ? '已配置' : '未配置', v.configured ? 'green' : 'red'),
    ]))));

  if (cfg.missing.length) {
    view.appendChild(el('div', { class: 'notice notice--danger' }, [
      el('strong', { text: `缺少 ${cfg.missing.join('、')}` }),
      el('div', { text: '把 key 填进下面任一 .env 文件，保存后立即生效（不用重启）。' }),
      el('ul', { style: { margin: '8px 0 0 18px', padding: 0, fontSize: '12px' },
                 text: '' }, cfg.env_files.map((f) =>
        el('li', { text: f, style: { fontFamily: 'monospace' } }))),
    ]));
  } else {
    view.appendChild(el('div', { class: 'notice notice--info' }, [
      el('strong', { text: '两个密钥都已配置' }),
      el('div', { text: 'DeepSeek 用于文本生成，DashScope 用于读图与语音。' }),
    ]));
  }

  view.appendChild(el('div', { class: 'group-title', text: '当前使用的模型' }));
  view.appendChild(el('div', { class: 'block' }, Object.entries(cfg.models).map(([k, v]) =>
    el('div', { class: 'queue-item' }, [
      el('div', { class: 'file-info' }, [
        el('b', { text: { text: '文本', vision: '视觉', asr: '语音识别', tts: '语音合成' }[k] || k }),
        el('span', { style: { fontFamily: 'monospace' }, text: v }),
      ]),
    ]))));

  view.appendChild(el('div', { class: 'group-title', text: '配置文件位置' }));
  view.appendChild(el('div', { class: 'block' }, cfg.env_files.map((f) =>
    el('div', { style: { fontFamily: 'monospace', fontSize: '12px',
                          wordBreak: 'break-all' }, text: f }))));

  view.appendChild(el('button', {
    class: 'btn btn--soft btn--block', text: '重新检查',
    onclick: () => navigate(location.hash, { replace: true }),
  }));
}

/* ══════════ 变更记录 ══════════ */
async function pageChangelog(view, { setTopbar }) {
  setTopbar({ title: '变更记录', back: true });
  view.appendChild(loading());
  const { items } = await (await fetch('/api/changelog?limit=100')).json();
  view.innerHTML = '';

  if (!items.length) {
    view.appendChild(empty('🕘', '还没有变更记录。你每次确认或修改内容都会记录在这里。'));
    return;
  }
  const ENTITY = {
    profile_field: '档案字段', project: '项目', project_point: '项目要点',
    material: '材料', material_fact: '材料事实', job: '岗位', resume: '简历',
    resume_suggestion: '简历建议', question: '题目', interview: '面试',
    application: '投递', practice_session: '练习',
  };
  const ACTION = {
    confirm: '确认', update: '修改', create: '新建', delete: '删除',
    obsolete: '标记失效', promote: '加入档案', parse: '解析', accept: '采纳',
    reject: '忽略', edit: '改后采纳', generate: '生成', apply: '应用', analyze: '分析',
  };
  for (const it of items) {
    view.appendChild(el('div', { class: 'fact-card' }, [
      el('div', { text: `${ENTITY[it.entity] || it.entity} · ${ACTION[it.action] || it.action}` }),
      it.before || it.after ? el('span', {
        text: `${clip(it.before, 40) || '（空）'} → ${clip(it.after, 40) || '（空）'}` }) : null,
      el('span', { class: 'field-time', text: timeAgo(it.at) }),
    ]));
  }
}
