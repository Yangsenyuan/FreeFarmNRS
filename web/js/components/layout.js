/* 布局组件：顶栏、侧栏、步骤条 */
import { store, go, openProject, loadProjects, projectSteps, routeStepIndex, notify } from '../store.js';
import { projectStatusMeta, formatDate } from '../utils.js';
import { AppIcon } from './icons.js';

const { ref, computed, onMounted, onBeforeUnmount } = window.Vue;

export const TopBar = {
  name: 'TopBar',
  components: { AppIcon },
  setup() {
    const open = ref(false);
    const switcher = ref(null);

    function toggle() { open.value = !open.value; }
    function close() { open.value = false; }
    function onDocClick(e) {
      if (switcher.value && !switcher.value.contains(e.target)) open.value = false;
    }
    onMounted(() => document.addEventListener('click', onDocClick));
    onBeforeUnmount(() => document.removeEventListener('click', onDocClick));

    async function chooseProject(id) {
      close();
      if (!id) return;
      try {
        await openProject(id);
        if (store.route === 'home') go('viewer');
      } catch (e) { /* toast */ }
    }
    function newProject() { close(); store.newProjectOpen = true; }
    function statusCls(p) { return projectStatusMeta(p.status).cls; }

    const recentProjects = computed(() => store.projects.slice(0, 8));

    return {
      store, open, switcher, toggle, close, chooseProject, newProject, statusCls, recentProjects,
      openHelp() { store.helpOpen = true; },
      openLogs() { store.logOpen = true; },
      projectStatusMeta,
      formatDate,
    };
  },
  template: `
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark">
        <svg viewBox="0 0 64 64" aria-hidden="true">
          <path d="M17 46C17 28 30 16 51 15c1 21-12 33-34 31Z" fill="#d8f0da"/>
          <path d="M17 48c7-14 18-22 31-27" fill="none" stroke="#2f7d4a" stroke-width="2.6" stroke-linecap="round"/>
        </svg>
      </div>
      <div class="brand-text">
        <div class="brand-name">FreeFarmNRS<span class="brand-version">V1.0</span></div>
        <div class="brand-sub">Nmass Remote Sensing</div>
      </div>
    </div>

    <div class="topbar-center">
      <div class="project-switcher" :class="{ open }" ref="switcher">
        <button class="project-btn" @click.stop="toggle" :title="store.project ? store.project.name : '选择项目'">
          <app-icon name="folder" :size="16" class="folder"/>
          <span class="name">{{ store.project ? store.project.name : '未打开项目' }}</span>
          <app-icon name="chevron-down" :size="14" class="chev"/>
        </button>
        <transition name="fade">
          <div class="project-menu" v-if="open" @click.stop>
            <div class="project-menu-head">
              <span>切换项目</span>
              <span>{{ store.projects.length }} 个项目</span>
            </div>
            <button v-for="p in recentProjects" :key="p.id" class="menu-item" :class="{ active: store.project && store.project.id === p.id }" @click="chooseProject(p.id)">
              <span class="status-badge" :class="statusCls(p)" style="height:20px;padding:0 7px"><i class="dot"></i>{{ p.status_label }}</span>
              <span class="mi-main">
                <span class="mi-name">{{ p.name }}</span>
                <span class="mi-meta">{{ p.image_count }} 幅影像 · {{ p.updated_at ? formatDate(p.updated_at) : '' }}</span>
              </span>
            </button>
            <div v-if="!store.projects.length" class="menu-empty">还没有项目，先新建一个吧</div>
            <div class="divider" style="margin:6px 4px"></div>
            <button class="menu-item" @click="newProject">
              <app-icon name="plus" :size="16" style="color:var(--green)"/>
              <span class="mi-main"><span class="mi-name">新建项目</span><span class="mi-meta">开始一次氮素反演</span></span>
            </button>
          </div>
        </transition>
      </div>
      <div class="local-chip"><span class="dot"></span>本地处理 · 数据不上传</div>
    </div>

    <div class="topbar-actions">
      <button class="btn btn-ghost btn-sm" @click="newProject" title="新建项目">
        <app-icon name="plus" :size="15"/><span>新建项目</span>
      </button>
      <div class="top-divider"></div>
      <button class="icon-btn" @click="openLogs" title="处理日志"><app-icon name="list" :size="18"/></button>
      <button class="icon-btn" @click="openHelp" title="帮助"><app-icon name="help" :size="18"/></button>
    </div>
  </header>`,
};

export const Sidebar = {
  name: 'Sidebar',
  components: { AppIcon },
  setup() {
    const items = [
      { route: 'home', label: '首页', icon: 'home', requireProject: false },
      { route: 'import', label: '数据导入', icon: 'upload', requireProject: true, badgeKey: 'image_count' },
      { route: 'viewer', label: '影像查看', icon: 'image', requireProject: true },
      { route: 'preprocess', label: '数据预处理', icon: 'sliders', requireProject: true },
      { route: 'inversion', label: '模型反演', icon: 'cpu', requireProject: true },
      { route: 'results', label: '结果分析', icon: 'chart', requireProject: true, badgeKey: 'inverted_count' },
    ];
    const steps = computed(() => projectSteps());
    const projectMeta = computed(() => {
      const p = store.project;
      if (!p) return null;
      return [p.crop, p.field].filter(Boolean).join(' · ') || '未填写作物/田块';
    });
    function badge(item) {
      if (!item.badgeKey || !store.project) return '';
      const n = store.project[item.badgeKey] || 0;
      return n ? String(n) : '';
    }
    return { store, items, steps, projectMeta, badge };
  },
  template: `
  <aside class="sidebar">
    <div class="nav-group-label">工作台</div>
    <nav class="nav-list">
      <button v-for="item in items" :key="item.route" class="nav-item"
        :class="{ active: store.route === item.route }"
        :disabled="item.requireProject && !store.project"
        :title="item.requireProject && !store.project ? '请先打开或新建项目' : item.label"
        @click="go(item.route)">
        <app-icon :name="item.icon" :size="18" class="nav-icon"/>
        <span class="nav-label">{{ item.label }}</span>
        <span class="nav-badge" v-if="badge(item)">{{ badge(item) }}</span>
      </button>
    </nav>

    <div class="sidebar-spacer"></div>

    <div class="sidebar-mini" v-if="store.project">
      <div class="mini-label">当前项目</div>
      <div class="mini-name">{{ store.project.name }}</div>
      <div class="mini-meta">{{ projectMeta }}</div>
      <div class="mini-track">
        <span v-for="s in steps" :key="s.key" :class="{ on: s.done }"></span>
      </div>
      <div class="mini-steps">
        <span>导入</span><span>预处理</span><span>反演</span><span>结果</span>
      </div>
    </div>

    <div class="sidebar-foot">
      <span>V{{ store.system.version || '1.0.0' }}</span>
      <span>本地版</span>
    </div>
  </aside>`,
};

export const StepBar = {
  name: 'StepBar',
  components: { AppIcon },
  setup() {
    const steps = computed(() => projectSteps());
    const current = computed(() => routeStepIndex(store.route));
    return {
      store, steps, current, go,
      imageCount() { return store.project?.image_count || 0; },
      doneCount() { return store.project?.inverted_count || 0; },
    };
  },
  template: `
  <div class="stepbar">
    <template v-for="(step, i) in steps" :key="step.key">
      <button class="step" :class="{ done: step.done, current: current === i }" @click="go(step.route)">
        <span class="dot">
          <app-icon v-if="step.done" name="check" :size="13" :stroke-width="2.6"/>
          <span v-else>{{ i + 1 }}</span>
        </span>
        <span class="step-text">{{ step.label }}</span>
      </button>
      <span v-if="i < steps.length - 1" class="step-line"></span>
    </template>
    <div class="stepbar-extra">
      <app-icon name="image" :size="14"/>
      <span>{{ imageCount() }} 幅影像</span>
      <span style="opacity:.5">·</span>
      <span>{{ doneCount() }} 幅已反演</span>
    </div>
  </div>`,
};
