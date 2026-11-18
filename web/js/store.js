/* 全局状态与数据加载 */
import { api } from './api.js';

const { reactive } = window.Vue;

export const ROUTES = ['home', 'import', 'viewer', 'preprocess', 'inversion', 'results'];

export const store = reactive({
  booted: false,
  bootError: '',
  route: 'home',
  projects: [],
  project: null,
  activeImageId: null,
  models: [],
  system: { demo_exists: false, project_count: 0, result_count: 0, model_count: 0, version: '1.0.0' },
  onnxAvailable: false,
  loadingProjects: false,
  loadingProject: false,
  loadingModels: false,
  logs: [],
  logsLoading: false,
  logOpen: false,
  helpOpen: false,
  guideOpen: false,
  newProjectOpen: false,
  toasts: [],
});

let toastSeq = 0;

export function notify(message, type = 'success', title = '') {
  const id = ++toastSeq;
  const defaultTitle = { success: '操作成功', error: '操作失败', info: '提示' }[type] || '提示';
  store.toasts.push({ id, type, message, title: title || defaultTitle });
  if (store.toasts.length > 3) store.toasts.shift();
  const ttl = type === 'error' ? 6200 : 4200;
  setTimeout(() => dismissToast(id), ttl);
  return id;
}

export function dismissToast(id) {
  const index = store.toasts.findIndex((t) => t.id === id);
  if (index >= 0) store.toasts.splice(index, 1);
}

export function routeFromHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '').split('?')[0].trim();
  return ROUTES.includes(raw) ? raw : 'home';
}

export function go(route) {
  if (!ROUTES.includes(route)) route = 'home';
  if (route !== 'home' && !store.project) {
    notify('请先打开或新建一个项目', 'info');
    route = 'home';
  }
  store.route = route;
  const target = `#/${route}`;
  if (location.hash !== target) location.hash = target;
}

export function syncRouteFromHash() {
  const route = routeFromHash();
  if (route !== 'home' && !store.project) {
    store.route = 'home';
    if (location.hash !== '#/home') location.hash = '#/home';
    return;
  }
  store.route = route;
}

export async function loadSystem() {
  try {
    store.system = await api.systemInfo();
  } catch (err) {
    // 系统信息失败不阻断主流程
    console.warn('加载系统信息失败', err);
  }
}

export async function loadProjects() {
  store.loadingProjects = true;
  try {
    const data = await api.listProjects();
    store.projects = data.projects || [];
  } finally {
    store.loadingProjects = false;
  }
}

export async function loadModels() {
  store.loadingModels = true;
  try {
    const data = await api.listModels();
    store.models = data.models || [];
    store.onnxAvailable = !!data.onnx_available;
  } finally {
    store.loadingModels = false;
  }
}

export async function loadProject(id, { silent = false } = {}) {
  if (!id) return null;
  if (!silent) store.loadingProject = true;
  try {
    const project = await api.getProject(id);
    store.project = project;
    const images = project.images || [];
    if (!images.some((im) => im.id === store.activeImageId)) {
      store.activeImageId = images[0]?.id || null;
    }
    localStorage.setItem('nmass_last_project', id);
    const idx = store.projects.findIndex((p) => p.id === id);
    if (idx >= 0) {
      const { images, ...rest } = project;
      store.projects[idx] = { ...store.projects[idx], ...rest };
    }
    return project;
  } catch (err) {
    if (store.project && store.project.id === id) store.project = null;
    if (!silent) notify(err.message, 'error');
    throw err;
  } finally {
    if (!silent) store.loadingProject = false;
  }
}

export async function refreshProject({ silent = true } = {}) {
  if (!store.project) return;
  await loadProject(store.project.id, { silent });
  try { await loadProjects(); } catch (e) { /* ignore */ }
}

export async function openProject(id, { navigateTo = null } = {}) {
  await loadProject(id);
  if (navigateTo) go(navigateTo);
}

export async function createProject(payload) {
  const project = await api.createProject(payload);
  await loadProjects();
  await loadProject(project.id, { silent: true });
  notify(`项目「${project.name}」已创建`, 'success');
  return project;
}

export async function deleteProject(id) {
  await api.deleteProject(id);
  if (store.project && store.project.id === id) {
    store.project = null;
    localStorage.removeItem('nmass_last_project');
    go('home');
  }
  await loadProjects();
  notify('项目已删除', 'success');
}

export async function loadLogs() {
  if (!store.project) return;
  store.logsLoading = true;
  try {
    const data = await api.getLogs(store.project.id, 200);
    store.logs = data.logs || [];
  } catch (err) {
    console.warn(err);
  } finally {
    store.logsLoading = false;
  }
}

export function pollJob(jobId, { onUpdate, onDone, interval = 700 } = {}) {
  let stopped = false;
  let timer = null;
  async function tick() {
    if (stopped) return;
    try {
      const job = await api.getJob(jobId);
      if (stopped) return;
      if (onUpdate) onUpdate(job);
      if (job.status === 'completed' || job.status === 'failed') {
        if (onDone) onDone(job);
        return;
      }
    } catch (err) {
      if (onDone) onDone({ id: jobId, status: 'failed', error: err.message });
      return;
    }
    if (!stopped) timer = setTimeout(tick, interval);
  }
  tick();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

export async function openDemo({ onUpdate } = {}) {
  if (store.system.demo_exists) {
    await loadProjects();
    const exists = store.projects.find((p) => p.id === 'demo_corn');
    if (exists) {
      await openProject('demo_corn', { navigateTo: 'results' });
      return { existed: true };
    }
  }
  const { job_id } = await api.seedDemo(false);
  return new Promise((resolve, reject) => {
    pollJob(job_id, {
      onUpdate,
      onDone: async (job) => {
        if (job.status === 'completed') {
          await loadSystem();
          await loadProjects();
          const target = store.projects.find((p) => p.id === 'demo_corn') || store.projects[0];
          if (target) await openProject(target.id, { navigateTo: 'results' });
          notify('示例项目已就绪', 'success');
          resolve(job);
        } else {
          notify(job.error || '示例项目生成失败', 'error');
          reject(new Error(job.error || '示例项目生成失败'));
        }
      },
    });
  });
}

export function projectSteps() {
  const images = store.project?.images || [];
  const imported = images.length > 0;
  const preprocessed = images.some((im) => ['preprocessed', 'inverted'].includes(im.status));
  const inverted = images.some((im) => im.status === 'inverted');
  return [
    { key: 'import', label: '数据导入', done: imported, route: 'import' },
    { key: 'preprocess', label: '数据预处理', done: preprocessed, route: 'preprocess' },
    { key: 'inversion', label: '模型反演', done: inverted, route: 'inversion' },
    { key: 'results', label: '结果分析', done: inverted, route: 'results' },
  ];
}

export function routeStepIndex(route) {
  return { import: 0, viewer: 0, preprocess: 1, inversion: 2, results: 3 }[route] ?? 0;
}

export async function boot() {
  try {
    const [sys, projectsData, modelsData] = await Promise.all([
      api.systemInfo(),
      api.listProjects(),
      api.listModels(),
    ]);
    store.system = sys;
    store.projects = projectsData.projects || [];
    store.models = modelsData.models || [];
    store.onnxAvailable = !!modelsData.onnx_available;

    const urlParams = new URLSearchParams(location.search);
    const requestedProject = urlParams.get('project');
    const last = localStorage.getItem('nmass_last_project');
    const projectToLoad = [requestedProject, last].find((id) => id && store.projects.some((p) => p.id === id));
    if (projectToLoad) {
      try { await loadProject(projectToLoad, { silent: true }); } catch (e) { /* ignore */ }
    }
    const route = routeFromHash();
    if (route !== 'home' && !store.project) {
      store.route = 'home';
      if (location.hash !== '#/home') location.hash = '#/home';
    } else {
      store.route = route;
    }
    const forceWelcome = urlParams.get('welcome') === '1';
    const suppressWelcome = urlParams.get('guide') === '0';
    if (forceWelcome || (!suppressWelcome && !localStorage.getItem('nmass_guide_seen'))) {
      store.guideOpen = true;
    }
  } catch (err) {
    store.bootError = err.message || '初始化失败';
  } finally {
    store.booted = true;
  }
}
