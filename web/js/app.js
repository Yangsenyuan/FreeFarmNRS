/* 应用入口：路由、布局与全局组件 */
import { store, boot, syncRouteFromHash } from './store.js';
import { AppIcon } from './components/icons.js';
import { TopBar, Sidebar, StepBar } from './components/layout.js';
import { ToastStack, HelpModal, GuideModal, NewProjectModal, LogDrawer } from './components/ui.js';
import { HomePage } from './pages/home.js';
import { ImportPage } from './pages/import.js';
import { ViewerPage } from './pages/viewer.js';
import { PreprocessPage } from './pages/preprocess.js';
import { InversionPage } from './pages/inversion.js';
import { ResultsPage } from './pages/results.js';

const { computed, watch, onMounted, onBeforeUnmount } = window.Vue;

const PAGES = {
  home: HomePage,
  import: ImportPage,
  viewer: ViewerPage,
  preprocess: PreprocessPage,
  inversion: InversionPage,
  results: ResultsPage,
};

const App = {
  name: 'App',
  components: {
    AppIcon, TopBar, Sidebar, StepBar, ToastStack, HelpModal, GuideModal, NewProjectModal, LogDrawer,
  },
  setup() {
    const pageComponent = computed(() => PAGES[store.route] || HomePage);
    const pageKey = computed(() => `${store.route}:${store.project ? store.project.id : 'none'}`);
    const showStepBar = computed(() => !!store.project && store.route !== 'home');

    function onHashChange() { syncRouteFromHash(); }

    watch(() => store.route, () => {
      window.scrollTo({ top: 0, behavior: 'auto' });
    });

    onMounted(async () => {
      window.addEventListener('hashchange', onHashChange);
      await boot();
      if (!location.hash) location.hash = '#/home';
    });
    onBeforeUnmount(() => window.removeEventListener('hashchange', onHashChange));

    return {
      store, pageComponent, pageKey, showStepBar,
      reload() { location.reload(); },
    };
  },
  template: `
  <div v-if="store.booted">
    <div class="app-shell">
      <TopBar/>
      <div class="app-body">
        <Sidebar/>
        <main class="app-main">
          <StepBar v-if="showStepBar"/>
          <component :is="pageComponent" :key="pageKey"/>
        </main>
      </div>
    </div>
    <LogDrawer/>
    <HelpModal/>
    <GuideModal/>
    <NewProjectModal/>
    <ToastStack/>
  </div>

  <div v-else-if="store.bootError" class="boot-splash">
    <div class="boot-logo" style="background:#fbecea;color:#c0392b">
      <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>
    </div>
    <div class="boot-title">平台初始化失败</div>
    <div class="boot-sub">{{ store.bootError }}</div>
    <button class="btn btn-primary" style="margin-top:8px" @click="reload">重新加载</button>
  </div>

  <div v-else class="boot-splash">
    <div class="boot-logo">
      <svg viewBox="0 0 64 64" aria-hidden="true">
        <path d="M17 46C17 28 30 16 51 15c1 21-12 33-34 31Z" fill="#d8f0da"/>
        <path d="M17 48c7-14 18-22 31-27" fill="none" stroke="#2f7d4a" stroke-width="2.6" stroke-linecap="round"/>
      </svg>
    </div>
    <div class="boot-title">FreeFarmNRS</div>
    <div class="boot-sub">正在载入项目与模型库…</div>
    <span class="spinner" style="margin-top:6px"></span>
  </div>`,
};

const app = Vue.createApp(App);
app.component('app-icon', AppIcon);
app.mount('#app');
