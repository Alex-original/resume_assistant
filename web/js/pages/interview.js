/**
 * 面试：入口聚合 / 题库 / 练习模式 / 模拟面试 / 记录复盘
 *
 * 练习模式：每题自己选「我先答」或「直接看答案」——学
 * 模拟面试：全屏通话界面，全程不给答案，追问到底——考
 *
 * 视觉对齐设计稿：练习答题页(practice/record)、通话页(interview)、
 * 评分页(interview-score)、复盘(review)、题库(bank)。
 */
import { api } from '../api.js';
import {
  el, empty, loading, toast, chip, block, blockHead, icon, ICONS, wave,
  progressRow, pct, openSheet, closeSheet, clip, timeAgo,
  createRecorder, transcribe, speak, stopSpeaking, primeAudio, confirmSheet,
  VOICES, getVoice, setVoice,
} from '../ui.js';
import { navigate, refreshOverview, state } from '../app.js';

const ROUNDS = {
  hr: 'HR 面', tech1: '技术一面', tech2: '技术二面',
  cross: '交叉面', product: '产品面', final: '总监面',
};
const PRESSURE = { normal: '常规', strict: '偏严', stress: '压力面' };
const STATUS_CHIP = {
  unseen: ['未练', ''], seen: ['已练', 'blue'],
  mastered: ['已掌握', 'green'], review: ['待复习', 'yellow'],
};
const DIMS = [
  ['tech', '技术深度'], ['structure', '表达结构'], ['honesty', '诚信一致'],
  ['tradeoff', '取舍判断'], ['communication', '沟通互动'],
];
const DIM_COLOR = {
  tech: '#3D6BFF', structure: '#3D6BFF', honesty: '#22A06B',
  tradeoff: '#E8A13C', communication: '#3D6BFF',
};

export default async function interviewHub(view, ctx) {
  const { param } = ctx;
  if (param === 'questions') return pageQuestions(view, ctx);
  if (param === 'practice') return pagePractice(view, ctx);
  if (param === 'mock') return pageMockSetup(view, ctx);
  if (param === 'call') return pageCall(view, ctx);
  if (param === 'records') return pageRecords(view, ctx);
  if (param === 'report') return pageReport(view, ctx);
  return pageHub(view, ctx);
}

/* ══════════════ 入口聚合 ══════════════ */
async function pageHub(view, { setTopbar }) {
  setTopbar({ title: '面试' });
  view.appendChild(loading());
  const [qs, ivs] = await Promise.all([api.questions(), api.interviews()]);
  view.innerHTML = '';

  const review = qs.questions.filter((q) => q.status === 'review').length;
  const mastered = qs.questions.filter((q) => q.status === 'mastered').length;
  const finished = ivs.interviews.filter((i) => i.status === 'finished').length;

  view.appendChild(block([
    blockHead('面试准备度', null, 'strong'),
    el('div', { class: 'stat-row' }, [
      el('div', { class: 'stat' }, [
        el('b', { class: 'c-blue', text: String(qs.total) }), el('span', { text: '题库题量' })]),
      el('div', { class: 'stat stat-green' }, [
        el('b', { text: String(mastered) }), el('span', { text: '已掌握' })]),
      el('div', { class: 'stat stat-yellow' }, [
        el('b', { text: String(review) }), el('span', { text: '待复习' })]),
      el('div', { class: 'stat' }, [
        el('b', { text: String(finished) }), el('span', { text: '已面试' })]),
    ]),
  ]));

  /* 两个模式的区别：学 vs 考。这是产品最核心的分野，单独强调。 */
  view.appendChild(el('div', { class: 'block block-blue' }, [
    blockHead('练习模式', chip('可以看答案', 'blue'), 'title-blue'),
    el('p', { class: 'body-txt body-blue',
      text: '每题自己选「先答」还是「直接看答案」。看完还有标准答案、面试官在考什么、想听的重点、追问链——用来学。' }),
    el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '进入练习模式',
      onclick: () => navigate('#/interview/practice'),
    }),
  ]));

  view.appendChild(el('div', { class: 'block' }, [
    blockHead('模拟面试', chip('全程不给答案', 'red'), 'strong'),
    el('p', { class: 'body-txt',
      text: '全屏通话界面，语音问答。面试官会追问到你说不出细节，每个问题最多追问 2 次。结束后才出五维评分和复盘——用来考。' }),
    el('button', {
      class: 'btn btn--danger btn--block btn--lg', text: '开始模拟面试',
      onclick: () => navigate('#/interview/mock'),
    }),
  ]));

  view.appendChild(el('div', { class: 'btn-row' }, [
    el('button', { class: 'btn', text: `题库（${qs.total}）`,
      onclick: () => navigate('#/interview/questions') }),
    el('button', { class: 'btn', text: `记录（${ivs.interviews.length}）`,
      onclick: () => navigate('#/interview/records') }),
  ]));
}

/* ══════════════ 题库 ══════════════ */
async function pageQuestions(view, { setTopbar, query }) {
  setTopbar({ title: '题库', back: true });
  view.appendChild(loading());
  const { questions } = await api.questions(query.job_id || null, query.status || '');
  view.innerHTML = '';

  const cur = query.status || '';
  const filters = [['全部', ''], ['待复习', 'review'], ['已掌握', 'mastered'], ['未练', 'unseen']];
  view.appendChild(el('div', { class: 'seg-bar' }, filters.map(([label, val]) =>
    el('button', {
      class: `seg${val === cur ? ' active' : ''}`, text: label,
      onclick: () => navigate(`#/interview/questions${val ? `?status=${val}` : ''}`),
    }))));

  if (!questions.length) {
    view.appendChild(empty('📚', '还没有题目。到「岗位」详情页点「生成题库」。',
      el('button', { class: 'btn btn--primary', text: '去岗位', onclick: () => navigate('#/jobs') })));
    return;
  }

  const grouped = [
    ['待复习', questions.filter((q) => q.status === 'review')],
    ['未练', questions.filter((q) => q.status === 'unseen')],
    ['其余', questions.filter((q) => !['review', 'unseen'].includes(q.status))],
  ];
  for (const [group, list] of grouped) {
    if (!list.length) continue;
    view.appendChild(el('div', { class: `group-title${group === '待复习' ? ' c-yellow' : ''}`,
      text: `${group} · ${list.length} 道` }));
    for (const q of list) view.appendChild(questionItem(q));
  }
}

function questionItem(q) {
  const [stText, stKind] = STATUS_CHIP[q.status] || ['未练', ''];
  return el('div', { class: 'qitem' }, [
    el('div', { class: 'qitem-head' }, [
      q.is_risk ? chip('风险题', 'red') : chip(ROUNDS[q.round_type] || '题目', 'blue'),
      chip(stText, stKind),
    ]),
    el('p', { class: 'qitem-q', text: q.question }),
    el('div', { class: 'qitem-meta' }, [
      el('span', { text: [ROUNDS[q.round_type], q.company].filter(Boolean).join(' · ') }),
      el('span', { text: q.standard ? '有标准答案' : '缺标准答案' }),
    ]),
    el('div', { class: 'sug-actions' }, [
      el('button', { class: 'btn btn--sm', text: '看答案', onclick: () => showAnswerSheet(q) }),
      el('button', {
        class: 'btn btn--sm', text: q.status === 'mastered' ? '取消掌握' : '已掌握',
        onclick: async () => {
          await api.patchQuestion(q.id, q.status === 'mastered' ? 'seen' : 'mastered');
          toast('已更新'); navigate(location.hash, { replace: true });
        },
      }),
      el('button', {
        class: 'btn btn--sm', text: q.status === 'review' ? '移出复习' : '加入复习',
        onclick: async () => {
          await api.patchQuestion(q.id, q.status === 'review' ? 'seen' : 'review');
          toast('已更新'); navigate(location.hash, { replace: true });
        },
      }),
    ]),
  ]);
}

/** 展示一道题的完整内容（练习模式和题库共用）。 */
export function renderQuestionBody(q) {
  const box = el('div', { style: { display: 'flex', flexDirection: 'column', gap: '12px' } });

  box.appendChild(el('div', { class: 'block block-blue' }, [
    blockHead('标准答案', el('button', {
      class: 'chip-btn chip-blue', text: '复制',
      onclick: async () => {
        try { await navigator.clipboard.writeText(q.standard || ''); toast('已复制'); }
        catch { toast('复制失败，请手动选择'); }
      },
    }), 'title-blue'),
    el('p', { class: 'body-txt body-blue', style: { whiteSpace: 'pre-wrap' },
      text: q.standard || '（还没有标准答案）' }),
  ]));

  if (q.probe) {
    box.appendChild(block([
      blockHead('面试官在考什么', null, 'strong'),
      el('p', { class: 'body-txt', style: { whiteSpace: 'pre-wrap' }, text: q.probe }),
    ]));
  }

  if (q.key_points && q.key_points.length) {
    box.appendChild(block([
      blockHead('面试官想听的重点', null, 'strong'),
      ...q.key_points.map((k) => el('div', { class: 'focus-row' }, [
        el('span', { class: k.do === false ? 'check-no' : 'check-ok',
          text: k.do === false ? '✗' : '✓' }),
        el('span', { text: k.text || String(k) }),
      ])),
    ]));
  }

  if (q.followups && q.followups.length) {
    box.appendChild(block([
      blockHead('追问链', null, 'strong'),
      ...q.followups.map((f) => el('p', { class: 'body-txt',
        text: `→ ${typeof f === 'string' ? f : (f.q || '')}` })),
    ]));
  }

  if (q.is_risk && q.risk_note) {
    box.appendChild(el('div', { class: 'notice notice--danger' }, [
      el('strong', { text: '这是风险题' }), el('div', { text: q.risk_note }),
    ]));
  }
  return box;
}

function showAnswerSheet(q) {
  openSheet(clip(q.question, 24), renderQuestionBody(q));
}

/* ══════════════ 练习模式 ══════════════ */
async function pagePractice(view, { setTopbar, query }) {
  setTopbar({ title: '练习模式', back: true });
  view.appendChild(loading());

  const [jobsRes, resumesRes, qRes] = await Promise.all([
    api.jobs(), api.resumes(), api.questions(),
  ]);
  view.innerHTML = '';

  const jobId = query.job_id ? Number(query.job_id) : null;
  const resumeId = query.resume_id ? Number(query.resume_id) : null;

  /* 第一步：选岗位 + 简历 */
  if (!jobId || !resumeId) {
    view.appendChild(el('div', { class: 'tip-bar' }, [
      icon(ICONS.speaker, 18),
      el('span', { text: '题目是结合「岗位要求 + 你的简历」生成的，两个都要选。' }),
    ]));

    const jSel = el('select', {}, [el('option', { value: '', text: '— 选择岗位 —' })].concat(
      jobsRes.jobs.map((j) => el('option', { value: String(j.id),
        text: `${j.title || '未命名'} @ ${j.company || ''}` }))));
    const rSel = el('select', {}, [el('option', { value: '', text: '— 选择简历 —' })].concat(
      resumesRes.resumes.map((r) => el('option', { value: String(r.id), text: r.name }))));
    if (jobsRes.jobs.length === 1) jSel.value = String(jobsRes.jobs[0].id);
    if (resumesRes.resumes.length === 1) rSel.value = String(resumesRes.resumes[0].id);

    view.append(
      el('label', { class: 'fld' }, [el('span', { text: '岗位' }), jSel]),
      el('label', { class: 'fld' }, [el('span', { text: '简历' }), rSel]),
      el('button', {
        class: 'btn btn--primary btn--block btn--lg', text: '开始练习',
        onclick: () => {
          if (!jSel.value || !rSel.value) { toast('两个都要选'); return; }
          navigate(`#/interview/practice?job_id=${jSel.value}&resume_id=${rSel.value}`);
        },
      }),
    );
    return;
  }

  /* 第二步：开始答题。题库按 (岗位, 简历) 配对——只按岗位筛会串到别的简历的题 */
  let pool = qRes.questions.filter((q) => q.job_id === jobId && q.resume_id === resumeId);
  if (!pool.length) pool = qRes.questions.filter((q) => q.job_id === jobId);
  if (!pool.length) {
    view.appendChild(el('div', { class: 'block block-yellow' }, [
      blockHead('这个组合还没有题库', null, 'title-yellow'),
      el('p', { class: 'body-txt body-yellow',
        text: '题库是结合「岗位要求 + 这份简历」生成的，先点下面生成。' }),
    ]));
    const genBtn = el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '生成题库（约 30 秒）',
      onclick: async (ev) => {
        const b = ev.currentTarget;
        b.disabled = true; b.textContent = '生成中…（别切换页面）';
        try {
          const out = await api.generateQuestions(jobId, resumeId, '', 20);
          toast(`生成了 ${out.total} 道题`);
          navigate(location.hash, { replace: true });
        } catch (e) {
          toast(`生成失败：${e.message}`); b.disabled = false; b.textContent = '重试';
        }
      },
    });
    view.appendChild(genBtn);
    return;
  }

  let sessionId = null;
  try {
    const s = await api.practiceStart(jobId, resumeId);
    sessionId = s.session_id;
  } catch (e) { toast(e.message); }

  let index = 0;
  let looked = 0;
  let peeked = false;          // 本题是否先偷看了答案
  const host = el('div', { class: 'page--flush' });
  view.appendChild(host);

  function draw() {
    host.innerHTML = '';
    peeked = false;              // 换题就重置
    const q = pool[index];
    const progress = pct(index, pool.length);
    host.appendChild(progressRow(`第 ${index + 1} / ${pool.length} 题`, progress));

    /* 题目卡片 */
    const card = el('div', { class: 'q-card' }, [
      el('div', { class: 'q-head' }, [
        el('span', { class: 'q-badge', text: `Q${index + 1}` }),
        el('button', {
          class: 'read-btn',
          onclick: async (ev) => {
            ev.stopPropagation();
            try { await speak(q.question); } catch (e) { toast(e.message); }
          },
          html: `<svg width="15" height="15" viewBox="0 0 20 20" fill="none">` +
            `<path d="${ICONS.speaker}" stroke="currentColor" stroke-width="1.7" ` +
            `stroke-linecap="round" stroke-linejoin="round"/></svg><span>朗读</span>`,
        }),
      ]),
      el('p', { class: 'q-text', text: q.question }),
      el('div', { class: 'q-meta' }, [
        q.is_risk ? chip('高风险', 'red', el('i', { class: 'dot dot-red' }))
          : chip('常规', '', el('i', { class: 'dot dot-yellow' })),
        chip(ROUNDS[q.round_type] || '题目'),
        el('span', { class: 'q-time', text: q.followups?.length ? `${q.followups.length} 层追问` : '约 60 秒' }),
      ]),
      el('div', { class: 'hint', text: '💡 先想一下，再决定怎么答' }),
    ]);
    host.appendChild(card);

    const answerArea = el('div', {});
    const bodyArea = el('div', { hidden: true });

    /* 录音控件 */
    const recRow = el('div', { class: 'recorder', hidden: true });
    const recTime = el('span', { class: 'recorder__time', text: '00:00' });
    const meter = el('span', { class: 'recorder__meter' },
      Array.from({ length: 12 }, () => el('i', {})));
    recRow.append(el('span', { class: 'recorder__dot' }), recTime, meter);

    const micBtn = el('button', { class: 'btn btn--primary btn--block btn--lg' });
    const setMicLabel = (html) => { micBtn.innerHTML = html; };

    const recorder = createRecorder({
      onState: (st) => {
        if (st === 'recording') {
          recRow.hidden = false;
          setMicLabel('⏹ 结束并提交');
          micBtn.classList.add('btn--danger'); micBtn.classList.remove('btn--primary');
        } else if (st === 'processing') {
          setMicLabel('识别中…');
        } else if (st === 'idle') {
          recRow.hidden = true;
          setMicLabel('🎤 我先答');
          micBtn.classList.add('btn--primary'); micBtn.classList.remove('btn--danger');
        }
      },
      onTick: (sec) => {
        recTime.textContent = `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
      },
      onLevel: (v) => {
        meter.childNodes.forEach((bar) => {
          bar.style.height = `${Math.max(3, v * 22 * (0.4 + Math.random() * 0.9))}px`;
        });
      },
      onResult: async (blob) => {
        if (!blob) { toast('没有录到声音'); return; }
        try {
          const text = await transcribe(blob);
          if (!text) { toast('没听清，再试一次'); return; }
          answered(text);
        } catch (e) { toast(`识别失败：${e.message}`); }
      },
      onError: (m) => toast(m),
    });
    setMicLabel('🎤 我先答');
    micBtn.onclick = async () => {
      try {
        if (recorder.recording) recorder.stop();
        else await recorder.start();
      } catch (e) {
        toast(e.name === 'NotAllowedError'
          ? '麦克风权限被拒绝，请在浏览器设置里允许（或改用打字）'
          : `打不开麦克风：${e.message}`);
      }
    };

    answerArea.append(
      recRow,
      micBtn,
      el('button', {
        class: 'btn btn--ghost btn--block btn--lg', text: '改打字',
        style: { marginTop: '10px' },
        onclick: () => {
          const input = el('textarea', { placeholder: '在这里写下你的回答…' });
          const submit = el('button', {
            class: 'btn btn--primary btn--block btn--lg', text: '提交回答',
            style: { marginTop: '10px' },
            onclick: () => answered(input.value),
          });
          answerArea.innerHTML = '';
          answerArea.append(input, submit);
        },
      }),
      el('button', {
        class: 'btn btn--ghost btn--block btn--lg', text: '直接看答案',
        style: { marginTop: '10px' },
        onclick: () => { looked += 1; peeked = true; reveal(''); },
      }),
      el('div', { class: 'swipe-hint' }, [
        icon(ICONS.right, 16), el('span', { text: '答完右滑下一题' }),
      ]),
    );
    host.append(answerArea, bodyArea);

    function answered(text) {
      if (sessionId) {
        api.practiceAnswer({
          session_id: sessionId, question_id: q.id, answer: text,
          input_mode: 'voice', looked_first: peeked,
        }).catch(() => {});
      }
      reveal(text);
    }

    function reveal(userAnswer) {
      answerArea.hidden = true;
      bodyArea.hidden = false;
      bodyArea.innerHTML = '';

      if (userAnswer) {
        bodyArea.appendChild(block([
          blockHead('你的回答', el('button', {
            class: 'chip-btn', text: '重答', onclick: () => draw(),
          }), 'strong'),
          el('p', { class: 'body-txt', style: { whiteSpace: 'pre-wrap' }, text: userAnswer }),
        ]));
      } else if (sessionId) {
        // 直接看答案、没作答的题也要落库，否则「有几题是偷看的」统计不准
        api.practiceAnswer({
          session_id: sessionId, question_id: q.id, answer: '',
          input_mode: 'text', looked_first: true,
        }).catch(() => {});
      }
      bodyArea.appendChild(renderQuestionBody(q));
      bodyArea.appendChild(el('div', { class: 'action-bar' }, [
        el('button', {
          class: 'btn btn--soft', text: '掌握了',
          onclick: async () => {
            try { await api.patchQuestion(q.id, 'mastered'); } catch { /* 静默 */ }
            next();
          },
        }),
        el('button', {
          class: 'btn btn--soft', text: '加入复习',
          onclick: async () => {
            try { await api.patchQuestion(q.id, 'review'); } catch { /* 静默 */ }
            next();
          },
        }),
        el('button', { class: 'btn btn--primary', text: '下一题', onclick: next }),
      ]));
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function next() {
      if (index + 1 >= pool.length) { finish(); return; }
      index += 1;
      draw();
      window.scrollTo({ top: 0 });
    }

    async function finish() {
      let out = {};
      try { if (sessionId) out = await api.practiceFinish(sessionId); } catch { /* 静默 */ }
      host.innerHTML = '';
      host.appendChild(el('div', { class: 'block block-green' }, [
        blockHead('练习完成', null, 'title-green'),
        el('p', { class: 'body-txt body-green',
          text: `共 ${pool.length} 题，其中 ${looked} 题直接看了答案。` }),
        el('p', { class: 'body-txt body-green',
          text: out.review ? `还有 ${out.review} 道题在复习列表里。` : '复习列表是空的。' }),
      ]));
      host.appendChild(el('div', { class: 'btn-row' }, [
        el('button', { class: 'btn', text: '查看复习列表',
          onclick: () => navigate('#/interview/questions?status=review') }),
        el('button', { class: 'btn btn--primary', text: '再来一轮',
          onclick: () => { index = 0; looked = 0; draw(); } }),
      ]));
      await refreshOverview();
    }

    /* 左右滑动切题（答案已展开时） */
    let touchX = null;
    host.ontouchstart = (e) => { touchX = e.touches[0].clientX; };
    host.ontouchend = (e) => {
      if (touchX === null || bodyArea.hidden) { touchX = null; return; }
      const dx = e.changedTouches[0].clientX - touchX;
      touchX = null;
      if (dx < -60) next();
      else if (dx > 60 && index > 0) { index -= 1; draw(); }
    };
  }

  draw();
}

/* ══════════════ 模拟面试 · 设置 ══════════════ */
async function pageMockSetup(view, { setTopbar }) {
  setTopbar({ title: '模拟面试', back: true });
  view.appendChild(loading());
  const [jobsRes, resumesRes] = await Promise.all([api.jobs(), api.resumes()]);
  view.innerHTML = '';

  if (!jobsRes.jobs.length || !resumesRes.resumes.length) {
    view.appendChild(empty('🎤', '模拟面试需要「岗位」和「简历」都准备好，并且生成过题库。',
      el('button', { class: 'btn btn--primary', text: '去岗位',
        onclick: () => navigate('#/jobs') })));
    return;
  }

  /* 岗位下拉里直接写题量：用户最迷惑的就是"这个岗位到底有没有题"。
     进页面先把每个岗位的题量拉回来（一个请求），选之前就能看见。 */
  const bankByJob = {};
  try {
    const { questions } = await api.questions();
    for (const q of questions) {
      bankByJob[q.job_id] = (bankByJob[q.job_id] || 0) + 1;
    }
  } catch { /* 拉不到就按 0 显示，后面还会再查一次 */ }

  const jobLabel = (j) => {
    const n = bankByJob[j.id] || 0;
    return `${j.title || '未命名'} @ ${j.company || ''} · ${n ? `${n} 题` : '还没题库'}`;
  };
  const jSel = el('select', {}, jobsRes.jobs.map((j) =>
    el('option', { value: String(j.id), text: jobLabel(j) })));
  const rSel = el('select', {}, resumesRes.resumes.map((r) =>
    el('option', { value: String(r.id), text: r.name })));
  const roundSel = el('select', {}, Object.entries(ROUNDS).map(([k, v]) =>
    el('option', { value: k, text: v })));
  roundSel.value = 'tech1';        // 默认技术一面，比 HR 面更常用
  const pressSel = el('select', {}, Object.entries(PRESSURE).map(([k, v]) =>
    el('option', { value: k, text: v })));
  pressSel.value = 'strict';

  /* ★ 岗位和简历是**成对**的：题库是按 (岗位, 简历) 生成的。
     之前两个下拉框各选各的，很容易选到"这个组合没题库"，
     点开始才报错——用户看到的就是"提示没有题库"却不知道该干嘛。
     现在选完立刻检查，没有题库就地给生成入口。 */
  const gate = el('div', {});
  const startBtn = el('button', { class: 'btn btn--danger btn--block btn--lg', text: '开始面试' });
  let pool = [];
  let generating = false;

  function currentPair() {
    return [Number(jSel.value), Number(rSel.value)];
  }

  async function checkBank() {
    const [jid, rid] = currentPair();
    gate.innerHTML = '';
    startBtn.disabled = true;
    startBtn.textContent = '检查题库…';
    try {
      const { questions } = await api.questions(jid);
      pool = questions.filter((q) => q.resume_id === rid);
      // 没有绑定该简历的题，但对这个岗位有题：退一步按岗位匹配（题库是按岗位价值生成的）
      if (!pool.length) pool = questions;
    } catch { pool = []; }

    if (pool.length) {
      startBtn.disabled = false;
      startBtn.textContent = `开始面试（${pool.length} 道题）`;
      const risky = pool.filter((q) => q.is_risk).length;
      gate.appendChild(el('div', { class: 'block block-green' }, [
        blockHead('题库已就绪', chip(`${pool.length} 题`, 'green'), 'title-green'),
        el('p', { class: 'body-txt body-green',
          text: `「${jSel.options[jSel.selectedIndex].text.split(' · ')[0]}」+ 这份简历，` +
            `可用 ${pool.length} 道题${risky ? `，其中 ${risky} 道是简历里的风险点` : ''}。` }),
      ]));
      return;
    }

    startBtn.textContent = '还没有题库';
    gate.appendChild(el('div', { class: 'block block-yellow' }, [
      blockHead('这个组合还没有题库', null, 'title-yellow'),
      el('p', { class: 'body-txt body-yellow',
        text: '题库是结合「岗位要求 + 这份简历」生成的。先点下面生成，大约 30 秒。' }),
      el('button', {
        class: 'btn btn--primary btn--block', text: '生成题库（约 30 秒）',
        onclick: async (ev) => {
          const b = ev.currentTarget;
          if (generating) return;
          generating = true;
          b.disabled = true; b.textContent = '生成中…（别切换页面）';
          try {
            const jid = Number(jSel.value);
            const out = await api.generateQuestions(jid, Number(rSel.value), '', 20);
            toast(`生成了 ${out.total} 道题`);
            bankByJob[jid] = (bankByJob[jid] || 0) + out.total;
            // 顺手把下拉里的题量标签更新掉
            [...jSel.options].forEach((op) => {
              const j = jobsRes.jobs.find((x) => String(x.id) === op.value);
              if (j) op.text = jobLabel(j);
            });
            await checkBank();
          } catch (e) {
            toast(`生成失败：${e.message}`);
            b.disabled = false; b.textContent = '重试';
          }
          generating = false;
        },
      }),
    ]));
  }

  jSel.onchange = () => {
    // 换岗位时，优先把简历切到"本来就绑这个岗位"的那一份
    const bound = resumesRes.resumes.find((r) => r.job_id === Number(jSel.value));
    if (bound) rSel.value = String(bound.id);
    checkBank();
  };
  rSel.onchange = () => checkBank();

  view.appendChild(el('div', { class: 'block block-blue' }, [
    blockHead('面试中不会给你任何答案', null, 'title-blue'),
    el('p', { class: 'body-txt body-blue',
      text: '面试官会追问到你说不出细节，每个问题最多追问 2 次。结束后才给五维评分。' }),
    el('p', { class: 'body-txt body-blue', text: '建议戴耳机，并保持页面在前台。' }),
  ]));

  view.append(
    el('label', { class: 'fld' }, [el('span', { text: '岗位' }), jSel]),
    el('label', { class: 'fld' }, [el('span', { text: '简历' }), rSel]),
    el('div', { class: 'btn-row' }, [
      el('label', { class: 'fld', style: { flex: 1 } }, [el('span', { text: '轮次' }), roundSel]),
      el('label', { class: 'fld', style: { flex: 1 } }, [el('span', { text: '压力等级' }), pressSel]),
    ]),
  );

  /* 面试官音色：选完存 localStorage，试听一条再决定 */
  const voiceSel = el('select', {}, VOICES.map(([v, label]) =>
    el('option', { value: v, text: label })));
  voiceSel.value = getVoice();
  voiceSel.onchange = () => setVoice(voiceSel.value);
  const tryBtn = el('button', {
    class: 'btn', text: '试听',
    onclick: async (ev) => {
      primeAudio();
      const b = ev.currentTarget;
      b.disabled = true; b.textContent = '合成中…';
      try { await speak('你好，我是这次面试的面试官，我们开始吧。', voiceSel.value); }
      catch (e) { toast(`试听失败：${e.message}`); }
      b.disabled = false; b.textContent = '试听';
    },
  });
  view.appendChild(el('label', { class: 'fld' }, [
    el('span', { text: '面试官音色' }),
    el('div', { class: 'btn-row' }, [voiceSel, el('div', { style: { flex: '0 0 88px' } }, [tryBtn])]),
  ]));

  startBtn.onclick = async () => {
    startBtn.disabled = true; startBtn.textContent = '准备中…';
    try {
      const s = await api.interviewStart({
        job_id: Number(jSel.value), resume_id: Number(rSel.value),
        round_type: roundSel.value, pressure: pressSel.value,
      });
      sessionStorage.setItem('ja_call', JSON.stringify(s));
      navigate('#/interview/call');
    } catch (e) {
      toast(e.message);
      startBtn.disabled = false;
      startBtn.textContent = pool.length ? `开始面试（${pool.length} 道题）` : '开始面试';
    }
  };

  view.append(gate, startBtn);
  // 默认选中"岗位和简历配套"的那一组，避免一进来就是空组合
  const paired = resumesRes.resumes.find((r) => r.job_id === Number(jSel.value));
  if (paired) rSel.value = String(paired.id);
  await checkBank();
}

/* ══════════════ 模拟面试 · 通话界面 ══════════════ */
async function pageCall(view, { setTopbar }) {
  const raw = sessionStorage.getItem('ja_call');
  if (!raw) { navigate('#/interview/mock', { replace: true }); return; }
  const s = JSON.parse(raw);

  // 双保险：万一是同一个通话页被渲染了两次，只保留后面的那次。
  // 关键是要让**前一个实例彻底闭嘴**——它可能已经发起了语音合成请求。
  if (window.__ja_call_cleanup) {
    try { window.__ja_call_cleanup(); } catch { /* 已清理 */ }
    window.__ja_call_cleanup = null;
  }
  const callId = (window.__ja_call_id || 0) + 1;
  window.__ja_call_id = callId;
  const stale = () => window.__ja_call_id !== callId;

  /* 全屏沉浸：隐藏导航与标签栏 */
  document.body.classList.add('in-call');
  view.innerHTML = '';
  const root = el('div', { class: 'call' });
  document.body.appendChild(root);

  let closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    clearInterval(timerId);
    cancelAnimationFrame(waveRaf);
    stopSpeaking();                    // 退出时别再念了
    try { recorder.stop(); } catch { /* 可能没在录 */ }
    document.body.classList.remove('in-call');
    root.remove();
    window.__ja_call_cleanup = null;
  };
  window.__ja_call_cleanup = cleanup;

  let turn = s.turn || 1;
  let busy = false;
  let isFollowup = false;
  let sec = 0;
  let recSec = 0;

  const timer = el('span', { class: 'call-timer', text: '00:00' });
  const exitBtn = el('button', { class: 'call-exit' }, [
    icon(ICONS.back, 16), el('span', { text: '退出' }),
  ]);
  const avatarRing = el('div', { class: 'avatar-ring', html:
    `<div class="avatar"><svg width="60" height="60" viewBox="0 0 44 44">` +
    `<circle cx="22" cy="22" r="22" fill="#3D6BFF"/>` +
    `<circle cx="22" cy="18" r="9" fill="#E8EDF5"/>` +
    `<path d="M8 36c2-7 8-11 14-11s12 4 14 11" fill="#E8EDF5"/></svg></div>` });
  const waveEl = wave([12, 20, 30, 24, 34, 22, 14, 26, 18, 30]);
  const stateLine = el('div', { class: 'call-status', text: '正在连接…' });
  const qno = el('div', { class: 'call-question', text: `第 ${turn} 题` });
  const progressLine = el('div', { class: 'call-progress', text: '' });
  const setProgress = (n, min) => {
    progressLine.textContent = n
      ? `已聊 ${n} 个话题${min ? `（聊够 ${min} 个左右面试官会收尾，也可以随时点结束）` : ''}`
      : '';
  };

  /* 面试官说的话：一直留在屏幕上，录音时也不消失（不然听着听着就忘了问的啥） */
  const saidLabel = el('div', { class: 'call-said-label', text: '面试官' });
  const saidText = el('div', { class: 'call-said-text', text: '' });
  const saidBox = el('div', { class: 'call-said' }, [saidLabel, saidText]);

  /* 你自己的回答：识别出来之后贴在下面，能看见自己说了什么 */
  const mineBox = el('div', { class: 'call-mine', hidden: true });
  const mineText = el('div', { class: 'call-mine-text', text: '' });
  mineBox.append(el('div', { class: 'call-mine-label', text: '你说的' }), mineText);

  const mic = el('button', { class: 'ctrl-btn ctrl-mic', html:
    `<svg width="26" height="26" viewBox="0 0 24 24" fill="none">` +
    `<rect x="9" y="3" width="6" height="11" rx="3" fill="#fff"/>` +
    `<path d="M5 11a7 7 0 0014 0" stroke="#fff" stroke-width="2" stroke-linecap="round"/>` +
    `<path d="M12 18v3" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>` });
  const micLabel = el('span', { class: 'ctrl-label', text: '点这里回答' });
  const micCtrl = el('div', { class: 'call-ctrl' }, [mic, micLabel]);

  const endBtn = el('button', { class: 'ctrl-btn ctrl-end', html:
    `<svg width="26" height="26" viewBox="0 0 24 24" fill="none">` +
    `<path d="M4 10a8 8 0 0116 0v1.5a3.5 3.5 0 01-3.5 3.5H15a1.5 1.5 0 01-1.5-1.5V8` +
    `A1.5 1.5 0 0115 6.5h.5a6 6 0 00-7 0H9A1.5 1.5 0 0110.5 8v5.5A1.5 1.5 0 019 15H7.5` +
    `A3.5 3.5 0 014 11.5V10z" fill="#fff"/></svg>` });
  const endCtrl = el('div', { class: 'call-ctrl' }, [endBtn, el('span', { class: 'ctrl-label', text: '结束面试' })]);

  root.append(
    el('div', { class: 'call-top' }, [
      exitBtn,
      el('span', { class: 'call-round', text: s.round_label || '模拟面试' }),
      timer,
    ]),
    el('div', { class: 'call-body' }, [
      avatarRing,
      stateLine,
      waveEl,
      el('div', { class: 'call-jobline', text: [s.job, s.pressure_label && `压力等级：${s.pressure_label}`].filter(Boolean).join(' · ') }),
      saidBox,
      mineBox,
      qno,
      progressLine,
    ]),
    el('div', { class: 'call-controls' }, [micCtrl, endCtrl]),
  );

  const timerId = setInterval(() => {
    sec += 1;
    timer.textContent = `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`;
  }, 1000);

  let waveRaf = 0;
  const IDLE_WAVE = [14, 24, 34, 20, 28, 16, 26, 18, 30, 20];
  function showWave(heights) {
    cancelAnimationFrame(waveRaf);
    waveEl.childNodes.forEach((b, i) => { b.style.height = `${heights[i % heights.length]}px`; });
  }
  function animateWave(active) {
    cancelAnimationFrame(waveRaf);
    if (!active) { showWave(IDLE_WAVE); return; }
    const tick = () => {
      waveEl.childNodes.forEach((b) => { b.style.height = `${8 + Math.random() * 30}px`; });
      waveRaf = requestAnimationFrame(tick);
    };
    tick();
  }
  showWave(IDLE_WAVE);

  const doFinishRef = { fn: null };
  exitBtn.onclick = () => {
    confirmSheet('结束面试', '结束后面试官会给评分。确定要结束吗？', () => doFinishRef.fn());
  };
  endBtn.onclick = () => exitBtn.onclick();

  /* 录音器 */
  const recorder = createRecorder({
    onState: (st) => {
      if (st === 'recording') {
        recSec = 0;
        micCtrl.classList.add('recording');
        mic.classList.add('recording');
        micLabel.textContent = '录音中 00:00 · 点一下结束';
        stateLine.textContent = '● 正在听你说…（停一下我就当你答完了）';
        stateLine.classList.add('rec');
        // ★ 不再隐藏面试官的问题：录音时更要看得见
        saidBox.classList.add('dim');
        animateWave(true);
      } else if (st === 'processing') {
        micCtrl.classList.remove('recording');
        mic.classList.remove('recording');
        micLabel.textContent = '识别中…';
        stateLine.textContent = '正在把你的话转成文字…';
        stateLine.classList.remove('rec');
        saidBox.classList.remove('dim');
        showWave([10, 16, 22, 14, 18]);
      } else if (st === 'idle') {
        micCtrl.classList.remove('recording');
        mic.classList.remove('recording');
        stateLine.classList.remove('rec');
        saidBox.classList.remove('dim');
        showWave(IDLE_WAVE);
      }
    },
    onTick: (t) => {
      recSec = t;
      if (micCtrl.classList.contains('recording')) {
        micLabel.textContent =
          `录音中 ${String(Math.floor(t / 60)).padStart(2, '0')}:` +
          `${String(Math.floor(t % 60)).padStart(2, '0')} · 点一下结束`;
      }
    },
    onSilence: () => {
      // 停了一会儿没说话 → 自动提交，不用再点一次按钮
      if (recorder.recording) {
        stateLine.textContent = '说完了，我提交…';
        recorder.stop();
      }
    },
    onResult: async (blob) => {
      if (!blob) { idle(); return; }
      try {
        const text = await transcribe(blob);
        if (!text) { toast('没听清，再说一次'); idle(); return; }
        mineText.textContent = text;
        mineBox.hidden = false;
        await submitAnswer(text);
      } catch (e) { toast(`识别失败：${e.message}`); idle(); }
    },
    onError: (m) => { toast(m); idle(); },
  });

  function idle() {
    micCtrl.classList.remove('recording');
    mic.classList.remove('recording');
    saidBox.classList.remove('dim');
    stateLine.classList.remove('rec');
    micLabel.textContent = '点这里回答';
    stateLine.textContent = '轮到你了';
    showWave(IDLE_WAVE);
  }

  mic.onclick = async () => {
    primeAudio();            // 用户手势里预热音频
    // ★ 允许打断：面试官还在说的时候按麦克风，就掐掉他的话直接开口。
    // 真人打电话就是可以插话的，之前会弹"稍等一下"，很出戏。
    if (busy) {
      stopSpeaking();
      askToken += 1;                 // 让这次提问作废，别再动界面
      busy = false;
      avatarRing.classList.remove('listening');
    }
    try {
      if (recorder.recording) recorder.stop();
      else await recorder.start();
    } catch (e) {
      toast(e.name === 'NotAllowedError'
        ? '麦克风权限被拒绝，请在浏览器设置里允许（或用 HTTPS 打开）'
        : `打不开麦克风：${e.message}`);
    }
  };

  function say(text) {
    saidText.textContent = text;
    saidBox.hidden = false;
  }

  /* ── 面试官说话 ── */
  /* 每次提问给一个令牌：只有最新那次能念出来。
     避免"上一次提问还在合成/播放，下一次已经开始了"导致两条音轨重叠。 */
  let askToken = 0;

  async function ask(text, follow = false) {
    if (stale()) return;                 // 这次通话已经被取代，别再说话
    const token = ++askToken;
    busy = true;
    isFollowup = follow;
    say(text);
    mineBox.hidden = true;
    stateLine.textContent = follow ? '面试官在追问…' : '面试官正在提问…';
    qno.textContent = follow ? `第 ${turn} 题 · 追问` : `第 ${turn} 题`;
    avatarRing.classList.add('listening');
    animateWave(true);
    try {
      await speak(text);
    } catch (e) {
      if (token === askToken) {
        toast(`朗读失败：${e.message}（文字还在屏幕上，可以自己读）`);
      }
    }
    if (token !== askToken || stale()) return;   // 已被取代（或被打断），别再动界面
    avatarRing.classList.remove('listening');
    busy = false;
    idle();
  }

  /* ── 提交回答 ── */
  async function submitAnswer(text) {
    busy = true;
    stateLine.textContent = '面试官正在思考…';
    animateWave(true);
    try {
      const res = await api.interviewAnswer(s.interview_id, {
        text, input_mode: 'voice', is_followup: isFollowup,
      });

      /* ★ 先给一句回应再往下走。
         真人面试不会你答完立刻蹦出下一题——中间得有"嗯""好"这种接茬，
         否则像在跟机器表单对话。ack 由同一次模型调用顺带产出，不额外花钱。 */
      if (res.ack) {
        // ack 只显示不朗读：紧接着就要念下一题，两句一起念会叠成两条音轨
        stateLine.textContent = res.ack;
        await new Promise((r) => setTimeout(r, 450));
      }

      if (res.type === 'followup') {
        await ask(res.text, true);
      } else if (res.type === 'next') {
        turn = res.turn || turn + 1;
        if (res.topic_count) {
          // 让他心里有数：大概聊够 min_topics 个话题，面试官就会收尾
          stateLine.dataset.topics = String(res.topic_count);
        }
        await ask(res.text, false);
      } else {
        await doFinish();
      }
    } catch (e) {
      showWave(IDLE_WAVE);
      toast(e.message); busy = false; idle();
    }
  }

  let finishing = false;
  async function doFinish() {
    if (finishing) return;
    finishing = true;
    clearInterval(timerId);
    cancelAnimationFrame(waveRaf);
    root.innerHTML = '';
    root.append(
      el('div', { class: 'call-status', text: '正在生成评估报告…' }),
      el('div', { class: 'call-hintfoot', text: '大约需要 10 秒，别关页面' }),
      el('div', { class: 'spinner', style: { marginTop: '18px', borderTopColor: '#3D6BFF' } }),
    );
    try {
      await api.interviewFinish(s.interview_id);
    } catch { /* 即使失败也跳去记录页，那里可以补评分 */ }
    cleanup();
    // 面试已经结束，「模拟面试设置页」不该再留在返回路径上
    state.backStack.length = 0;
    navigate(`#/interview/report?id=${s.interview_id}`, { replace: true });
  }
  doFinishRef.fn = doFinish;

  /* 开场：面试官先打个招呼，再问第一题。
     真人不会一上来就"第一题：……"，这两句连起来才像一个电话的开头。 */
  if (s.greeting) {
    stateLine.textContent = s.greeting;
    avatarRing.classList.add('listening');
    try { await speak(s.greeting); } catch { /* 朗读失败不影响 */ }
    avatarRing.classList.remove('listening');
  }
  setProgress(s.topic_count || 1, s.min_topics);
  await ask(s.question, false);
}


async function pageRecords(view, { setTopbar }) {
  setTopbar({ title: '面试记录', back: true });
  view.appendChild(loading());
  const { interviews } = await api.interviews();
  view.innerHTML = '';

  /* 半途退出的分两种，别混在一起说：
     · 一个字都没答过 → 没有评分价值，可以清掉
     · 答过但没结束   → 有内容，应该「补评分」而不是删掉
     之前这里把两种都算进"半途退出 N 场"，但清理接口只删第一种——
     用户点了半天数字不变，看着就是"清理不生效"。 */
  const empty = interviews.filter((i) => i.status === 'running' && !i.answered_turns);
  const unfinished = interviews.filter((i) => i.status === 'running' && i.answered_turns > 0);
  if (empty.length) {
    view.appendChild(el('div', { class: 'block block-yellow' }, [
      blockHead('有点开就退出的面试', chip(`${empty.length} 场`, 'yellow'), 'title-yellow'),
      el('p', { class: 'body-txt body-yellow',
        text: '这些是只听了个开场就退出的，一个字都没答过，删掉不影响任何记录。' }),
      el('button', {
        class: 'btn btn--soft btn--block', text: `清理这 ${empty.length} 场`,
        onclick: () => confirmSheet('清理点开就退出的面试',
          `会删掉 ${empty.length} 场「一个字都没答过」的记录。答过内容的一律保留。`,
          async () => {
            try {
              const out = await api.cleanupAbandoned();
              toast(out.removed ? `已清理 ${out.removed} 场` : '没有可清理的记录');
              navigate(location.hash, { replace: true });
            } catch (e) { toast(`清理失败：${e.message}`); }
          }),
      }),
    ]));
  }
  if (unfinished.length) {
    view.appendChild(el('div', { class: 'block block-blue' }, [
      blockHead('有答过但没结束的面试', chip(`${unfinished.length} 场`, 'blue'), 'title-blue'),
      el('p', { class: 'body-txt body-blue',
        text: '这些你已经答了一些、但中途退了（也没出评分）。内容都还在——' +
          '点各条下面的「补评分」，大约 10 秒就能补出复盘报告。删掉就可惜了，' +
          '所以这里给的是补评分而不是清理。' }),
    ]));
  }

  if (!interviews.length) {
    view.appendChild(empty('📋', '还没有面试记录。',
      el('button', { class: 'btn btn--primary', text: '开始模拟面试',
        onclick: () => navigate('#/interview/mock') })));
    return;
  }

  /* 卡住的面试排在最前面：中途退出/断网会留下 running 的记录，
     用户看到的"有对话没复盘"就是这类。要能一键补评分。 */
  for (const iv of interviews) {
    const scored = iv.total_score > 0;
    const needsScore = !scored;
    const kind = !scored ? '' : iv.total_score >= 40 ? 'green' : iv.total_score >= 30 ? 'yellow' : 'red';
    const item = el('div', { class: 'qitem' });
    const head = el('div', { class: 'qitem-head', style: { cursor: 'pointer' },
      onclick: () => navigate(`#/interview/report?id=${iv.id}`) }, [
      el('strong', { text: iv.title || '面试' }),
      scored ? chip(`${iv.total_score}/50`, kind)
        : chip(iv.status === 'running' ? '没结束 · 无评分' : '无评分', 'yellow'),
    ]);
    const body = el('div', { style: { cursor: 'pointer' },
      onclick: () => navigate(`#/interview/report?id=${iv.id}`) }, [
      el('div', { class: 'qitem-q', text: [iv.company, iv.round_label].filter(Boolean).join(' · ') }),
      el('div', { class: 'qitem-meta' }, [
        el('span', { text: timeAgo(iv.started_at) }),
        el('span', { text: iv.duration_sec ? `用时 ${Math.round(iv.duration_sec / 60)} 分钟` : '' }),
      ]),
    ]);
    item.append(head, body);
    if (needsScore) {
      const btn = el('button', {
        class: 'btn btn--primary btn--sm', text: '补评分',
        onclick: async (ev) => {
          ev.stopPropagation();
          btn.disabled = true; btn.textContent = '评分中…（约 10 秒）';
          try {
            const out = await api.interviewFinish(iv.id);
            if (out.scored) {
              toast(`评分完成 ${Object.values(out.scores || {}).reduce((a, b) => a + b, 0)}/50`);
              navigate(location.hash, { replace: true });
            } else {
              toast(out.reason || '这场面试没有可评分的内容');
              btn.disabled = false; btn.textContent = '补评分';
            }
          } catch (e) {
            toast(`评分失败：${e.message}`);
            btn.disabled = false; btn.textContent = '补评分';
          }
        },
      });
      item.appendChild(el('div', { class: 'sug-actions', style: { marginTop: '10px' } }, [btn]));
    }
    view.appendChild(item);
  }
}

/* ══════════════ 复盘报告 ══════════════ */
async function pageReport(view, { setTopbar, query }) {
  setTopbar({ title: '面试复盘', back: true });
  view.appendChild(loading());
  let iv;
  try { iv = await api.interview(query.id); } catch (e) {
    view.innerHTML = ''; view.appendChild(empty('⚠️', e.message)); return;
  }
  view.innerHTML = '';

  // 打开复盘页 = 复盘过这一场。不记这一笔的话，「待复盘」永远清不掉。
  if (!iv.reviewed_at) {
    api.markReviewed(iv.id).then(() => refreshOverview()).catch(() => {});
  }

  const scores = iv.score_json || {};
  const total = DIMS.reduce((n, [k]) => n + (scores[k] || 0), 0);

  /* 还没评分（中途退出/断网）：给一个补评分的入口，别让人对着空报告发呆 */
  if (!total && !iv.summary && !(iv.turns || []).length) {
    view.appendChild(empty('📋', '这场面试还没有内容。'));
    view.appendChild(el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '去模拟面试',
      onclick: () => navigate('#/interview/mock'),
    }));
    return;
  }
  if (!total && !iv.summary) {
    view.appendChild(el('div', { class: 'block block-yellow' }, [
      blockHead('这场面试还没出评分', null, 'title-yellow'),
      el('p', { class: 'body-txt body-yellow',
        text: `已记录 ${(iv.turns || []).length} 轮问答，但评分没生成（多半是中途退出或网络断了）。` }),
    ]));
    const fixBtn = el('button', {
      class: 'btn btn--primary btn--block btn--lg', text: '现在补评分（约 10 秒）',
      onclick: async (ev) => {
        const b = ev.currentTarget;
        b.disabled = true; b.textContent = '评分中…';
        try {
          const out = await api.interviewFinish(iv.id);
          if (out.scored) { toast('评分完成'); navigate(location.hash, { replace: true }); }
          else { toast(out.reason || '没有可评分的内容'); b.disabled = false; b.textContent = '现在补评分（约 10 秒）'; }
        } catch (e) {
          toast(`评分失败：${e.message}`); b.disabled = false; b.textContent = '现在补评分（约 10 秒）';
        }
      },
    });
    view.appendChild(fixBtn);
    view.appendChild(el('div', { class: 'group-title', text: '这次问过的题' }));
    for (const t of (iv.turns || [])) {
      view.appendChild(block([
        el('div', { class: 'bq-head' }, [
          el('span', { class: 'q-badge', text: `Q${t.seq}` }),
          el('span', { class: 'bq-text', text: t.question }),
        ]),
        t.answer ? el('div', { class: 'bq-row' }, [
          el('span', { class: 'bq-lab', text: '你的回答' }), el('p', { text: t.answer }),
        ]) : null,
      ]));
    }
    return;
  }

  /* 评分（深色 hero + 五维条，对齐设计稿 interview-score） */
  view.appendChild(el('div', { class: 'score-hero' }, [
    el('div', { class: 'score-num' }, [
      document.createTextNode(String(total)),
      el('span', { text: ' / 50' }),
    ]),
    el('div', { class: 'score-cap',
      text: `${iv.job_label || ''} ${iv.round_label || ''} · 综合评分`.trim() }),
  ]));

  view.appendChild(block(DIMS.map(([k, name]) => el('div', { class: 'dim' }, [
    el('div', { class: 'dim-head' }, [
      el('span', { text: name }),
      el('span', { style: { color: DIM_COLOR[k] }, text: String(scores[k] || 0) }),
    ]),
    el('div', { class: 'dim-track' }, [
      el('span', { class: 'dim-fill',
        style: { width: `${(scores[k] || 0) * 10}%`, background: DIM_COLOR[k] } }),
    ]),
  ]))));

  /* 评分趋势：和同一岗位的上一次面试比（设计稿的「上次 / 本次 ↑+5」） */
  try {
    const { interviews } = await api.interviews();
    const prev = interviews
      .filter((x) => x.job_id === iv.job_id && x.id !== iv.id
        && x.started_at < iv.started_at && x.total_score > 0)
      .sort((a, b) => (a.started_at < b.started_at ? 1 : -1))[0];
    if (prev) {
      const delta = total - prev.total_score;
      view.appendChild(block([
        blockHead('评分趋势', null, 'strong'),
        el('div', { class: 'trend-row' }, [
          el('div', { class: 'trend-item' }, [
            el('b', { class: 'c-muted', text: String(prev.total_score) }),
            el('span', { text: '上次' }),
          ]),
          el('div', { class: 'trend-item' }, [
            el('b', { class: 'c-blue', text: String(total) }),
            el('span', { class: delta >= 0 ? 'c-green' : 'c-muted',
              text: `本次 ${delta >= 0 ? '↑ +' : '↓ '}${delta}` }),
          ]),
          el('span', { class: 'trend-arrow' }, [
            icon(delta >= 0 ? ICONS.up : ICONS.chevron, 22),
          ]),
        ]),
      ]));
    }
  } catch { /* 拿不到历史记录就不显示趋势，不影响报告主体 */ }

  if (iv.summary) {
    view.appendChild(el('div', { class: 'concl-card' }, [
      el('span', { text: '一句话总评' }), el('p', { text: iv.summary }),
    ]));
  }

  if (iv.highlights && iv.highlights.length) {
    view.appendChild(block([
      blockHead('答得最好的地方', null, 'strong'),
      ...iv.highlights.map((h, i) => el('div', { class: 'num-row' }, [
        el('span', { class: 'num num-green', text: String(i + 1) }),
        el('span', { text: [h.quote ? `「${h.quote}」` : '', h.why || ''].filter(Boolean).join('　') || String(h) }),
      ])),
    ]));
  }

  if (iv.dangers && iv.dangers.length) {
    const items = [];
    iv.dangers.forEach((d, i) => {
      items.push(el('div', { class: 'num-row' }, [
        el('span', { class: 'num num-red', text: String(i + 1) }),
        el('span', { text: d.problem || String(d) }),
      ]));
      if (d.better) {
        items.push(el('div', { class: 'bq-improve' }, [
          el('span', { text: '改进后的答法' }), el('p', { text: d.better }),
        ]));
      }
    });
    view.appendChild(block([blockHead('最危险的地方', null, 'strong'), ...items]));
  }

  /* 改进计划 */
  const advice = (iv.review && iv.review.advice) || [];
  if (advice.length) {
    view.appendChild(el('div', { class: 'group-title', text: '改进计划' }));
    advice.forEach((a) => {
      view.appendChild(el('div', { class: 'plan-row' }, [
        el('span', { class: 'checkbox', html:
          `<svg width="12" height="12" viewBox="0 0 16 16"><path d="M3 8.5l3 3L13 5" ` +
          `stroke="#CCD1D9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>` }),
        el('span', { class: 'plan-txt', text: typeof a === 'string' ? a : JSON.stringify(a) }),
      ]));
    });
  }

  /* 逐题拆解：题目 → 你的回答 → 追问 → 面试官想听什么 → 差在哪 → **标准回答案例**
     「标准回答案例」优先用评审针对他本人写的那份；没有就退回题库里的标准答案。 */
  const turnReviews = (iv.review && iv.review.turn_reviews) || [];
  const reviewOf = (seq) => turnReviews.find((x) => Number(x.seq) === Number(seq)) || null;
  const bankStd = iv.bank_standards || {};

  view.appendChild(el('div', { class: 'group-title', text: '逐题拆解' }));
  for (const t of iv.turns) {
    const tr = reviewOf(t.seq);
    const modelAnswer = (tr && tr.model_answer) || bankStd[t.question] || '';
    const fromBank = !(tr && tr.model_answer) && !!bankStd[t.question];
    const parts = [
      el('div', { class: 'bq-head' }, [
        el('span', { class: 'q-badge', text: `Q${t.seq}` }),
        el('span', { class: 'bq-text', text: t.question }),
      ]),
    ];
    if (tr && tr.wanted) {
      parts.push(el('div', { class: 'bq-row' }, [
        el('span', { class: 'bq-lab', text: '面试官想听什么' }), el('p', { text: tr.wanted }),
      ]));
    }
    parts.push(t.answer ? el('div', { class: 'bq-row' }, [
      el('span', { class: 'bq-lab', text: '你的回答' }),
      el('p', { text: t.answer }),
    ]) : el('div', { class: 'bq-row' }, [
      el('span', { class: 'bq-lab c-red', text: '这题没答' }),
    ]));
    if (tr && tr.gap) {
      parts.push(el('div', { class: 'bq-row' }, [
        el('span', { class: 'bq-lab c-red', text: '差在哪' }), el('p', { text: tr.gap }),
      ]));
    }
    for (const [i, f] of (t.followups || []).entries()) {
      parts.push(el('div', { class: 'bq-row' }, [
        el('span', { class: 'bq-lab c-green', text: `追问 ${i + 1}` }), el('p', { text: f.q || '' }),
      ]));
      if (f.a) {
        parts.push(el('div', { class: 'bq-row' }, [
          el('span', { class: 'bq-lab', text: '你的回答' }), el('p', { text: f.a }),
        ]));
      }
    }
    if (modelAnswer) {
      parts.push(el('div', { class: 'bq-improve' }, [
        el('span', { text: fromBank ? '标准回答案例（题库原文）' : '标准回答案例' }),
        el('p', { text: modelAnswer }),
      ]));
    } else {
      parts.push(el('p', { class: 'body-txt',
        style: { fontSize: '12px', color: 'var(--muted)' },
        text: '这道题没有匹配到标准答案——它不在题库里，是面试官临场问的。' }));
    }
    view.appendChild(block(parts));
  }

  view.appendChild(el('div', { class: 'btn-row', style: { marginTop: '6px' } }, [
    el('button', { class: 'btn btn--primary', text: '再练一次',
      onclick: () => navigate('#/interview/mock') }),
    el('button', { class: 'btn btn--ghost', text: '去练习模式',
      onclick: () => navigate('#/interview/practice') }),
  ]));
}
