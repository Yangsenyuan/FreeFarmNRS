/* 后端 API 客户端 */

const BASE = '/api';

function detailText(detail) {
  if (!detail) return '请求失败';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => d.msg || d.message || JSON.stringify(d)).join('；');
  }
  return detail.message || JSON.stringify(detail);
}

async function request(path, { method = 'GET', body, headers = {}, signal } = {}) {
  const opts = { method, headers: { ...headers }, signal };
  if (body !== undefined) {
    if (body instanceof FormData) {
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (err) {
    throw new Error('无法连接本地服务，请确认平台已启动');
  }
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch (e) { /* ignore */ }
    const msg = data && data.detail ? detailText(data.detail) : `请求失败（HTTP ${res.status}）`;
    const error = new Error(msg);
    error.status = res.status;
    throw error;
  }
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return res;
}

function qs(params) {
  const sp = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    sp.append(key, value);
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}

function uploadImages(pid, files, meta = {}, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${BASE}/projects/${encodeURIComponent(pid)}/images`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
      } else {
        reject(new Error(detailText(data && data.detail) || `上传失败（HTTP ${xhr.status}）`));
      }
    };
    xhr.onerror = () => reject(new Error('上传失败：无法连接本地服务'));
    const fd = new FormData();
    files.forEach((file) => fd.append('files', file, file.name));
    Object.entries(meta).forEach(([key, value]) => fd.append(key, value || ''));
    xhr.send(fd);
  });
}

async function importModel(formData) {
  let res;
  try {
    res = await fetch(`${BASE}/models/import`, { method: 'POST', body: formData });
  } catch (e) {
    throw new Error('无法连接本地服务');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) throw new Error(detailText(data && data.detail) || `模型导入失败（HTTP ${res.status}）`);
  return data;
}

export const api = {
  // 系统
  health: () => request('/health'),
  systemInfo: () => request('/system/info'),
  preprocessDefaults: () => request('/system/preprocess-defaults'),
  demoStatus: () => request('/system/demo'),
  seedDemo: (force = false) => request('/system/demo', { method: 'POST', body: { force } }),

  // 项目
  listProjects: () => request('/projects'),
  createProject: (payload) => request('/projects', { method: 'POST', body: payload }),
  getProject: (pid) => request(`/projects/${encodeURIComponent(pid)}`),
  updateProject: (pid, payload) => request(`/projects/${encodeURIComponent(pid)}`, { method: 'PATCH', body: payload }),
  deleteProject: (pid) => request(`/projects/${encodeURIComponent(pid)}`, { method: 'DELETE' }),
  getLogs: (pid, limit = 200) => request(`/projects/${encodeURIComponent(pid)}/logs${qs({ limit })}`),

  // 影像
  uploadImages,
  listImages: (pid) => request(`/projects/${encodeURIComponent(pid)}/images`),
  updateImage: (pid, iid, payload) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}`, { method: 'PATCH', body: payload }),
  deleteImage: (pid, iid) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}`, { method: 'DELETE' }),
  previewUrl(pid, iid, params = {}) {
    return `${BASE}/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/preview${qs(params)}`;
  },
  thumbUrl(pid, iid, version = '', size = 480) {
    return `${BASE}/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/preview${qs({ mode: 'rgb', size, v: version })}`;
  },
  coverUrl(pid, version = '') {
    return `${BASE}/projects/${encodeURIComponent(pid)}/cover${qs({ v: version })}`;
  },
  spectrumPoint(pid, iid, x, y) {
    return request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/spectrum${qs({ x, y })}`);
  },
  spectraBatch(pid, iid, payload) {
    return request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/spectra`, { method: 'POST', body: payload });
  },
  reparseImageMetadata(pid, iid, apply = false) {
    return request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/reparse-metadata${qs({ apply })}`, { method: 'POST' });
  },

  // 预处理
  getProcessed: (pid, iid) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/processed`),
  processedPreviewUrl(pid, iid, layer, version = '') {
    return `${BASE}/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/processed/preview${qs({ layer, v: version })}`;
  },
  startPreprocess: (pid, iid, config) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/preprocess`, { method: 'POST', body: { config } }),

  // 反演
  startInversion: (pid, iid, payload) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/inversions`, { method: 'POST', body: payload }),
  startBatchInversion: (pid, payload) => request(`/projects/${encodeURIComponent(pid)}/batch-inversions`, { method: 'POST', body: payload }),
  previewFewShot: (pid, iid, payload) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/few-shot/preview`, { method: 'POST', body: payload }),
  applyFewShot: (pid, iid, payload) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/few-shot/apply`, { method: 'POST', body: payload }),
  fewShotTemplateUrl(pid, iid, coordinate = 'pixel') {
    return `${BASE}/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/few-shot/template.csv${qs({ coordinate })}`;
  },
  imageModelCompatibility(pid, iid, modelId = '') {
    return request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/model-compatibility${qs({ model_id: modelId })}`);
  },
  batchModelCompatibility(pid, payload) {
    return request(`/projects/${encodeURIComponent(pid)}/batch-model-compatibility`, { method: 'POST', body: payload });
  },

  // 任务
  getJob: (jid) => request(`/jobs/${encodeURIComponent(jid)}`),
  listJobs: (projectId, activeOnly = false) => request(`/jobs${qs({ project_id: projectId, active_only: activeOnly })}`),

  // 模型
  listModels: () => request('/models'),
  importModel,
  deleteModel: (mid) => request(`/models/${encodeURIComponent(mid)}`, { method: 'DELETE' }),

  // 结果
  projectResults: (pid) => request(`/projects/${encodeURIComponent(pid)}/results`),
  getResult: (pid, iid) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/result`),
  resultPreviewUrl(pid, iid, mode = 'classified', version = '') {
    return `${BASE}/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/result/preview${qs({ mode, v: version })}`;
  },
  resultValue: (pid, iid, x, y) => request(`/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/result/value${qs({ x, y })}`),
  exportUrl(pid, iid, kind) {
    return `${BASE}/projects/${encodeURIComponent(pid)}/images/${encodeURIComponent(iid)}/result/export/${kind}`;
  },
};
