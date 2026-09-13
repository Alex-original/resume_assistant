/** API 封装。所有请求走这里，统一错误处理。 */

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body instanceof FormData
      ? undefined
      : { 'content-type': 'application/json' },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    // 登录态失效（比如 cookie 过期、账号被停用）就回登录页，
    // 而不是在页面上弹一堆看不懂的报错
    if (res.status === 401) {
      sessionStorage.removeItem('ja_call');
      location.href = '/login';
      throw new Error('登录已过期');
    }
    const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}

const get = (p) => request(p);
const post = (p, body) => request(p, { method: 'POST', body: JSON.stringify(body ?? {}) });
const patch = (p, body) => request(p, { method: 'PATCH', body: JSON.stringify(body ?? {}) });
const del = (p) => request(p, { method: 'DELETE' });

export const api = {
  // 基础
  overview: () => get('/api/overview'),
  settings: () => get('/api/settings'),

  // 档案
  profile: () => get('/api/profile'),
  projects: () => get('/api/projects'),
  patchPoint: (id, body) => patch(`/api/points/${id}`, body),

  // 材料
  materials: () => get('/api/materials'),
  material: (id) => get(`/api/materials/${id}`),
  materialFromUrl: (url, projectId = null) =>
    post('/api/materials/from-url', { url, project_id: projectId }),
  projectSummary: (projectId) => post(`/api/projects/${projectId}/summary`),
  uploadMaterial: (file, projectId = null, docKind = '') => {
    const fd = new FormData();
    fd.append('file', file);
    const qs = [];
    if (projectId) qs.push(`project_id=${projectId}`);
    if (docKind) qs.push(`doc_kind=${docKind}`);
    return request(`/api/materials${qs.length ? '?' + qs.join('&') : ''}`,
      { method: 'POST', body: fd });
  },
  parseMaterial: (id) => post(`/api/materials/${id}/parse`),
  parseJd: (id) => post(`/api/materials/${id}/parse-jd`),
  confirmMaterialFacts: (id) => post(`/api/materials/${id}/confirm-facts`),
  promoteFacts: (id, factIds) => post(`/api/materials/${id}/promote`, { fact_ids: factIds }),

  // 岗位
  jobs: () => get('/api/jobs'),
  job: (id) => get(`/api/jobs/${id}`),
  deleteJob: (id) => del(`/api/jobs/${id}`),
  createJob: (body) => post('/api/jobs', body),
  analyzeJob: (id, resumeId = null) =>
    post(`/api/jobs/${id}/analyze`, { resume_id: resumeId }),
  patchJob: (id, status) => patch(`/api/jobs/${id}`, { status }),

  // 简历
  resumes: () => get('/api/resumes'),
  resume: (id) => get(`/api/resumes/${id}`),
  createResume: (body) => post('/api/resumes', body),
  patchResume: (id, body) => patch(`/api/resumes/${id}`, body),
  deleteResume: (id) => del(`/api/resumes/${id}`),
  deletePoint: (id) => del(`/api/points/${id}`),
  createProject: (body) => post('/api/projects', body),
  deleteProject: (id) => del(`/api/projects/${id}`),
  deleteMaterial: (id) => del(`/api/materials/${id}`),
  me: () => get('/api/auth/me'),
  logout: () => post('/api/auth/logout'),
  resumeChat: (messages, jobId = null, resumeId = null) =>
    post('/api/resume-chat', { messages, job_id: jobId, resume_id: resumeId }),
  resumeChatSession: () => get('/api/resume-chat/session'),
  resumeChatReset: () => del('/api/resume-chat/session'),

  // 简历包装
  generatePackaging: (jobId, resumeId) =>
    post('/api/packaging/generate', { job_id: jobId, resume_id: resumeId }),
  suggestions: (resumeId, jobId) =>
    get(`/api/packaging/suggestions?resume_id=${resumeId}` + (jobId ? `&job_id=${jobId}` : '')),
  decideSuggestion: (id, decision, editedText = '') =>
    post(`/api/packaging/suggestions/${id}/decide`, { decision, edited_text: editedText }),
  applyPackaging: (resumeId) => post('/api/packaging/apply', { resume_id: resumeId }),

  // 题库
  questions: (jobId, status, round) => {
    const q = new URLSearchParams();
    if (jobId) q.set('job_id', jobId);
    if (status) q.set('status', status);
    if (round) q.set('round_type', round);
    return get(`/api/questions?${q}`);
  },
  generateQuestions: (jobId, resumeId, roundType = '', count = 20) =>
    post('/api/questions/generate',
      { job_id: jobId, resume_id: resumeId, round_type: roundType, count }),
  patchQuestion: (id, status) => patch(`/api/questions/${id}`, { status }),

  // 练习
  practiceStart: (jobId, resumeId) =>
    post('/api/practice/start', { job_id: jobId, resume_id: resumeId }),
  practiceAnswer: (body) => post('/api/practice/answer', body),
  practiceFinish: (sid) => post(`/api/practice/${sid}/finish`),

  // 模拟面试
  interviewStart: (body) => post('/api/interviews', body),
  interviews: () => get('/api/interviews'),
  cleanupAbandoned: () => del('/api/interviews/abandoned'),
  interview: (id) => get(`/api/interviews/${id}`),
  markReviewed: (id) => post(`/api/interviews/${id}/reviewed`),
  interviewAnswer: (id, body) => post(`/api/interviews/${id}/answer`, body),
  interviewFinish: (id) => post(`/api/interviews/${id}/finish`),

  // 语音

  // 投递
  applications: () => get('/api/applications'),
  saveApplication: (body) => post('/api/applications', body),
};
