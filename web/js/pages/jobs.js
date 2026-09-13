/** 岗位：列表 / 录入 / 详情（四层拆解 + 匹配度矩阵 + 投递建议） */
import { api } from '../api.js';
import {
  el, empty, loading, toast, chip, block, blockHead, icon, ICONS,
  openSheet, clip, timeAgo, confirmSheet,
} from '../ui.js';
import { navigate } from '../app.js';

const STATUS_TEXT = {
  draft: '草稿', to_apply: '待投递', applied: '已投递',
  interview: '面试中', closed: '已结束',
};
const LAYERS = [
  ['gate', '硬门槛'], ['duty', '核心职责'], ['plus', '加分项'], ['hidden', '隐性偏好'],
];
const STRENGTH = { strong: '强', medium: '中', weak: '弱', none: '无' };
const WEIGHT = { high: '高', mid: '中', low: '低' };

/** 匹配度 → chip 颜色。60 分以下明确标红，别让人误以为"还行"。 */
function scoreChip(score) {
  if (!(score >= 0)) return '';
  if (score >= 80) return 'green';
  if (score >= 65) return 'blue';
  if (score >= 55) return 'yellow';
  return 'red';
}

export default async function jobs(view, { param, query, setTopbar }) {
  if (param === 'new') return renderNew(view, { setTopbar });
  if (param) return renderDetail(view, param, { setTopbar });
  return renderList(view, { setTopbar });
}

/* ─────────── 列表 ─────────── */
async function renderList(view, { setTopbar }) {
  setTopbar({ title: '岗位' });
  view.appendChild(loading());
  const { jobs } = await api.jobs();
  view.innerHTML = '';

  view.appendChild(el('button', {
    class: 'btn btn--primary btn--block btn--lg', text: '＋ 录入岗位',
    onclick: () => navigate('#/jobs/new'),
  }));

  if (!jobs.length) {
    view.appendChild(empty('💼', '还没有岗位。点上面录入一个岗位要求（粘贴文本或上传 JD 截图）。'));
    return;
  }

  for (const j of jobs) {
    const kind = scoreChip(j.score);
    view.appendChild(el('div', {
      class: 'job-card', style: { cursor: 'pointer' },
      onclick: () => navigate(`#/jobs/${j.id}`),
    }, [
      el('div', { class: 'qitem-head' }, [
        el('span', { class: 'job-card-company', text: j.title || '未命名岗位' }),
        j.score >= 0 ? chip(`${j.score} 分`, kind) : chip('未分析', ''),
      ]),
      el('div', { class: 'job-card-title',
        text: [j.company, j.city, j.salary].filter(Boolean).join(' · ') || '未填写公司信息' }),
      el('div', { class: 'job-card-meta' }, [
        el('span', { text: STATUS_TEXT[j.status] || j.status }),
        el('span', { text: [j.req_count ? `${j.req_count} 条要求` : '',
                            j.q_count ? `${j.q_count} 道题` : ''].filter(Boolean).join(' · ') }),
      ]),
      el('div', { class: 'sug-actions', style: { marginTop: '2px' } }, [
        el('button', {
          class: 'btn btn--soft btn--sm', text: '查看详情',
          onclick: (ev) => { ev.stopPropagation(); navigate(`#/jobs/${j.id}`); },
        }),
        el('button', {
          class: 'btn btn--ghost btn--sm', text: '删除',
          onclick: (ev) => {
            ev.stopPropagation();
            confirmSheet('删除岗位',
              `确定删除「${j.company || ''} ${j.title || ''}」吗？这个岗位的题库会一起删掉，简历保留。`,
              async () => {
                try { await api.deleteJob(j.id); toast('已删除'); navigate('#/jobs', { replace: true }); }
                catch (e) { toast(`删除失败：${e.message}`); }
              });
          },
        }),
      ]),
    ]));
  }
}

/* ─────────── 录入 ─────────── */
async function renderNew(view, { setTopbar }) {
  setTopbar({ title: '录入岗位', back: true });
  const state = { materialId: null, parsed: null };

  const company = el('input', { type: 'text', placeholder: '如：蚂蚁集团' });
  const title = el('input', { type: 'text', placeholder: '如：AI 工程师（金融智能）' });
  const city = el('input', { type: 'text', placeholder: '如：杭州' });
  const salary = el('input', { type: 'text', placeholder: '如：30-60K' });
  const raw = el('textarea', { placeholder: '把岗位要求原文粘贴到这里…' });
  const jdBox = el('div', {});
  const fileInput = el('input', { type: 'file', accept: 'image/*,application/pdf', hidden: true });
  const uploadBox = el('div', { class: 'dropzone' }, [
    el('span', { html: `<svg width="36" height="36" viewBox="0 0 20 20" fill="none">` +
      `<circle cx="10" cy="10" r="10" fill="#EEF2FF"/>` +
      `<path d="${ICONS.upload}" stroke="#3D6BFF" stroke-width="1.6" ` +
      `stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
    el('div', { class: 'up-title', text: '上传 JD 截图或 PDF' }),
    el('div', { class: 'up-sub', text: '招聘 App 里截图后直接上传，比手打准确' }),
  ]);

  uploadBox.onclick = () => fileInput.click();
  fileInput.onchange = async () => {
    const f = fileInput.files[0];
    if (!f) return;
    uploadBox.innerHTML = '';
    uploadBox.appendChild(el('div', { class: 'up-title', text: `已上传：${f.name}` }));
    uploadBox.appendChild(el('div', { class: 'up-sub', text: '正在识别岗位要求…（约 30 秒）' }));
    try {
      const { id } = await api.uploadMaterial(f, null, 'jd');
      state.materialId = id;
      // ★ 上传完**立刻解析**，并把识别出来的原文填进下面的输入框。
      // 之前只存 material_id 不解析，用户以为"保存后会解析"，其实什么也没发生。
      const out = await api.parseJd(id);
      if (!out.ok) throw new Error(out.error || '识别失败');
      state.parsed = out;
      uploadBox.innerHTML = '';
      uploadBox.appendChild(el('div', { class: 'up-title', text: `已识别：${f.name}` }));
      uploadBox.appendChild(el('div', { class: 'up-sub',
        text: `抽出 ${(out.raw_text || '').length} 字岗位原文、${out.facts || 0} 条要求` }));
      // 表单里没填的字段用识别结果补上，用户还能改
      if (!company.value) company.value = out.company || '';
      if (!title.value) title.value = out.title || '';
      if (!city.value) city.value = out.city || '';
      if (!salary.value) salary.value = out.salary || '';
      if (!raw.value) raw.value = out.raw_text || '';
      drawJdPreview();
    } catch (e) {
      uploadBox.innerHTML = '';
      uploadBox.appendChild(el('div', { class: 'up-title', text: `❌ 识别失败：${e.message}` }));
      uploadBox.appendChild(el('div', { class: 'up-sub', text: '可以把要求文字直接粘到下面，或再传一张更清楚的截图' }));
    }
  };

  /* 把识别出来的要求逐条列出来给用户过目——他要能看出"漏了哪条" */
  function drawJdPreview() {
    jdBox.innerHTML = '';
    const p = state.parsed;
    if (!p) return;
    const lines = (p.raw_text || '').split('\n').map((x) => x.trim()).filter(Boolean);
    jdBox.appendChild(el('div', { class: 'block block-green' }, [
      blockHead('识别结果（请核对）', chip(`${lines.length} 行`, 'green'), 'title-green'),
      el('p', { class: 'body-txt body-green',
        text: '这一份会作为岗位要求原文用于匹配度分析和出题。有漏的、认错的，直接在下面「岗位要求原文」里改。' }),
      el('div', { class: 'jd-preview', text: p.raw_text || '（没识别出文字）' }),
    ]));
  }

  const save = el('button', {
    class: 'btn btn--primary btn--block btn--lg', text: '保存岗位',
    onclick: async () => {
      if (!raw.value.trim() && !state.materialId) {
        toast('请粘贴岗位要求，或上传截图'); return;
      }
      save.disabled = true; save.textContent = '保存中…';
      try {
        const { id } = await api.createJob({
          company: company.value.trim(), title: title.value.trim(),
          city: city.value.trim(), salary: salary.value.trim(),
          raw_text: raw.value.trim(), material_id: state.materialId,
        });
        toast('已保存');
        navigate(`#/jobs/${id}`, { replace: true });
      } catch (e) {
        toast(`保存失败：${e.message}`); save.disabled = false; save.textContent = '保存岗位';
      }
    },
  });

  view.append(
    el('label', { class: 'fld' }, [el('span', { text: '公司' }), company]),
    el('label', { class: 'fld' }, [el('span', { text: '岗位名' }), title]),
    el('div', { class: 'btn-row' }, [
      el('label', { class: 'fld', style: { flex: 1 } }, [el('span', { text: '城市' }), city]),
      el('label', { class: 'fld', style: { flex: 1 } }, [el('span', { text: '薪资' }), salary]),
    ]),
    el('label', { class: 'fld' }, [el('span', { text: '岗位要求原文' }), raw]),
    fileInput, uploadBox, jdBox,
    el('div', { style: { marginTop: '16px' } }, [save]),
  );
}

/* ─────────── 详情 ─────────── */
async function renderDetail(view, jobId, { setTopbar }) {
  setTopbar({ title: '岗位详情', back: true });
  view.appendChild(loading());
  let job;
  try { job = await api.job(jobId); } catch (e) {
    view.innerHTML = ''; view.appendChild(empty('⚠️', e.message)); return;
  }
  view.innerHTML = '';
  setTopbar({ title: clip(job.title || '岗位详情', 14), back: true });

  /* 头部 + 评分（深色 hero，对齐设计稿 job-detail） */
  const hero = el('div', { class: 'job-hero' }, [
    el('div', { class: 'job-company', text: [job.company, STATUS_TEXT[job.status]].filter(Boolean).join(' · ') }),
    el('div', { class: 'job-title', text: job.title || '未命名岗位' }),
    el('div', { class: 'job-meta',
      text: [job.city, job.salary, job.material_name ? `JD 来源：${clip(job.material_name, 14)}` : '']
        .filter(Boolean).join(' · ') || '还没填城市和薪资' }),
  ]);
  if (job.score >= 0) {
    hero.appendChild(el('div', { class: 'job-score' }, [
      el('b', { text: String(job.score) }),
      el('div', {}, [
        el('span', { text: '匹配度 / 100' }),
        el('p', { text: verdictLine(job.score) }),
      ]),
    ]));
  } else {
    hero.appendChild(el('div', { class: 'job-score' }, [
      el('b', { text: '—' }), el('div', {}, [el('span', { text: '还没分析匹配度' })]),
    ]));
  }
  view.appendChild(hero);

  /* 对着哪份简历分析：默认最近更新的那份，也可以自己选。
     之前匹配度只对着"档案事实库"算，简历里改过的东西它根本看不见，
     用户新建了简历再分析，结果却一点不变——看着就像"没用我的简历"。 */
  const allResumes = job.all_resumes || [];
  let pickedResume = job.analyzed_resume_id
    || (allResumes[0] ? allResumes[0].id : null);
  const resumeSel = el('select', {}, [
    el('option', { value: '', text: allResumes.length ? '— 选一份简历 —' : '（还没有简历）' }),
  ].concat(allResumes.map((r) => el('option', {
    value: String(r.id),
    text: `${r.name}${r.job_id === job.id ? '（本岗位绑定）' : ''} · ${r.chars || 0} 字`,
  }))));
  if (pickedResume) resumeSel.value = String(pickedResume);

  if (allResumes.length) {
    view.appendChild(el('label', { class: 'fld' }, [
      el('span', { text: '对着哪份简历分析' }),
      resumeSel,
    ]));
    if (job.analyzed_resume_name) {
      view.appendChild(el('p', { class: 'body-txt',
        style: { fontSize: '12px', color: 'var(--muted)', marginTop: '-6px' },
        text: `上次分析用的是「${job.analyzed_resume_name}」（更新于 ${job.analyzed_resume_updated}）` }));
    }
  } else {
    view.appendChild(el('div', { class: 'notice notice--info' }, [
      el('div', { text: '还没有简历。匹配度是对着简历算的——先去「简历」新建一份，再来分析会更准。' }),
    ]));
  }

  /* 操作 */
  const analyzeBtn = el('button', {
    class: job.score >= 0 ? 'btn' : 'btn btn--primary',
    text: job.score >= 0 ? '重新分析' : '开始分析匹配度',
    onclick: () => runAnalyze(job.id, analyzeBtn, view, jobId, setTopbar,
                              resumeSel.value ? Number(resumeSel.value) : null),
  });
  view.appendChild(el('div', { class: 'btn-row' }, [
    analyzeBtn,
    el('button', {
      class: 'btn', text: '生成题库',
      onclick: () => generateQuestionsSheet(job.id, job.resumes),
    }),
    el('button', {
      class: 'btn', text: '投递状态',
      onclick: () => changeStatus(job.id, job.status, view, jobId, setTopbar),
    }),
  ]));

  /* 删除岗位 */
  view.appendChild(el('div', { class: 'btn-row' }, [
    el('button', {
      class: 'btn btn--ghost btn--block', text: '删除这个岗位',
      onclick: () => confirmSheet('删除岗位',
        `确定删除「${job.company || ''} ${job.title || ''}」吗？` +
        '岗位要求、匹配度分析、题库、投递与面试记录会一起删掉；' +
        '简历会保留（只是解除关联）。',
        async () => {
          try {
            const out = await api.deleteJob(job.id);
            toast(`已删除（连同 ${out.removed_questions} 道题；保留 ${out.kept_resumes} 份简历）`);
            navigate('#/jobs', { replace: true });
          } catch (e) { toast(`删除失败：${e.message}`); }
        }),
    }),
  ]));

  /* 四层拆解 + 匹配矩阵（一条要求一张卡） */
  if (job.requirements.length) {
    view.appendChild(el('div', { class: 'group-title', text: '匹配度矩阵' }));
    for (const [layer, label] of LAYERS) {
      const items = job.requirements.filter((r) => r.layer === layer);
      if (!items.length) continue;
      view.appendChild(el('div', { class: 'section-title', text: `${label} · ${items.length} 条` }));
      for (const r of items) {
        const strengthKind = { strong: 'green', medium: 'blue', weak: 'yellow', none: 'red' }[r.strength] || '';
        view.appendChild(el('div', { class: 'req-card' }, [
          el('div', { class: 'req-head' }, [
            el('span', { class: 'req-name', text: r.text }),
            el('span', { class: 'req-badges' }, [
              chip(`${WEIGHT[r.weight] || r.weight}权重`, r.weight === 'high' ? 'blue' : ''),
              chip(STRENGTH[r.strength] || r.strength, strengthKind),
            ]),
          ]),
          el('div', { class: 'req-evi' }, [
            el('span', { text: '我的证据' }),
            el('p', { text: r.evidence || '（事实库里没有对应证据）' }),
          ]),
          r.strategy ? el('div', { class: 'req-evi' }, [
            el('span', { text: '怎么答' }), el('p', { text: r.strategy }),
          ]) : null,
        ]));
      }
    }
  }

  /* 投递建议 */
  if (job.verdict) {
    view.appendChild(el('div', { class: 'block block-green' }, [
      blockHead('投递建议', null, 'title-green'),
      el('p', { class: 'body-txt body-green', text: job.verdict }),
    ]));
  }

  /* 缺口与补强：把「无/弱」的强要求拎出来 */
  const gaps = job.requirements.filter((r) => ['none', 'weak'].includes(r.strength)
    && r.weight === 'high');
  if (gaps.length) {
    view.appendChild(el('div', { class: 'block block-yellow' }, [
      blockHead('缺口与补强', null, 'title-yellow'),
      ...gaps.map((r) => el('p', { class: 'body-txt body-yellow',
        text: `· ${clip(r.text, 40)} → ${r.strategy || '需要补一个能拿出手的证据'}` })),
    ]));
  }

  /* 关联内容 */
  view.appendChild(el('div', { class: 'group-title', text: '关联' }));
  view.appendChild(el('div', { class: 'block' }, [
    el('div', { class: 'stat-row' }, [
      el('div', { class: 'stat' }, [el('b', { text: String(job.questions.total) }), el('span', { text: '题库题目' })]),
      el('div', { class: 'stat' }, [el('b', { text: String(job.questions.mastered) }), el('span', { text: '已掌握' })]),
      el('div', { class: 'stat' }, [el('b', { text: String(job.resumes.length) }), el('span', { text: '简历版本' })]),
      el('div', { class: 'stat' }, [el('b', { text: String(job.interviews.length) }), el('span', { text: '面试记录' })]),
    ]),
  ]));
  view.appendChild(el('div', { class: 'btn-row' }, [
    el('button', { class: 'btn', text: '去练习', onclick: () => navigate('#/interview/practice') }),
    el('button', { class: 'btn', text: '去面试', onclick: () => navigate('#/interview/mock') }),
    el('button', { class: 'btn', text: '改简历', onclick: () => navigate('#/resume') }),
  ]));

  /* JD 原文折叠 */
  if (job.raw_text) {
    view.appendChild(el('details', { class: 'evi' }, [
      el('summary', { text: '查看 JD 原文' }),
      el('div', { class: 'block', style: { whiteSpace: 'pre-wrap', fontSize: '13px' },
                  text: job.raw_text }),
    ]));
  }
}

/** 分数 → 一句话结论（设计稿里 hero 里那句）。 */
function verdictLine(score) {
  if (score >= 85) return '强匹配，优先投，简历按这个岗位重排叙事';
  if (score >= 70) return '值得投，补掉高权重短板后胜率更高';
  if (score >= 55) return '可以投，但要有被拒的准备，先补硬伤';
  return '不建议现在投，代价大于收益';
}

async function runAnalyze(jobId, btn, view, id, setTopbar, resumeId = null) {
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '分析中…（约 30-60 秒）';
  try {
    const r = await api.analyzeJob(jobId, resumeId);
    toast(`分析完成：匹配度 ${r.score}/100` +
      (r.resume_name ? `（对着「${r.resume_name}」）` : ''));
    await renderDetail(view, id, { setTopbar });
  } catch (e) {
    toast(`分析失败：${e.message}`);
    btn.disabled = false; btn.textContent = old;
  }
}

function changeStatus(jobId, current, view, id, setTopbar) {
  const box = el('div', {});
  for (const [k, label] of Object.entries(STATUS_TEXT)) {
    box.appendChild(el('button', {
      class: 'btn btn--block', text: label,
      style: { marginBottom: '8px' },
      onclick: async () => {
        await api.patchJob(jobId, k);
        await api.saveApplication({ job_id: jobId, status: k });
        toast('已更新');
        document.querySelector('#sheet').hidden = true;
        await renderDetail(view, id, { setTopbar });
      },
    }));
  }
  openSheet('设置投递状态', box);
}

/** 生成题库：先选简历 */
async function generateQuestionsSheet(jobId, resumes) {
  const { resumes: all } = await api.resumes();
  const list = all.length ? all : resumes;
  if (!list.length) {
    openSheet('生成题库', el('div', {}, [
      el('div', { class: 'notice' }, [
        el('strong', { text: '还没有简历' }),
        el('div', { text: '题库需要结合简历出题。请先到「简历」页新建一份。' }),
      ]),
      el('button', {
        class: 'btn btn--primary btn--block', text: '去新建简历',
        onclick: () => { document.querySelector('#sheet').hidden = true; navigate('#/resume'); },
      }),
    ]));
    return;
  }
  const box = el('div', {});
  box.appendChild(el('div', { style: { fontSize: '13px', color: 'var(--c-muted)',
    marginBottom: '10px' }, text: '选择题库要结合哪一份简历：' }));
  for (const r of list) {
    box.appendChild(el('button', {
      class: 'btn btn--block', text: r.name,
      style: { marginBottom: '8px' },
      onclick: async (ev) => {
        const b = ev.currentTarget;
        b.disabled = true; b.textContent = '生成中…（约 60-90 秒）';
        try {
          const out = await api.generateQuestions(jobId, r.id, '', 20);
          toast(`已生成 ${out.total} 道题`);
          document.querySelector('#sheet').hidden = true;
          location.hash = '#/interview/questions';
        } catch (e) {
          toast(`生成失败：${e.message}`);
          b.disabled = false; b.textContent = r.name;
        }
      },
    }));
  }
  openSheet('生成题库', box);
}
