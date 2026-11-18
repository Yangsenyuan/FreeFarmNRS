/* 通用 UI 组件 */
import { store, notify, dismissToast, createProject, go, openDemo, loadLogs } from '../store.js';
import { projectStatusMeta, imageStatusMeta, formatDate } from '../utils.js';
import { AppIcon } from './icons.js';

const { ref, reactive, computed, watch, onMounted, onBeforeUnmount } = window.Vue;

export const Modal = {
  name: 'Modal',
  props: {
    open: Boolean,
    title: String,
    subtitle: String,
    icon: { type: String, default: 'info' },
    wide: Boolean,
    closeOnBackdrop: { type: Boolean, default: true },
  },
  emits: ['close'],
  setup(props, { emit }) {
    function onKey(e) {
      if (e.key === 'Escape' && props.open) emit('close');
    }
    watch(() => props.open, (open) => {
      document.body.style.overflow = open ? 'hidden' : '';
      if (open) document.addEventListener('keydown', onKey);
      else document.removeEventListener('keydown', onKey);
    });
    onBeforeUnmount(() => {
      document.body.style.overflow = '';
      document.removeEventListener('keydown', onKey);
    });
    return {
      backdrop() { if (props.closeOnBackdrop) emit('close'); },
    };
  },
  template: `
  <teleport to="body">
    <transition name="fade">
      <div v-if="open" class="modal-mask" @mousedown.self="backdrop">
        <div class="modal-card" :class="{ 'modal-wide': wide }" role="dialog" aria-modal="true">
          <div class="modal-head">
            <div class="mh-icon"><app-icon :name="icon" :size="18"/></div>
            <div style="min-width:0">
              <h3>{{ title }}</h3>
              <p v-if="subtitle">{{ subtitle }}</p>
            </div>
            <button class="icon-btn" @click="$emit('close')" aria-label="关闭"><app-icon name="x" :size="18"/></button>
          </div>
          <div class="modal-body"><slot/></div>
          <div class="modal-foot" v-if="$slots.footer"><slot name="footer"/></div>
        </div>
      </div>
    </transition>
  </teleport>`,
};

export const ToastStack = {
  name: 'ToastStack',
  components: { AppIcon },
  computed: {
    toasts() { return store.toasts; },
  },
  methods: {
    dismiss(id) { dismissToast(id); },
    icon(type) { return type === 'error' ? 'alert-circle' : type === 'info' ? 'info' : 'check-circle'; },
  },
  template: `
  <div class="toast-stack">
    <transition-group name="fade">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type">
        <div class="t-icon"><app-icon :name="icon(t.type)" :size="15"/></div>
        <div class="t-body">
          <div class="t-title">{{ t.title }}</div>
          <div class="t-msg">{{ t.message }}</div>
        </div>
        <button class="t-close" @click="dismiss(t.id)" aria-label="关闭"><app-icon name="x" :size="14"/></button>
      </div>
    </transition-group>
  </div>`,
};

export const EmptyState = {
  name: 'EmptyState',
  components: { AppIcon },
  props: {
    icon: { type: String, default: 'leaf' },
    title: { type: String, default: '暂无数据' },
    text: { type: String, default: '' },
  },
  template: `
  <div class="empty">
    <div class="empty-icon"><app-icon :name="icon" :size="26"/></div>
    <h3>{{ title }}</h3>
    <p v-if="text">{{ text }}</p>
    <slot/>
  </div>`,
};

export const StatusBadge = {
  name: 'StatusBadge',
  props: {
    status: String,
    image: Boolean,
    hasResult: Boolean,
    label: String,
  },
  computed: {
    meta() {
      if (this.label) return { label: this.label, cls: this.image ? 'completed' : 'empty' };
      return this.image ? imageStatusMeta(this.status, this.hasResult) : projectStatusMeta(this.status);
    },
  },
  template: `<span class="status-badge" :class="meta.cls"><i class="dot"></i>{{ meta.label }}</span>`,
};

export const LogDrawer = {
  name: 'LogDrawer',
  components: { AppIcon },
  setup() {
    let timer = null;
    function stopTimer() {
      if (timer) { clearInterval(timer); timer = null; }
    }
    watch(() => store.logOpen, (open) => {
      stopTimer();
      if (open) {
        loadLogs();
        timer = setInterval(() => { if (store.logOpen) loadLogs(); }, 5000);
      }
    });
    onBeforeUnmount(stopTimer);
    return {
      store,
      close() { store.logOpen = false; },
      refresh() { loadLogs(); },
      levelClass(level) { return level === 'error' ? 'error' : level === 'warning' ? 'warning' : ''; },
      time(item) { return formatDate(item.time, true); },
      sourceLabel(source) {
        return { project: '项目', import: '导入', preprocess: '预处理', inversion: '反演', result: '结果', demo: '示例', system: '系统' }[source] || '系统';
      },
    };
  },
  template: `
  <teleport to="body">
    <transition name="fade">
      <div v-if="store.logOpen" class="drawer-mask" @click.self="close">
        <div class="drawer">
          <div class="drawer-head">
            <div class="empty-icon" style="width:34px;height:34px;border-radius:9px"><app-icon name="list" :size="17"/></div>
            <div style="flex:1"><h3>处理日志</h3><div class="tiny muted">记录每一步操作，便于问题追溯</div></div>
            <button class="icon-btn" @click="refresh" title="刷新"><app-icon name="refresh" :size="17"/></button>
            <button class="icon-btn" @click="close" title="关闭"><app-icon name="x" :size="18"/></button>
          </div>
          <div class="drawer-body">
            <div v-if="store.logsLoading && !store.logs.length" class="loading-line" style="padding:20px 0"><span class="spinner"></span>正在读取日志…</div>
            <div v-else-if="!store.logs.length" class="empty" style="padding:36px 10px">
              <div class="empty-icon"><app-icon name="list" :size="24"/></div>
              <h3>暂无处理日志</h3>
              <p>导入影像、执行预处理或反演后，操作记录会显示在这里。</p>
            </div>
            <div v-for="(item, i) in store.logs" :key="i" class="log-item" :class="levelClass(item.level)">
              <span class="log-dot"></span>
              <div style="min-width:0">
                <div class="log-msg">{{ item.message }}</div>
                <div class="log-time">{{ time(item) }} · {{ sourceLabel(item.source) }}</div>
              </div>
            </div>
          </div>
          <div class="drawer-head" style="border-top:1px solid var(--line);border-bottom:0;padding:11px 16px">
            <div class="tiny muted">日志保存在项目目录 logs/project.jsonl，仅本机可读</div>
          </div>
        </div>
      </div>
    </transition>
  </teleport>`,
};

export const HelpModal = {
  name: 'HelpModal',
  components: { AppIcon, Modal },
  setup() {
    const opening = ref(false);
    async function openSample() {
      opening.value = true;
      try {
        await openDemo();
        store.helpOpen = false;
      } catch (e) { /* toast already shown */ }
      finally { opening.value = false; }
    }
    function close() { store.helpOpen = false; }
    function showGuide() { store.helpOpen = false; store.guideOpen = true; }
    return { store, close, openSample, opening, showGuide };
  },
  template: `
  <Modal :open="store.helpOpen" title="帮助与使用说明" subtitle="从影像到氮素分布图，只需四步" icon="help" wide @close="close">
    <div class="help-flow">
      <div class="help-step"><div class="hs-n">1</div><div class="hs-t">数据导入</div><div class="hs-d">GeoTIFF / ENVI，多幅影像批量导入</div></div>
      <div class="help-step"><div class="hs-n">2</div><div class="hs-t">数据预处理</div><div class="hs-d">反射率转换、平滑、波段对齐、掩膜</div></div>
      <div class="help-step"><div class="hs-n">3</div><div class="hs-t">模型反演</div><div class="hs-d">选择内置或自定义模型，一键反演</div></div>
      <div class="help-step"><div class="hs-n">4</div><div class="hs-t">结果分析</div><div class="hs-d">专题图、统计、多期对比、PDF 报告</div></div>
    </div>

    <div class="help-section">
      <h4><app-icon name="database" :size="14"/>支持的数据格式</h4>
      <ul>
        <li><b>GeoTIFF</b>（.tif / .tiff）：单文件直接导入。</li>
        <li><b>ENVI</b>（.dat / .img + 同名 .hdr）：请同时选择头文件与数据文件，缺少 .hdr 会给出提示。</li>
        <li>波段中心波长与带宽自动解析；若缺失，可在导入页手动补充，否则光谱横轴使用波段序号。</li>
      </ul>
    </div>

    <div class="help-section">
      <h4><app-icon name="cpu" :size="14"/>关于反演模型</h4>
      <ul>
        <li>内置模型带有「示例」标识，基于合成光谱数据训练，仅用于演示完整流程。</li>
        <li>正式使用请导入自己的 .pkl / .joblib 模型；模型需接受形状为 (N, 波段数) 的光谱矩阵。</li>
        <li>模型训练波长需与导入影像的波段一致，平台会在反演前自动对齐波段。</li>
        <li>已有少量实测 Nmass 样点时，可在结果分析页执行「实测微调」，生成偏置或斜率校正的派生模型。</li>
      </ul>
    </div>

    <div class="help-section">
      <h4><app-icon name="shield" :size="14"/>数据安全</h4>
      <p>所有影像、处理结果与日志都保存在本机 data 目录，不会上传到外网。关闭启动窗口即可停止服务。</p>
    </div>

    <div class="help-section">
      <h4><app-icon name="info" :size="14"/>版本信息</h4>
      <p>FreeFarmNRS V{{ store.system.version || '1.0.0' }} · 氮素遥感监测与反演 · Python {{ store.system.python || '3.9' }} · Rasterio {{ (store.system.packages || {}).rasterio || '—' }}</p>
    </div>

    <template #footer>
      <button class="btn btn-ghost" @click="showGuide"><app-icon name="play" :size="16"/>新手引导</button>
      <button class="btn btn-ghost" @click="openSample" :disabled="opening">
        <app-icon name="sparkles" :size="16"/>{{ opening ? '正在准备…' : (store.system.demo_exists ? '打开示例项目' : '载入示例项目') }}
      </button>
      <button class="btn btn-primary" @click="close">知道了</button>
    </template>
  </Modal>`,
};

export const GuideModal = {
  name: 'GuideModal',
  components: { AppIcon, Modal },
  setup() {
    const busy = ref(false);
    function close() {
      localStorage.setItem('nmass_guide_seen', '1');
      store.guideOpen = false;
    }
    async function startDemo() {
      busy.value = true;
      try {
        await openDemo();
        close();
      } catch (e) { /* toast shown */ }
      finally { busy.value = false; }
    }
    return { store, close, startDemo, busy };
  },
  template: `
  <Modal :open="store.guideOpen" title="欢迎使用" subtitle="FreeFarmNRS · V1.0" icon="leaf" wide :close-on-backdrop="false" @close="close">
    <div class="guide-hero">
      <div class="gh-icon"><app-icon name="leaf" :size="30"/></div>
      <h3>从光谱影像到氮素分布图</h3>
      <p>无需实测样本即可体验零样本反演；有少量实测 Nmass 样点时，可在结果页进行 few-shot 校正</p>
    </div>
    <div class="guide-steps">
      <div class="guide-step"><div class="gs-ico"><app-icon name="upload" :size="17"/></div><div><h4>1. 导入影像</h4><p>支持多光谱与高光谱，自动解析波段波长</p></div></div>
      <div class="guide-step"><div class="gs-ico"><app-icon name="sliders" :size="17"/></div><div><h4>2. 一键预处理</h4><p>默认参数已预置，也可逐步调整</p></div></div>
      <div class="guide-step"><div class="gs-ico"><app-icon name="cpu" :size="17"/></div><div><h4>3. 选择模型</h4><p>内置示例模型，支持导入自己的模型</p></div></div>
      <div class="guide-step"><div class="gs-ico"><app-icon name="chart" :size="17"/></div><div><h4>4. 查看结果</h4><p>分级专题图、统计指标与 PDF 报告</p></div></div>
    </div>
    <div class="hint-box" style="margin-top:12px">
      <app-icon name="shield" :size="15"/>
      <span>全部处理在本机完成，数据不上传外网。示例项目使用合成影像，不会影响你的数据。</span>
    </div>
    <template #footer>
      <button class="btn btn-ghost" @click="close">稍后探索</button>
      <button class="btn btn-primary" @click="startDemo" :disabled="busy">
        <span v-if="busy" class="spinner light"></span>
        <app-icon v-else name="sparkles" :size="16"/>
        {{ busy ? '正在准备示例…' : (store.system.demo_exists ? '打开示例项目' : '从示例项目开始') }}
      </button>
    </template>
  </Modal>`,
};

export const NewProjectModal = {
  name: 'NewProjectModal',
  components: { AppIcon, Modal },
  setup() {
    const form = reactive({ name: '', crop: '夏玉米', field: '', description: '' });
    const submitting = ref(false);
    const error = ref('');
    const crops = ['夏玉米', '春玉米', '小麦', '水稻', '大豆', '其他'];

    watch(() => store.newProjectOpen, (open) => {
      if (open) { error.value = ''; form.name = ''; form.field = ''; form.description = ''; }
    });

    function close() { store.newProjectOpen = false; }

    async function submit() {
      if (!form.name.trim()) { error.value = '请填写项目名称'; return; }
      submitting.value = true;
      error.value = '';
      try {
        await createProject({
          name: form.name.trim(),
          crop: form.crop,
          field: form.field.trim(),
          description: form.description.trim(),
        });
        store.newProjectOpen = false;
        go('import');
      } catch (e) {
        error.value = e.message;
      } finally {
        submitting.value = false;
      }
    }
    return { store, form, submitting, error, crops, close, submit };
  },
  template: `
  <Modal :open="store.newProjectOpen" title="新建项目" subtitle="一个项目对应一个田块或一批同期影像" icon="folder" @close="close">
    <div class="form-grid">
      <div class="field full">
        <label>项目名称 <span class="required">*</span></label>
        <input class="input" v-model="form.name" maxlength="80" placeholder="例如：田块A 夏玉米氮素监测" @keyup.enter="submit">
      </div>
      <div class="field">
        <label>作物类型</label>
        <select class="select" v-model="form.crop">
          <option v-for="c in crops" :key="c" :value="c">{{ c }}</option>
        </select>
      </div>
      <div class="field">
        <label>田块编号</label>
        <input class="input" v-model="form.field" placeholder="例如：A区-03">
      </div>
      <div class="field full">
        <label>项目说明 <span class="unit">（可选）</span></label>
        <textarea class="textarea" v-model="form.description" placeholder="记录项目目的、数据来源等信息"></textarea>
      </div>
    </div>
    <div v-if="error" class="hint-box danger" style="margin-top:12px"><app-icon name="alert-circle" :size="15"/>{{ error }}</div>
    <template #footer>
      <button class="btn btn-ghost" @click="close">取消</button>
      <button class="btn btn-primary" @click="submit" :disabled="submitting">
        <span v-if="submitting" class="spinner light"></span>
        <app-icon v-else name="plus" :size="16"/>
        {{ submitting ? '正在创建…' : '创建项目' }}
      </button>
    </template>
  </Modal>`,
};
