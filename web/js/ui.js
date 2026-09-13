/** 通用 UI 工具：元素构造、徽章、弹层、加载态、录音控件。 */

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);

let toastTimer = null;
export function toast(msg, ms = 2000) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms);
}

export function loading(text = '处理中…') {
  return el('div', { class: 'loading' }, [
    el('div', { class: 'spinner' }), el('div', { text }),
  ]);
}

export function empty(icon, text, action) {
  return el('div', { class: 'empty' }, [
    el('span', { class: 'empty__icon', text: icon }),
    el('div', { text }),
    action ? el('div', { style: { marginTop: '14px' } }, [action]) : null,
  ]);
}

const STATUS_TEXT = {
  unconfirmed: '待确认', confirmed: '已确认', corrected: '已修正', obsolete: '已失效',
  ok: '可直接采纳', need: '需要补充', risk: '不建议写',
  strong: '强', medium: '中', weak: '弱', none: '无',
  pending: '待处理', accepted: '已采纳', rejected: '已忽略', edited: '已修改',
  running: '进行中', finished: '已结束',
};

export function badge(kind, text) {
  return el('span', { class: `badge badge--${kind}`, text: text || STATUS_TEXT[kind] || kind });
}

export function statusBadge(s) { return badge(s); }

/* ── 设计稿里的组件（页面模块共用） ── */

/** 20×20 描边图标。 */
export const ICONS = {
  back: 'M12.5 4.5L7 10l5.5 5.5',
  chevron: 'M7.5 5l5 5-5 5',
  speaker: 'M4 8v4h2.6L10 15V5L6.6 8H4zM12.4 7.6a3.4 3.4 0 010 4.8M14.4 5.6a6 6 0 010 8.8',
  mic: 'M10 3a2.6 2.6 0 012.6 2.6v4a2.6 2.6 0 11-5.2 0v-4A2.6 2.6 0 0110 3zM4.8 9.4a5.2 5.2 0 0010.4 0M10 14.6V17.5',
  stop: 'M7 7h6v6H7z',
  pause: 'M7.5 5v10M12.5 5v10',
  upload: 'M10 13.5V4M6.5 7.5L10 4l3.5 3.5M4 13v2.5A1.5 1.5 0 005.5 17h9a1.5 1.5 0 001.5-1.5V13',
  doc: 'M5.5 2.5h5.2L15 6.8V17a.5.5 0 01-.5.5h-9a.5.5 0 01-.5-.5V3a.5.5 0 01.5-.5zM10.5 2.6V7h4.4',
  briefcase: 'M3 7.5A1.5 1.5 0 014.5 6h11A1.5 1.5 0 0117 7.5V15a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 15V7.5zM7.5 6V4.5A1.5 1.5 0 019 3h2a1.5 1.5 0 011.5 1.5V6',
  camera: 'M4 6.5h2.5L8 5h4l1.5 1.5H16v9H4v-9zM10 8.5a3 3 0 100 6 3 3 0 000-6z',
  micCircle: 'M10 4a2 2 0 012 2v3.5a2 2 0 11-4 0V6a2 2 0 012-2zM6.5 9.8a3.5 3.5 0 007 0M10 13.3V16',
  left: 'M12 3l-5 5 5 5',
  copy: 'M7.5 7.5h6v6h-6zM5 12.5V5h7.5',
  check: 'M4.5 10.5l3.5 3.5L15.5 6.5',
  right: 'M4 10h12M11 5l5 5-5 5',
  up: 'M5 12.5l5-5 5 5',
};

/** 描边 SVG 图标元素。 */
export function icon(path, size = 20) {
  return el('span', {
    class: 'ui-icon',
    style: { display: 'inline-flex', flex: 'none' },
    html: `<svg width="${size}" height="${size}" viewBox="0 0 20 20" fill="none" aria-hidden="true">` +
      `<path d="${path}" stroke="currentColor" stroke-width="1.7" ` +
      `stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  });
}

export function chip(text, kind = '', extra = null) {
  return el('span', { class: `chip${kind ? ` chip-${kind}` : ''}` },
    [extra, text].filter(Boolean));
}

export function block(children, cls = '') {
  return el('div', { class: `block${cls ? ` ${cls}` : ''}` }, [].concat(children));
}

export function blockHead(title, right = null, titleCls = '') {
  return el('div', { class: 'block-head' }, [
    el('span', { class: `block-title${titleCls ? ` ${titleCls}` : ''}`, text: title }),
    right,
  ]);
}

/** 声波条。level 0~1 时按传入高度数组渲染静态波形。 */
export function wave(heights, cls = '') {
  return el('div', { class: `wave${cls ? ` ${cls}` : ''}` },
    heights.map((h) => el('span', { class: 'wave-bar', style: { height: `${h}px` } })));
}

/** 进度条（练习模式顶部）。 */
export function progressRow(label, pct) {
  return el('div', {}, [
    el('div', { class: 'progress-row' }, [
      el('span', { class: 'prog-label', text: label }),
      el('span', { class: 'prog-pct', text: `${pct}%` }),
    ]),
    el('div', { class: 'progress-track' }, [
      el('span', { class: 'progress-fill', style: { width: `${pct}%` } }),
    ]),
  ]);
}

/** 小工具：把百分比限制在 0~100。 */
export const pct = (v, max) => Math.max(0, Math.min(100, Math.round((v / (max || 1)) * 100)));

export function timeAgo(ts) {
  if (!ts) return '';
  const t = new Date(ts.replace(' ', 'T')).getTime();
  if (Number.isNaN(t)) return ts;
  const d = Math.floor((Date.now() - t) / 1000);
  if (d < 60) return '刚刚';
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  if (d < 2592000) return `${Math.floor(d / 86400)} 天前`;
  return ts.slice(0, 10);
}

/* ── 弹层 ── */
export function openSheet(title, content) {
  const sheet = $('#sheet');
  $('#sheet-title').textContent = title;
  const box = $('#sheet-content');
  box.innerHTML = '';
  box.appendChild(typeof content === 'string' ? el('div', { text: content }) : content);
  sheet.hidden = false;
  const close = () => { sheet.hidden = true; };
  sheet.querySelectorAll('[data-close]').forEach((n) => { n.onclick = close; });
  return close;
}
export const closeSheet = () => { $('#sheet').hidden = true; };

/** 二次确认。 */
export function confirmSheet(title, text, onOk) {
  const ok = el('button', {
    class: 'btn btn--danger btn--block btn--lg', text: '确认',
    onclick: () => { closeSheet(); onOk(); },
  });
  openSheet(title, el('div', {}, [el('p', { text }), el('div', { style: { marginTop: 14 } }, [ok])]));
}

/* ── 录音控件 ──
   用 MediaRecorder 采集，再用 AudioContext 解码重采样成 16k 单声道 WAV。
   这条链路在 DSH 插件里已经验证过（45 个测试用例），这里复用同样的做法。 */

function encodeWav(samples, sampleRate) {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const ascii = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
  ascii(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); ascii(8, 'WAVE');
  ascii(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); ascii(36, 'data'); view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return bytes;
}

function toMono16k(buffer) {
  const src = new Float32Array(buffer.length);
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const d = buffer.getChannelData(c);
    for (let i = 0; i < d.length; i++) src[i] += d[i] / buffer.numberOfChannels;
  }
  if (buffer.sampleRate === 16000) return src;
  const ratio = buffer.sampleRate / 16000;
  const len = Math.max(1, Math.floor(src.length / ratio));
  const out = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    const p = i * ratio, l = Math.floor(p), r = Math.min(l + 1, src.length - 1);
    out[i] = src[l] * (1 - (p - l)) + src[r] * (p - l);
  }
  return out;
}

/**
 * 录音器。
 * @param {{onState?: (s: string) => void, onLevel?: (v: number) => void}} hooks
 */
export function createRecorder(hooks = {}) {
  let mediaRecorder = null, stream = null, chunks = [], audioCtx = null;
  let rafId = 0, analyser = null, startedAt = 0, timerId = 0;
  let stopping = false;

  function cleanup() {
    if (rafId) cancelAnimationFrame(rafId); rafId = 0;
    if (timerId) clearInterval(timerId); timerId = 0;
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; }
    analyser = null;
    hooks.onLevel?.(0);
  }

  /* 静音自动结束（VAD）：
     连续 SILENCE_MS 毫秒音量低于阈值就自动停录并提交。
     真人对话不会"说完再点一下按钮"，这一步省掉，接话感会好很多。
     需要至少说过 MIN_SPEECH_MS 的话，避免刚开口就被切断。 */
  const SILENCE_MS = 1100;
  const MIN_SPEECH_MS = 600;
  const SILENCE_LEVEL = 0.045;

  async function start() {
    hooks.onState?.('starting');
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 128;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Uint8Array(analyser.frequencyBinCount);
    let spokeAt = 0;
    let quietSince = 0;
    const tick = () => {
      if (!analyser) return;
      analyser.getByteFrequencyData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i];
      const level = Math.min(1, sum / buf.length / 140);
      hooks.onLevel?.(level);

      const now = Date.now();
      if (level >= SILENCE_LEVEL) {
        if (!spokeAt) spokeAt = now;
        quietSince = 0;
      } else if (spokeAt && now - spokeAt > MIN_SPEECH_MS) {
        if (!quietSince) quietSince = now;
        else if (now - quietSince >= SILENCE_MS) {
          quietSince = 0; spokeAt = 0;
          hooks.onSilence?.();      // 交给调用方决定要不要停
          return;
        }
      }
      rafId = requestAnimationFrame(tick);
    };
    tick();

    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
      .find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
    mediaRecorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      cleanup();
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      if (!blob.size) { hooks.onState?.('idle'); hooks.onResult?.(null); return; }
      hooks.onState?.('processing');
      try {
        const ab = await blob.arrayBuffer();
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const decoded = await ctx.decodeAudioData(ab);
        const mono = toMono16k(decoded);
        ctx.close().catch(() => {});
        const wav = encodeWav(mono, 16000);
        hooks.onResult?.(new Blob([wav], { type: 'audio/wav' }));
      } catch (err) {
        hooks.onError?.(`音频处理失败：${err.message}`);
        hooks.onResult?.(null);
      }
    };
    mediaRecorder.start();
    startedAt = Date.now();
    timerId = setInterval(() => hooks.onTick?.((Date.now() - startedAt) / 1000), 200);
    hooks.onState?.('recording');
  }

  function stop() {
    if (stopping) return;
    stopping = true;
    if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
    else { cleanup(); hooks.onState?.('idle'); }
    setTimeout(() => { stopping = false; }, 300);
  }

  return { start, stop, get recording() { return mediaRecorder?.state === 'recording'; } };
}

/* ── 面试官音色 ──
   只放 DashScope 官方文档里有名字的四个（实测：瞎填的名字会静默回退成默认音色，
   所以不能"随便写一个试试"）。选完存在 localStorage，下次进来还是这个。 */
export const VOICES = [
  ['Cherry', 'Cherry'],
  ['Serena', 'Serena'],
  ['Ethan', 'Ethan'],
  ['Chelsie', 'Chelsie'],
];
const VOICE_KEY = 'ja_voice';
export const getVoice = () => {
  try { return localStorage.getItem(VOICE_KEY) || 'Cherry'; } catch { return 'Cherry'; }
};
export const setVoice = (v) => {
  try { localStorage.setItem(VOICE_KEY, v); } catch { /* 隐私模式下会失败，忽略 */ }
};

/* ── TTS 播放（流式，低延迟）──
   ★ 走 /api/voice/tts-stream：服务端边合成边吐 PCM，这边边收边排进 Web Audio。
   实测首块音频 ~0.45s 就到（老的整段接口要等 2.2s 才拿到完整文件、3s 才出声）。
   对话里"接话快不快"基本就取决于这一个数。
   speak() 会打断上一句，并且**等到真正播完**才返回。 */
let currentSpeech = null;
let sharedCtx = null;

export function stopSpeaking() {
  const h = currentSpeech;
  if (!h) return;
  currentSpeech = null;
  h.stopped = true;
  try { h.abort?.abort(); } catch { /* 已经结束 */ }
  for (const src of h.sources) { try { src.stop(); } catch { /* 没在播 */ } }
  h.sources.length = 0;
  if (h.audioEl) { try { h.audioEl.pause(); } catch { /* 已经停了 */ } }
  h.resolve?.();
}

function getAudioCtx() {
  if (!sharedCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    sharedCtx = new Ctx();
  }
  return sharedCtx;
}

/**
 * 预热音频：**必须在用户点击里调**。
 * 浏览器要求音频上下文由用户手势启动，晚一点再 resume() 可能永远起不来
 * （headless 里就是这样：整个 speak() 卡在 resume 上，一个请求都发不出去）。
 * 所以在「开始面试」「点这里回答」这类点击里先把它唤醒。
 */
export function primeAudio() {
  try {
    const ctx = getAudioCtx();
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  } catch { /* 不支持就算了，后面还有兜底 */ }
}

/** 把一块 16-bit PCM 排进播放队列。 */
function schedulePcm(handle, ctx, bytes) {
  const samples = new Float32Array(bytes.length / 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < samples.length; i++) {
    samples[i] = view.getInt16(i * 2, true) / 0x8000;
  }
  const buf = ctx.createBuffer(1, samples.length, 24000);
  buf.copyToChannel(samples, 0);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  const at = Math.max(handle.nextTime, ctx.currentTime + 0.02);
  src.start(at);
  handle.nextTime = at + buf.duration;
  handle.sources.push(src);
  src.onended = () => {
    const i = handle.sources.indexOf(src);
    if (i >= 0) handle.sources.splice(i, 1);
    if (handle.sources.length === 0 && handle.streamDone) handle.resolve?.();
  };
}

/** 播一句话。会打断上一句；返回的 Promise 在本句播完时 resolve。 */
export async function speak(text, voice = '') {
  stopSpeaking();
  const handle = { stopped: false, sources: [], streamDone: false, nextTime: 0 };
  currentSpeech = handle;

  const ctx = getAudioCtx();
  if (ctx.state === 'suspended') {
    // 给 resume 加个上限：某些环境下它会一直挂着，不能让它把整句话卡死
    try {
      await Promise.race([
        ctx.resume(),
        new Promise((r) => setTimeout(r, 800)),
      ]);
    } catch { /* 起不来也继续，下面还有兜底 */ }
  }
  handle.nextTime = ctx.currentTime + 0.05;

  const finished = new Promise((resolve) => { handle.resolve = resolve; });

  let res;
  const ac = new AbortController();
  handle.abort = ac;
  try {
    res = await fetch('/api/voice/tts-stream', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text, voice: voice || getVoice() }),
      signal: ac.signal,
    });
  } catch (e) {
    if (handle.stopped) return null;
    throw new Error(`语音连接失败：${e.message}`);
  }
  if (!res.ok || !res.body) {
    if (handle.stopped) return null;
    return speakFallback(text, voice, handle);
  }

  (async () => {
    const reader = res.body.getReader();
    let leftover = new Uint8Array(0);
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done || handle.stopped) break;
        // 分块可能把 16-bit 采样切断，补上上一块的尾巴
        let chunk = value;
        if (leftover.length) {
          chunk = new Uint8Array(leftover.length + value.length);
          chunk.set(leftover, 0);
          chunk.set(value, leftover.length);
        }
        const usable = chunk.length - (chunk.length % 2);
        if (usable) schedulePcm(handle, ctx, chunk.subarray(0, usable));
        leftover = usable < chunk.length ? chunk.slice(usable) : new Uint8Array(0);
      }
    } catch { /* 被打断或网络中断，按结束处理 */ }
    handle.streamDone = true;
    if (!handle.sources.length) handle.resolve?.();
    // ★ 按"排好的播放时刻"兜底：onended 在某些环境（没有音频设备、
    // 上下文被挂起）不会触发，只靠它会把整场对话卡死。
    const left = Math.max(0, (handle.nextTime - ctx.currentTime) * 1000) + 250;
    setTimeout(() => handle.resolve?.(), left);
  })();

  // 最后一道安全阀
  const guard = setTimeout(() => handle.resolve?.(), 60000);
  await finished;
  clearTimeout(guard);
  return handle;
}

/**
 * 兜底：流式走不通就用老的整段接口（一次性拿完整音频再播）。
 * 慢（~2.2s）但稳，不能因为流式有问题就整个哑掉。
 */
async function speakFallback(text, voice, handle) {
  const r = await fetch('/api/voice/tts', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text, voice: voice || getVoice() }),
  });
  if (!r.ok) throw new Error('语音合成失败');
  const url = URL.createObjectURL(await r.blob());
  const audio = new Audio(url);
  handle.audioEl = audio;
  await audio.play();
  await new Promise((resolve) => {
    audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
    audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
  });
  return handle;
}

/** 上传录音并取回识别文本。 */
export async function transcribe(blob) {
  const fd = new FormData();
  fd.append('file', blob, 'answer.wav');
  const res = await fetch('/api/voice/asr', { method: 'POST', body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || '识别失败');
  return data.text || '';
}

/** 长文本截断显示。 */
export const clip = (s, n = 90) => (s && s.length > n ? s.slice(0, n) + '…' : s || '');
