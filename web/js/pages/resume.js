/** 简历：版本列表 / 包装建议（核心）/ 编辑导出 */
import { api } from '../api.js';
import {
  el, empty, loading, toast, chip, block, blockHead, icon, ICONS,
  openSheet, closeSheet, timeAgo, confirmSheet,
} from '../ui.js';
import { navigate, refreshOverview } from '../app.js';

const GRADE_LABEL = { ok: '可直接采纳', need: '需要补充', risk: '不建议写' };
const GRADE_CHIP = { ok: 'green', need: 'yellow', risk: 'red' };
const DECISION_LABEL = { accepted: '已采纳', rejected: '已忽略', edited: '已改后采纳' };

export default async function resumeHub(view, ctx) {
  const { param } = ctx;
  if (param === 'pack') return pagePackaging(view, ctx);
  if (param === 'edit') return pageEditor(view, ctx);
  if (param === 'chat') return pageChat(view, ctx);
  return pageList(view, ctx);
}

/* ══════════ 列表 ══════════ */
async function pageList(view, { setTopbar }) {
  setTopbar({ title: '简历' });
  view.appendChild(loading());
  const [res, jobs] = await Promise.all([api.resumes(), api.jobs()]);
  view.innerHTML = '';

  view.appendChild(el('button', {
    class: 'btn btn--primary btn--block btn--lg', text: '＋ 新建简历',
    onclick: () => newResumeSheet(jobs.jobs),
  }));
  view.appendChild(el('button', {
    class: 'btn btn--soft btn--block btn--lg', text: '💬 和助手聊着改简历',
    onclick: () => navigate('#/resume/chat'),
  }));

  if (!res.resumes.length) {
    view.appendChild(empty('📄', '还没有简历。可以新建一份（默认从简历基线导入），或者先上传材料。'));
    return;
  }

  for (const r of res.resumes) {
    const linked = r.job_id ? `${r.company || ''} ${r.title || ''}`.trim() : '';
    view.appendChild(el('div', { class: 'job-card' }, [
      el('div', { class: 'qitem-head' }, [
        el('span', { class: 'job-card-company', text: r.name }),
        chip({ draft: '草稿', exported: '已导出', applied: '已投递' }[r.status] || r.status,
          r.status === 'draft' ? '' : 'green'),
      ]),
      /* 「未关联岗位」说白了就是：这份是通用简历，没指定投哪个岗位。
         关联之后，简历包装能直接对着那个岗位的 JD 提建议，出题也更准。
         所以这里既要把话说清楚，也要给个能改的地方。 */
      el('div', { class: 'job-card-title',
        text: linked ? `目标岗位：${linked}` : '通用简历（未指定目标岗位）' }),
      el('div', { class: 'job-card-meta' }, [
        el('span', { text: timeAgo(r.updated_at) }),
        el('span', { text: `${(r.content || '').length} 字` }),
      ]),
      !linked ? el('p', { class: 'body-txt',
        style: { fontSize: '12px', color: 'var(--muted)', margin: '2px 0 0' },
        text: '指定一个目标岗位后，包装建议会直接对着那份 JD 提，题库也按这个岗位出。' }) : null,
      // 四个按钮挤一行会把「包装建议」折行，改成两行两列
      el('div', { class: 'btn-grid' }, [
        el('button', {
          class: 'btn btn--primary', text: '包装建议',
          onclick: () => navigate(`#/resume/pack?resume_id=${r.id}`),
        }),
        el('button', {
          class: 'btn', text: linked ? '换目标岗位' : '指定目标岗位',
          onclick: () => linkJobSheet(r, jobs.jobs),
        }),
        el('button', {
          class: 'btn', text: '编辑',
          onclick: () => navigate(`#/resume/edit?id=${r.id}`),
        }),
        el('button', {
          class: 'btn btn--ghost', text: '删除',
          onclick: () => confirmSheet('删除简历', `确定删除「${r.name}」吗？` +
            '只删这份简历，相关的题库和面试记录会保留。', async () => {
            try {
              const out = await api.deleteResume(r.id);
              toast(`已删除（保留 ${out.kept_questions} 道题、${out.kept_interviews} 场面试）`);
              navigate(location.hash, { replace: true });
            } catch (e) { toast(`删除失败：${e.message}`); }
          }),
        }),
      ]),
    ]));
  }
}

function linkJobSheet(resume, jobs) {
  const box = el('div', {});
  if (!jobs.length) {
    box.appendChild(el('div', { class: 'notice notice--info' }, [
      el('div', { text: '还没有录入岗位。先到「岗位 → 录入岗位」加一个，再回来关联。' }),
    ]));
    openSheet('指定目标岗位', box);
    return;
  }
  box.appendChild(el('p', { class: 'body-txt',
    text: '指定之后，简历包装会对着这个岗位的 JD 提建议，题库也按它出题。' }));
  for (const j of jobs) {
    const current = resume.job_id === j.id;
    box.appendChild(el('button', {
      class: current ? 'btn btn--primary btn--block' : 'btn btn--block',
      text: `${j.title || '未命名'} @ ${j.company || ''}${current ? '（当前）' : ''}`,
      style: { marginBottom: '8px' },
      onclick: async () => {
        try {
          await api.patchResume(resume.id, { job_id: j.id });
          closeSheet(); toast('已关联'); navigate(location.hash, { replace: true });
        } catch (e) { toast(`关联失败：${e.message}`); }
      },
    }));
  }
  if (resume.job_id) {
    box.appendChild(el('button', {
      class: 'btn btn--ghost btn--block', text: '取消关联（改回通用简历）',
      onclick: async () => {
        try {
          await api.patchResume(resume.id, { job_id: null });
          closeSheet(); toast('已取消关联'); navigate(location.hash, { replace: true });
        } catch (e) { toast(`取消失败：${e.message}`); }
      },
    }));
  }
  openSheet('指定目标岗位', box);
}

async function newResumeSheet(jobs) {
  const name = el('input', { type: 'text', placeholder: '如：蚂蚁-AI工程师-金融智能版' });
  const jSel = el('select', {}, [el('option', { value: '', text: '— 不关联岗位 —' })].concat(
    jobs.map((j) => el('option', { value: String(j.id), text: `${j.title || '未命名'} @ ${j.company || ''}` }))));

  openSheet('新建简历', el('div', {}, [
    el('div', { class: 'notice notice--info' }, [
      el('div', { text: '默认会从「简历基线」导入正文内容，之后可以在编辑页修改。' }),
    ]),
    el('label', { class: 'fld' }, [el('span', { text: '简历名称' }), name]),
    el('label', { class: 'fld' }, [el('span', { text: '关联岗位（用于定制简历）' }), jSel]),
    el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '创建',
      style: { marginTop: '8px' },
      onclick: async () => {
        try {
          const out = await api.createResume({
            name: name.value.trim() || '未命名简历',
            job_id: jSel.value ? Number(jSel.value) : null,
          });
          closeSheet(); toast(`已创建（${out.chars} 字）`);
          navigate(location.hash, { replace: true });
        } catch (e) { toast(e.message); }
      },
    }),
  ]));
}

/* ══════════ 简历包装（核心页面）══════════ */
async function pagePackaging(view, { setTopbar, query }) {
  setTopbar({ title: '简历包装', back: true });
  view.appendChild(loading());
  const [jobs, resumes] = await Promise.all([api.jobs(), api.resumes()]);
  view.innerHTML = '';

  const resumeId = query.resume_id ? Number(query.resume_id) : null;
  if (!resumeId) {
    if (!resumes.resumes.length) {
      view.appendChild(empty('📄', '还没有简历，请先新建一份。'));
      return;
    }
    navigate(`#/resume/pack?resume_id=${resumes.resumes[0].id}`, { replace: true });
    return;
  }

  const resume = resumes.resumes.find((r) => r.id === resumeId);
  if (!resume) { view.appendChild(empty('⚠️', '简历不存在')); return; }

  /* 简历已经指定了目标岗位 → 直接用它，不再问一遍。
     这就是"关联岗位"的用处：省掉每次做包装都要重选一次。 */
  if (!query.job_id && resume.job_id) {
    const bound = jobs.jobs.find((j) => j.id === resume.job_id);
    if (bound) {
      navigate(`#/resume/pack?resume_id=${resumeId}&job_id=${resume.job_id}`,
               { replace: true });
      return;
    }
  }

  /* 未选岗位：先选 */
  if (!query.job_id) {
    view.appendChild(el('div', { class: 'block block-blue' }, [
      blockHead('选择目标岗位', null, 'title-blue'),
      el('p', { class: 'body-txt body-blue',
        text: '简历包装是「对照某个岗位」来改的，所以要先选岗位。' }),
    ]));
    if (!jobs.jobs.length) {
      view.appendChild(empty('💼', '还没有岗位。',
        el('button', { class: 'btn btn--primary', text: '去录入岗位',
          onclick: () => navigate('#/jobs/new') })));
      return;
    }
    const sorted = [...jobs.jobs].sort((a, b) =>
      (b.id === resume.job_id ? 1 : 0) - (a.id === resume.job_id ? 1 : 0));
    const sel = el('select', {}, sorted.map((j) =>
      el('option', { value: String(j.id),
        text: `${j.title || '未命名'} @ ${j.company || ''}` +
          (j.id === resume.job_id ? '（这份简历关联的岗位）' : '') })));
    view.append(
      el('label', { class: 'fld' }, [el('span', { text: `简历：${resume.name}` })]),
      el('label', { class: 'fld' }, [el('span', { text: '目标岗位' }), sel]),
      el('button', {
        class: 'btn btn--primary btn--block btn--lg', text: '生成修改建议',
        onclick: () => navigate(`#/resume/pack?resume_id=${resumeId}&job_id=${sel.value}`),
      }),
    );
    return;
  }

  const jobId = Number(query.job_id);
  const job = jobs.jobs.find((j) => j.id === jobId);
  const { suggestions, counts, total } = await api.suggestions(resumeId, jobId);

  /* 还没生成过 */
  if (!total) {
    view.appendChild(el('div', { class: 'block block-yellow' }, [
      blockHead('还没有修改建议', null, 'title-yellow'),
      el('p', { class: 'body-txt body-yellow',
        text: `将对照「${job?.title || ''} @ ${job?.company || ''}」分析这份简历。` }),
      el('p', { class: 'body-txt body-yellow',
        text: '生成时会读取你的档案和材料，只给有依据的建议——没有证据的地方不会替你编。' }),
    ]));
    const btn = el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '开始生成（约 20-40 秒）',
      onclick: async () => {
        btn.disabled = true; btn.textContent = '生成中…';
        try {
          const out = await api.generatePackaging(jobId, resumeId);
          toast(`生成了 ${out.total} 条建议`);
          navigate(location.hash, { replace: true });
        } catch (e) { toast(`失败：${e.message}`); btn.disabled = false; btn.textContent = '重试'; }
      },
    });
    view.appendChild(btn);
    return;
  }

  /* 上下文 + 三级统计 */
  view.appendChild(el('div', { class: 'ctx-card' }, [
    el('div', { class: 'ctx-title', text: `目标岗位：${job?.company || ''} ${job?.title || ''}`.trim() }),
    el('div', { class: 'ctx-meta',
      text: `使用简历：${resume.name} · 共 ${total} 条建议，待处理 ${counts.pending} 条` }),
  ]));

  view.appendChild(el('div', { class: 'block' }, [
    el('div', { class: 'stat-row' }, [
      el('div', { class: 'stat stat-green' }, [
        el('b', { text: String(counts.ok) }), el('span', { text: '可直接采纳' })]),
      el('div', { class: 'stat stat-yellow' }, [
        el('b', { text: String(counts.need) }), el('span', { text: '需要补充' })]),
      el('div', { class: 'stat stat-red' }, [
        el('b', { text: String(counts.risk) }), el('span', { text: '不建议写' })]),
    ]),
  ]));

  /* 建议列表 */
  for (const s of suggestions) {
    view.appendChild(renderSuggestion(s, resumeId));
  }

  if (counts.pending === 0) {
    view.appendChild(el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '应用已采纳的修改到简历',
      onclick: async () => {
        try {
          const out = await api.applyPackaging(resumeId);
          toast(`已应用 ${out.applied} 条`);
          navigate(`#/resume/edit?id=${resumeId}`);
        } catch (e) { toast(e.message); }
      },
    }));
  }
  await refreshOverview();
}

function renderSuggestion(s, resumeId) {
  const card = el('div', { class: 'sug-card' });

  card.appendChild(el('div', { class: 'sug-head' }, [
    el('span', { class: 'sug-title', text: s.location || '（未标注位置）' }),
    chip(GRADE_LABEL[s.grade], GRADE_CHIP[s.grade]),
  ]));

  /* 三段对比：现在 / 建议 / 为什么（缺数字时改成"缺什么"） */
  if (s.before_text) {
    card.appendChild(el('div', { class: 'cmp cmp-now' }, [
      el('span', { text: s.grade === 'risk' ? '这句话有问题' : '现在' }),
      el('p', { text: s.before_text }),
    ]));
  }
  if (s.after_text) {
    card.appendChild(el('div', { class: 'cmp cmp-sug' }, [
      el('span', { text: '建议' }), el('p', { text: s.after_text }),
    ]));
  }
  if (s.grade === 'need' && s.missing) {
    card.appendChild(el('div', { class: 'cmp cmp-miss' }, [
      el('span', { text: '缺什么' }), el('p', { text: s.missing }),
    ]));
  } else if (s.reason) {
    card.appendChild(el('div', { class: 'cmp cmp-why' }, [
      el('span', { text: '为什么' }), el('p', { text: s.reason }),
    ]));
  }

  if (s.evidence && s.evidence.length) {
    const det = el('details', { class: 'evi' });
    det.appendChild(el('summary', { text: `依据（${s.evidence.length}）` }));
    det.appendChild(el('ul', { class: 'suggest__evidence' },
      s.evidence.map((e) => el('li', { text: e }))));
    det.addEventListener('click', (e) => e.stopPropagation());
    card.appendChild(det);
  }

  /* 已处理 */
  if (s.decision !== 'pending') {
    card.appendChild(el('div', { class: 'sug-actions' }, [
      el('span', { class: `chip chip-${s.decision === 'rejected' ? '' : 'green'}`,
        text: DECISION_LABEL[s.decision] || s.decision }),
      el('button', {
        class: 'btn', text: '撤销',
        onclick: async () => {
          await api.decideSuggestion(s.id, 'pending');
          navigate(location.hash, { replace: true });
        },
      }),
    ]));
    return card;
  }

  /* 待处理 */
  const acts = el('div', { class: 'sug-actions' });
  if (s.grade === 'need') {
    /* 需要补充：直接在卡片里填数字，而不是让用户自己去找地方改 */
    const input = el('input', {
      class: 'opt-input', type: 'text',
      placeholder: '例如：P95 从 1.2s 降到 480ms',
    });
    card.appendChild(el('div', {}, [
      el('div', { class: 'opt-label', text: '补充一个具体数字（会替换掉建议里的占位）' }),
      input,
    ]));
    acts.appendChild(el('button', {
      class: 'btn btn--primary', text: '用我的数字',
      onclick: async () => {
        const v = input.value.trim();
        if (!v) { toast('先填一个数字'); return; }
        await api.decideSuggestion(s.id, 'edited',
          (s.after_text || s.before_text || '').replace(/【[^】]*】|X{2,}|_{2,}/g, v) || v);
        toast('已采纳'); navigate(location.hash, { replace: true });
      },
    }));
    acts.appendChild(el('button', {
      class: 'btn btn--soft', text: '用建议表述',
      onclick: () => editSuggestion(s),
    }));
    acts.appendChild(el('button', {
      class: 'btn btn--ghost', text: '跳过',
      onclick: async () => {
        await api.decideSuggestion(s.id, 'rejected');
        navigate(location.hash, { replace: true });
      },
    }));
  } else if (s.grade === 'risk') {
    acts.appendChild(el('button', {
      class: 'btn btn--danger', text: '删掉这句话',
      onclick: async () => {
        await api.decideSuggestion(s.id, 'accepted');
        toast('已标记删除'); navigate(location.hash, { replace: true });
      },
    }));
    acts.appendChild(el('button', {
      class: 'btn btn--ghost', text: '保留原样',
      onclick: async () => {
        await api.decideSuggestion(s.id, 'rejected');
        navigate(location.hash, { replace: true });
      },
    }));
  } else {
    acts.appendChild(el('button', {
      class: 'btn btn--primary', text: '采纳',
      onclick: async () => {
        await api.decideSuggestion(s.id, 'accepted');
        toast('已采纳'); navigate(location.hash, { replace: true });
      },
    }));
    acts.appendChild(el('button', {
      class: 'btn btn--soft', text: '改一下',
      onclick: () => editSuggestion(s),
    }));
    acts.appendChild(el('button', {
      class: 'btn btn--ghost', text: '不要',
      onclick: async () => {
        await api.decideSuggestion(s.id, 'rejected');
        navigate(location.hash, { replace: true });
      },
    }));
  }
  card.appendChild(acts);
  return card;
}

function editSuggestion(s) {
  const ta = el('textarea', {});
  ta.value = s.after_text || s.before_text || '';
  openSheet('改成什么样', el('div', {}, [
    el('div', { class: 'cmp cmp-now' }, [el('span', { text: '原文' }), el('p', { text: s.before_text })]),
    el('label', { class: 'fld', style: { marginTop: '12px' } },
      [el('span', { text: '你希望改成' }), ta]),
    el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '保存并采纳',
      onclick: async () => {
        try {
          await api.decideSuggestion(s.id, 'edited', ta.value);
          closeSheet(); toast('已保存');
          navigate(location.hash, { replace: true });
        } catch (e) { toast(e.message); }
      },
    }),
  ]));
}


/* ══════════ 简历对话助手 ══════════ */

async function pageChat(view, { setTopbar, query }) {
  setTopbar({ title: '简历助手', back: true });
  view.appendChild(loading());

  const [jobsRes, resumesRes, sess] = await Promise.all([
    api.jobs(), api.resumes(),
    api.resumeChatSession().catch(() => ({ messages: [], draft: '' })),
  ]);
  view.innerHTML = '';
  view.classList.add('chat-page');

  /* ★ 在改哪份简历：以前这里写死传 null，助手**根本没读过简历库**，
     它看到的只有材料里那批事实（也就是最早那张简历截图的解析结果）——
     所以怎么改都像在改最开始那一份。现在可以选，而且选择记在会话上。 */
  const library = resumesRes.resumes || [];
  let resumeId = query.resume_id ? Number(query.resume_id)
    : (sess.resume_id || (library[0] ? library[0].id : null));

  let jobId = query.job_id ? Number(query.job_id)
    : (sess.job_id || jobsRes.jobs[0]?.id || null);
  let draft = sess.draft || '';
  const history = [];

  const list = el('div', { class: 'chat-list' });
  const input = el('textarea', { class: 'chat-input', rows: 1,
    placeholder: '说说你想怎么改…' });
  const sendBtn = el('button', { class: 'btn btn--primary', text: '发送' });
  const fileInput = el('input', { type: 'file', accept: 'image/*,application/pdf', hidden: true });
  const attachBtn = el('button', { class: 'btn btn--ghost chat-attach', text: '📎',
    title: '上传截图或 PDF 作为资料' });

  function addBubble(role, text) {
    const node = el('div', { class: `chat-msg chat-msg--${role === 'user' ? 'me' : 'ai'}` }, [
      el('div', { class: 'chat-bubble', text }),
    ]);
    list.appendChild(node);
    list.scrollTop = list.scrollHeight;
    return node;
  }

  function addQuestionChips(questions) {
    const box = el('div', { class: 'chat-qbox' }, [
      el('div', { class: 'chat-qbox-label', text: questions.length > 1 ? '这几条帮我补一下？' : '想问你一句' }),
    ]);
    for (const q of questions) {
      box.appendChild(el('button', {
        class: 'chat-qchip', text: q,
        onclick: () => {
          input.value = input.value ? input.value : '';
          input.focus();
          input.placeholder = q.length > 24 ? q.slice(0, 24) + '…' : q;
          toast('照着这个问题说一句就行');
        },
      }));
    }
    list.appendChild(box);
    list.scrollTop = list.scrollHeight;
  }

  /* 草稿条：一直挂在顶部。用户随时能预览、随时能存，
     不用在聊天记录里往回翻找那份草稿。 */
  const draftBar = el('div', { class: 'draft-bar', hidden: !draft });
  function renderDraftBar() {
    draftBar.innerHTML = '';
    if (!draft) { draftBar.hidden = true; return; }
    draftBar.hidden = false;
    draftBar.append(
      el('div', { class: 'draft-bar-info' }, [
        el('strong', { text: '当前草稿' }),
        el('span', { text: `${draft.length} 字` }),
      ]),
      el('div', { class: 'draft-bar-acts' }, [
        el('button', { class: 'btn btn--soft btn--sm', text: '预览',
          onclick: () => previewDraft(draft) }),
        el('button', { class: 'btn btn--primary btn--sm', text: '保存',
          onclick: () => saveDraft(draft) }),
      ]),
    );
  }

  function previewDraft(md) {
    openSheet('简历草稿', el('div', {}, [
      el('div', { class: 'chat-draft-body', style: { maxHeight: '60vh' }, text: md }),
      el('button', {
        class: 'btn btn--primary btn--block btn--lg', text: '保存到简历列表',
        style: { marginTop: '12px' },
        onclick: () => saveDraft(md),
      }),
    ]));
  }

  function saveDraft(md) {
    const current = library.find((r) => r.id === resumeId);
    const nameInput = el('input', { type: 'text',
      value: current ? current.name : `简历助手稿 ${new Date().toLocaleDateString('zh-CN')}` });
    const box = el('div', {}, [
      el('p', { class: 'body-txt', text: current
        ? `你正在改「${current.name}」。可以覆盖它，也可以另存为新的一份。`
        : '保存后可以在「简历」列表里编辑、做包装建议。' }),
      el('label', { class: 'fld' }, [el('span', { text: '简历名称' }), nameInput]),
    ]);
    if (current) {
      box.appendChild(el('button', {
        class: 'btn btn--primary btn--block btn--lg', text: `覆盖「${current.name}」`,
        onclick: async () => {
          try {
            await api.patchResume(current.id, { name: nameInput.value.trim() || current.name,
                                                content: md });
            closeSheet(); toast('已更新'); navigate('#/resume');
          } catch (e) { toast(`保存失败：${e.message}`); }
        },
      }));
      box.appendChild(el('button', {
        class: 'btn btn--soft btn--block', text: '另存为新简历',
        style: { marginTop: '8px' },
        onclick: async () => {
          try {
            const out = await api.createResume({
              name: nameInput.value.trim() || '简历助手稿', job_id: jobId, content: md });
            closeSheet(); toast(`已保存（${out.chars} 字）`); navigate('#/resume');
          } catch (e) { toast(`保存失败：${e.message}`); }
        },
      }));
    } else {
      box.appendChild(el('button', {
        class: 'btn btn--primary btn--block btn--lg', text: '确认保存',
        onclick: async () => {
          try {
            const out = await api.createResume({
              name: nameInput.value.trim() || '简历助手稿', job_id: jobId, content: md });
            closeSheet(); toast(`已保存（${out.chars} 字）`); navigate('#/resume');
          } catch (e) { toast(`保存失败：${e.message}`); }
        },
      }));
    }
    openSheet('保存这份简历', box);
  }

  async function send(presetText) {
    const text = (presetText !== undefined ? presetText : input.value).trim();
    if (!text) return;
    if (presetText === undefined) { input.value = ''; input.style.height = '44px'; }
    addBubble('user', text);
    history.push({ role: 'user', content: text });

    const wait = addBubble('ai', '');
    wait.querySelector('.chat-bubble').appendChild(el('span', { class: 'typing' }, [
      el('i', {}), el('i', {}), el('i', {}),
    ]));
    sendBtn.disabled = true; sendBtn.textContent = '思考中';
    try {
      const out = await api.resumeChat(history, jobId, resumeId);
      wait.remove();
      // 空回复也必须说话，否则用户看到的是"发了消息没反应"
      addBubble('ai', out.reply || '这次没生成出内容，换个说法再试一次？');
      // 问题做成可点的小卡片：点一下就把这句话填进输入框，接着说下去就行。
      // 之前是把 8 个问题编号列一大段，看着像问卷，这就是"死板"的来源之一。
      if (out.questions && out.questions.length) {
        addQuestionChips(out.questions);
      }
      history.push({ role: 'assistant', content: out.reply || '' });
      if (out.draft_resume) {
        draft = out.draft_resume;
        renderDraftBar();
        toast('草稿已更新，可点右上「预览」查看');
      }
    } catch (e) {
      wait.remove();
      addBubble('ai', `出错了：${e.message}`);
    }
    sendBtn.disabled = false; sendBtn.textContent = '发送';
    list.scrollTop = list.scrollHeight;
  }

  sendBtn.onclick = () => send();
  input.oninput = () => {
    input.style.height = '44px';
    input.style.height = Math.min(132, input.scrollHeight) + 'px';
  };
  // 回车直接发（Shift+Enter 换行）
  input.onkeydown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  };
  attachBtn.onclick = () => fileInput.click();
  fileInput.onchange = async () => {
    const f = fileInput.files[0];
    if (!f) return;
    const wait = addBubble('ai', `正在读「${f.name}」…`);
    try {
      const { id } = await api.uploadMaterial(f);
      const out = await api.parseMaterial(id);
      wait.remove();
      addBubble('ai', `已读「${f.name}」，抽出 ${out.facts} 条事实。${out.summary ? '\n' + out.summary : ''}`);
      history.push({ role: 'user',
        content: `我刚上传了「${f.name}」，解析出 ${out.facts} 条事实（你现在能看到了）。请据此更新简历。` });
      await send('这份材料你看一下，有用的补进简历里');
    } catch (e) {
      wait.remove();
      addBubble('ai', `上传失败：${e.message}`);
    }
    fileInput.value = '';
  };

  const jobSel = el('select', {}, [el('option', { value: '', text: '不指定岗位' })].concat(
    jobsRes.jobs.map((j) => el('option', { value: String(j.id),
      text: `${j.title || '未命名'} @ ${j.company || ''}` }))));
  if (jobId) jobSel.value = String(jobId);
  jobSel.onchange = () => { jobId = jobSel.value ? Number(jobSel.value) : null; };

  const resumeSel = el('select', { title: '在改哪份简历' },
    [el('option', { value: '', text: library.length ? '从头写（不基于现有简历）' : '（简历库是空的）' })]
      .concat(library.map((r) => el('option', { value: String(r.id),
        text: `${r.name} · ${(r.content || '').length} 字` }))));
  if (resumeId) resumeSel.value = String(resumeId);
  resumeSel.onchange = () => {
    resumeId = resumeSel.value ? Number(resumeSel.value) : null;
    const picked = library.find((r) => r.id === resumeId);
    // 换了一份就明说一句，别让助手默默继续按旧的改
    addBubble('ai', picked
      ? `好，接下来按「${picked.name}」改。它的内容我已经读到了——你说改哪段就行。`
      : '好，这次不基于现有简历，我们从零开始写。');
  };

  const chips = el('div', { class: 'chat-chips' }, [
    el('button', { class: 'chip-btn', text: '生成完整简历',
      onclick: () => send('根据我的全部资料，生成一份可以直接投的完整简历') }),
    el('button', { class: 'chip-btn', text: '我还缺什么',
      onclick: () => send('对照目标岗位，指出我简历里还缺什么、哪些会被面试官问穿') }),
    el('button', { class: 'chip-btn', text: '这段先改短一点',
      onclick: () => send('把上面的问题按重要性排序，一次只问我一个，先问最关键的那个') }),
  ]);

  view.append(
    el('div', { class: 'chat-toolbar chat-toolbar--stack' }, [
      el('div', { class: 'chat-toolbar-row' }, [
        el('span', { class: 'chat-toolbar-label', text: '在改' }), resumeSel,
      ]),
      el('div', { class: 'chat-toolbar-row' }, [
        el('span', { class: 'chat-toolbar-label', text: '投' }), jobSel,
        el('button', { class: 'btn btn--ghost btn--sm', text: '重开',
          style: { flex: 'none' },
          onclick: () => confirmSheet('重开对话', '会开始一段新对话（旧对话仍保留在数据库里）。',
            async () => {
              await api.resumeChatReset();
              history.length = 0;
              list.innerHTML = '';
              draft = '';
              renderDraftBar();
              greet();
            }) }),
      ]),
    ]),
    draftBar,
    list,
    chips,
    el('div', { class: 'chat-inputbar' }, [fileInput, attachBtn, input, sendBtn]),
  );
  renderDraftBar();

  function greet() {
    addBubble('ai', '我是你的简历教练。目标只有一个：把这份简历改到能直接投。' +
      '\n你可以直接说「生成完整简历」，或者告诉我改哪一段；也可以传简历截图、证书、项目文档给我读。');
    addBubble('ai', '我会指出哪里会被面试官问穿，并且**不会替你编数字**——资料里没有的我会问你。');
  }

  // 恢复上次对话；没有历史才打招呼
  const past = sess.messages || [];
  if (past.length) {
    for (const m of past) {
      addBubble(m.role, m.content);
      if (m.questions && m.questions.length) addQuestionChips(m.questions);
      history.push({ role: m.role, content: m.content });
    }
    addBubble('ai', `（这是上次的对话，草稿 ${draft.length} 字还在。想重来点右上「重开」。）`);
  } else {
    greet();
  }
  list.scrollTop = list.scrollHeight;
}

/* ══════════ 编辑 ══════════ */
async function pageEditor(view, { setTopbar, query }) {
  setTopbar({ title: '编辑简历', back: true });
  view.appendChild(loading());
  let r;
  try { r = await api.resume(query.id); } catch (e) {
    view.innerHTML = ''; view.appendChild(empty('⚠️', e.message)); return;
  }
  view.innerHTML = '';

  view.appendChild(el('div', { class: 'tip-bar' }, [
    icon(ICONS.doc, 18),
    el('span', { text: '移动端适合查看和轻量改字；完整排版与预览建议在电脑上打开。' }),
  ]));

  const name = el('input', { type: 'text', value: r.name });
  const ta = el('textarea', { style: { minHeight: '340px', fontSize: '13px' } });
  ta.value = r.content || '';

  view.append(
    el('label', { class: 'fld' }, [el('span', { text: '简历名称' }), name]),
    el('label', { class: 'fld' }, [el('span', { text: '正文（Markdown）' }), ta]),
  );

  view.appendChild(el('div', { class: 'btn-row' }, [
    el('button', {
      class: 'btn btn--primary', text: '保存',
      onclick: async () => {
        await api.patchResume(r.id, { name: name.value, content: ta.value });
        toast('已保存');
      },
    }),
    el('button', {
      class: 'btn', text: '复制全文',
      onclick: async () => {
        try { await navigator.clipboard.writeText(ta.value); toast('已复制到剪贴板'); }
        catch { toast('复制失败，请手动选择'); }
      },
    }),
  ]));

  view.appendChild(el('div', { class: 'group-title', text: '预览（前 4000 字）' }));
  view.appendChild(el('div', {
    class: 'block',
    style: { whiteSpace: 'pre-wrap', fontSize: '13px', maxHeight: '380px', overflow: 'auto' },
    text: (r.content || '').slice(0, 4000),
  }));
}
