/* 数据预处理页 */
import { api } from '../api.js';
import { store, go, notify, refreshProject, pollJob } from '../store.js';
import {
  formatNumber, pct, parseRanges, rangesToText, imageStatusMeta, resolutionText, shortName,
} from '../utils.js';
import { AppIcon } from '../components/icons.js';
import { EmptyState } from '../components/ui.js';

const { ref, reactive, computed, watch, onBeforeUnmount } = window.Vue;

const FALLBACK_DEFAULTS = {
  reflectance: { enabled: true, method: 'scale', scale: 10000, coefficient: 1, offset: 0 },
  smoothing: { enabled: true, window: 5, polyorder: 2 },
  bad_bands: { enabled: false, ranges: [[1340, 1450], [1800, 1960]] },
  alignment: { enabled: true, start: 400, end: 900, step: 10, extrapolate: true },
  mask: { enabled: true, ndvi_threshold: 0.35, nir_wavelength: 840, red_wavelength: 670 },
  cloud: { enabled: false, blue_threshold: 0.3 },
  crop: { enabled: false, x0: 0, y0: 0, width: 0, height: 0 },
  indices: ['NDVI', 'NDRE', 'CIredge'],
};

const INDICES = [
  { key: 'NDVI', label: 'NDVI', desc: '归一化植被指数' },
  { key: 'NDRE', label: 'NDRE', desc: '红边归一化差值指数' },
  { key: 'CIredge', label: 'CIredge', desc: '红边叶绿素指数' },
  { key: 'GNDVI', label: 'GNDVI', desc: '绿光归一化植被指数' },
];

function clone(obj) { return JSON.parse(JSON.stringify(obj)); }
function deepMerge(target, source) {
  Object.keys(source || {}).forEach((key) => {
    const value = source[key];
    if (value && typeof value === 'object' && !Array.isArray(value)
      && target[key] && typeof target[key] === 'object' && !Array.isArray(target[key])) {
      deepMerge(target[key], value);
    } else if (value !== undefined) {
      target[key] = clone(value);
    }
  });
  return target;
}

export const PreprocessPage = {
  name: 'PreprocessPage',
  components: { AppIcon, EmptyState },
  setup() {
    const config = reactive(clone(FALLBACK_DEFAULTS));
    const badRangesText = ref(rangesToText(FALLBACK_DEFAULTS.bad_bands.ranges));
    const defaults = ref(clone(FALLBACK_DEFAULTS));
    const processed = ref(null);
    const processedLoading = ref(false);
    const previewLayer = ref('rgb');
    const running = ref(false);
    const job = ref(null);
    const startError = ref('');
    let stopPolling = null;

    const images = computed(() => store.project?.images || []);
    const image = computed(() => images.value.find((im) => im.id === store.activeImageId) || images.value[0] || null);
    const statusMeta = computed(() => image.value ? imageStatusMeta(image.value.status, image.value.has_result) : { label: '', cls: '' });
    const quality = computed(() => processed.value?.quality || image.value?.preprocess?.quality || null);
    const qualityLevelLabel = computed(() => ({ ok: '正常', notice: '提示', warning: '警告' }[quality.value?.level] || '—'));
    const previewLayers = computed(() => Object.keys(processed.value?.previews || {}));
    const previewUrl = computed(() => {
      if (!processed.value) return '';
      const urls = processed.value.preview_urls || {};
      if (urls[previewLayer.value]) return urls[previewLayer.value];
      return api.processedPreviewUrl(store.project.id, image.value.id, previewLayer.value, processed.value.created_at || '');
    });
    const indices = computed(() => {
      const data = processed.value?.indices || {};
      return Object.entries(data).map(([key, value]) => ({ key, ...value }));
    });
    const mask = computed(() => processed.value?.mask || image.value?.preprocess?.mask || null);
    const targetBands = computed(() => {
      const a = config.alignment;
      if (!a.enabled) return image.value?.bands || 0;
      if (!a.step || a.end <= a.start) return 0;
      return Math.max(0, Math.floor((Number(a.end) - Number(a.start)) / Number(a.step) + 1.0001));
    });
    const progress = computed(() => Math.max(0, Math.min(100, Number(job.value?.progress) || 0)));
    const jobStage = computed(() => job.value?.stage || (running.value ? '正在执行…' : ''));
    const jobMessage = computed(() => job.value?.message || '');
    const layersLabel = { rgb: '真彩色', ndvi: 'NDVI', mask: '植被掩膜' };

    function resetConfig() {
      const base = clone(defaults.value);
      Object.keys(config).forEach((key) => { delete config[key]; });
      Object.assign(config, base);
      badRangesText.value = rangesToText(config.bad_bands.ranges);
      previewLayer.value = previewLayers.value.includes('ndvi') ? 'ndvi' : 'rgb';
    }

    async function loadDefaults() {
      try {
        const data = await api.preprocessDefaults();
        defaults.value = data;
      } catch (e) {
        defaults.value = clone(FALLBACK_DEFAULTS);
      }
    }

    async function loadProcessed() {
      if (!image.value) { processed.value = null; return; }
      processedLoading.value = true;
      try {
        processed.value = await api.getProcessed(store.project.id, image.value.id);
        const layers = Object.keys(processed.value.previews || {});
        if (!layers.includes(previewLayer.value)) {
          previewLayer.value = layers.includes('ndvi') ? 'ndvi' : (layers[0] || 'rgb');
        }
      } catch (err) {
        processed.value = null;
        if (err.status !== 404) notify(err.message, 'error');
      } finally {
        processedLoading.value = false;
      }
    }

    async function initConfig() {
      const base = clone(defaults.value || FALLBACK_DEFAULTS);
      const saved = image.value?.preprocess?.config;
      if (saved) deepMerge(base, saved);
      Object.keys(config).forEach((key) => { delete config[key]; });
      Object.assign(config, base);
      badRangesText.value = rangesToText(config.bad_bands.ranges || []);
    }

    watch(() => image.value && image.value.id, async () => {
      stopPolling && stopPolling();
      running.value = false;
      job.value = null;
      startError.value = '';
      await loadProcessed();
      await initConfig();
    }, { immediate: true });

    loadDefaults();

    function buildConfig() {
      const payload = clone(config);
      payload.bad_bands.ranges = parseRanges(badRangesText.value);
      payload.indices = Array.isArray(config.indices) ? [...config.indices] : [];
      if (payload.smoothing.window % 2 === 0) payload.smoothing.window += 1;
      return payload;
    }

    function validate(payload) {
      if (!payload.indices.length) return '请至少选择一个植被指数';
      if (payload.reflectance.enabled && payload.reflectance.method === 'scale' && !(payload.reflectance.scale > 0)) {
        return '反射率比例因子必须大于 0';
      }
      if (payload.bad_bands.enabled && !payload.bad_bands.ranges.length) {
        return '已启用坏波段剔除，但波段范围格式不正确（示例：1340-1450, 1800-1960）';
      }
      if (payload.smoothing.enabled) {
        if (payload.smoothing.window < 3) return '平滑窗口至少为 3';
        if (payload.smoothing.polyorder >= payload.smoothing.window) return '平滑多项式阶数必须小于窗口长度';
      }
      if (payload.alignment.enabled) {
        if (payload.alignment.end <= payload.alignment.start) return '波段对齐的结束波长必须大于起始波长';
        if (!(payload.alignment.step > 0)) return '波段对齐步长必须大于 0';
        if (targetBands.value > 400) return '目标波段过多，请增大对齐步长';
      }
      return '';
    }

    async function run() {
      if (!image.value || running.value) return;
      startError.value = '';
      const payload = buildConfig();
      const error = validate(payload);
      if (error) { startError.value = error; notify(error, 'error'); return; }
      running.value = true;
      job.value = { progress: 0, stage: '提交任务', message: '正在创建预处理任务…' };
      try {
        const { job_id } = await api.startPreprocess(store.project.id, image.value.id, payload);
        stopPolling = pollJob(job_id, {
          onUpdate: (j) => { job.value = j; },
          onDone: async (j) => {
            running.value = false;
            job.value = j;
            if (j.status === 'completed') {
              notify('预处理完成，质量检查结果已更新', 'success');
              await refreshProject();
              await loadProcessed();
            } else {
              notify(j.error || '预处理失败', 'error');
            }
          },
        });
      } catch (err) {
        running.value = false;
        startError.value = err.message;
        notify(err.message, 'error');
      }
    }

    function selectImage(e) {
      if (running.value) return;
      store.activeImageId = e.target.value;
    }

    onBeforeUnmount(() => { stopPolling && stopPolling(); });

    return {
      store, go, config, badRangesText, defaults, processed, processedLoading, previewLayer,
      running, job, startError, images, image, statusMeta, quality, qualityLevelLabel,
      previewLayers, previewUrl, indices, mask, targetBands, progress, jobStage, jobMessage,
      layersLabel, INDICES, resetConfig, run, selectImage,
      formatNumber, pct, resolutionText, imageStatusMeta, shortName,
    };
  },
  template: `
  <div class="page preprocess-page">
    <div class="page-head">
      <div class="ph-copy">
        <div class="eyebrow">PREPROCESSING</div>
        <h1>数据预处理</h1>
        <p class="ph-sub">默认参数已按科学实践预置，不改参数也能跑通。每一步参数可见可调，执行后给出反射率范围、掩膜比例等质量检查。</p>
      </div>
      <div class="ph-actions">
        <select class="select" style="width:320px" :value="image && image.id" @change="selectImage" :disabled="running" v-if="images.length">
          <option v-for="im in images" :key="im.id" :value="im.id">{{ shortName(im.name, 30) }}</option>
        </select>
        <button class="btn btn-primary" @click="go('inversion')" :disabled="!image"><app-icon name="arrow-right" :size="16"/>下一步：模型反演</button>
      </div>
    </div>

    <template v-if="image">
      <div class="preprocess-grid">
        <div class="preprocess-main">
          <section class="card">
            <div class="card-head">
              <div>
                <div class="card-title"><app-icon name="sliders" :size="17"/>处理流程</div>
                <div class="card-sub">共 {{ image.bands }} 个输入波段 · 目标 {{ targetBands }} 个标准波段</div>
              </div>
              <button class="btn btn-sm btn-ghost" @click="resetConfig" :disabled="running"><app-icon name="rotate-ccw" :size="14"/>恢复默认参数</button>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.reflectance.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">1</span>
                <div style="flex:1"><div class="ps-title">反射率转换</div><div class="ps-desc">将 DN 值转换为地表反射率，或直接使用原始数值</div></div>
                <label class="switch"><input type="checkbox" v-model="config.reflectance.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.reflectance.enabled">
                <div class="param-grid">
                  <div class="field">
                    <label>转换方式</label>
                    <select class="select" v-model="config.reflectance.method">
                      <option value="scale">按比例因子（DN / 因子）</option>
                      <option value="panel">按定标系数（参考板）</option>
                      <option value="none">不转换，使用原始值</option>
                    </select>
                  </div>
                  <div class="field" v-if="config.reflectance.method === 'scale'">
                    <label>比例因子</label>
                    <input class="input" type="number" min="1" step="100" v-model.number="config.reflectance.scale">
                  </div>
                  <template v-if="config.reflectance.method === 'panel'">
                    <div class="field"><label>定标系数</label><input class="input" type="number" step="0.0001" v-model.number="config.reflectance.coefficient"></div>
                    <div class="field"><label>偏移量</label><input class="input" type="number" step="0.0001" v-model.number="config.reflectance.offset"></div>
                  </template>
                </div>
                <div class="help-text" style="margin-top:8px">示例：DN 值范围 0–10000 通常使用比例因子 10000；若数据已是 0–1 反射率，请选择“不转换”。</div>
              </div>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.bad_bands.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">2</span>
                <div style="flex:1"><div class="ps-title">坏波段剔除</div><div class="ps-desc">剔除水汽吸收带等不可靠波段，可填写多个波长范围</div></div>
                <label class="switch"><input type="checkbox" v-model="config.bad_bands.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.bad_bands.enabled">
                <div class="field">
                  <label>剔除范围（nm）</label>
                  <input class="input" v-model="badRangesText" placeholder="1340-1450, 1800-1960">
                  <div class="help-text">多个范围用逗号分隔；仅在影像提供中心波长时生效。</div>
                </div>
              </div>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.smoothing.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">3</span>
                <div style="flex:1"><div class="ps-title">光谱平滑去噪</div><div class="ps-desc">Savitzky-Golay 滤波，降低随机噪声，保留光谱形状</div></div>
                <label class="switch"><input type="checkbox" v-model="config.smoothing.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.smoothing.enabled">
                <div class="param-grid">
                  <div class="field"><label>窗口长度 <span class="unit">（奇数）</span></label><input class="input" type="number" min="3" max="31" step="2" v-model.number="config.smoothing.window"></div>
                  <div class="field"><label>多项式阶数</label><input class="input" type="number" min="1" max="6" step="1" v-model.number="config.smoothing.polyorder"></div>
                </div>
                <div class="help-text" style="margin-top:8px">若窗口大于影像波段数，平台会自动缩小窗口；偶数会自动加 1。</div>
              </div>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.alignment.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">4</span>
                <div style="flex:1"><div class="ps-title">波段对齐 / 重采样</div><div class="ps-desc">将不同传感器统一到模型训练波长，是零样本反演的前提</div></div>
                <label class="switch"><input type="checkbox" v-model="config.alignment.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.alignment.enabled">
                <div class="param-grid four" style="grid-template-columns:repeat(3,minmax(0,1fr))">
                  <div class="field"><label>起始波长 (nm)</label><input class="input" type="number" step="10" v-model.number="config.alignment.start"></div>
                  <div class="field"><label>结束波长 (nm)</label><input class="input" type="number" step="10" v-model.number="config.alignment.end"></div>
                  <div class="field"><label>步长 (nm)</label><input class="input" type="number" step="1" min="1" v-model.number="config.alignment.step"></div>
                </div>
                <div class="help-text" style="margin-top:8px">将生成 {{ targetBands }} 个标准波段。超出源影像范围的边缘波长采用最近邻填充，并在质量检查中提示。</div>
              </div>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.mask.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">5</span>
                <div style="flex:1"><div class="ps-title">植被掩膜</div><div class="ps-desc">基于 NDVI 剔除裸土、道路等非植被像元，提升反演可靠性</div></div>
                <label class="switch"><input type="checkbox" v-model="config.mask.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.mask.enabled">
                <div class="param-grid">
                  <div class="field"><label>NDVI 阈值</label><input class="input" type="number" min="-1" max="1" step="0.01" v-model.number="config.mask.ndvi_threshold"></div>
                  <div class="field"><label>近红外目标波长 (nm)</label><input class="input" type="number" step="10" v-model.number="config.mask.nir_wavelength"></div>
                  <div class="field"><label>红光目标波长 (nm)</label><input class="input" type="number" step="10" v-model.number="config.mask.red_wavelength"></div>
                </div>
                <div class="help-text" style="margin-top:8px">默认阈值 0.35，对应常见农田植被覆盖条件；阈值越高，保留的像元越少。</div>
              </div>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.cloud.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">6</span>
                <div style="flex:1"><div class="ps-title">云 / 阴影检测</div><div class="ps-desc">适用于卫星影像；按蓝光亮度阈值剔除疑似云像元</div></div>
                <label class="switch"><input type="checkbox" v-model="config.cloud.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.cloud.enabled">
                <div class="field" style="max-width:240px">
                  <label>蓝光反射率阈值</label>
                  <input class="input" type="number" min="0" max="1" step="0.01" v-model.number="config.cloud.blue_threshold">
                </div>
                <div class="help-text" style="margin-top:8px">反射率高于该阈值的像元将被视为云并排除；无人机影像通常可不启用。</div>
              </div>
            </div>

            <div class="pipeline-step" :class="{ enabled: config.crop.enabled }">
              <div class="pipeline-step-head">
                <span class="ps-num">7</span>
                <div style="flex:1"><div class="ps-title">感兴趣区裁剪</div><div class="ps-desc">按像素坐标裁剪目标田块；留 0 表示使用全图</div></div>
                <label class="switch"><input type="checkbox" v-model="config.crop.enabled"><span class="track"></span></label>
              </div>
              <div class="pipeline-step-body" v-show="config.crop.enabled">
                <div class="param-grid four">
                  <div class="field"><label>起点 X（列）</label><input class="input" type="number" min="0" v-model.number="config.crop.x0"></div>
                  <div class="field"><label>起点 Y（行）</label><input class="input" type="number" min="0" v-model.number="config.crop.y0"></div>
                  <div class="field"><label>宽度</label><input class="input" type="number" min="0" v-model.number="config.crop.width" placeholder="0 = 剩余宽度"></div>
                  <div class="field"><label>高度</label><input class="input" type="number" min="0" v-model.number="config.crop.height" placeholder="0 = 剩余高度"></div>
                </div>
                <div class="help-text" style="margin-top:8px">当前影像 {{ image.width }} × {{ image.height }} 像元（列 × 行）。</div>
              </div>
            </div>

            <div class="pipeline-step enabled">
              <div class="pipeline-step-head">
                <span class="ps-num">8</span>
                <div style="flex:1"><div class="ps-title">植被指数计算</div><div class="ps-desc">生成用于分析、对比与模型输入的植被指数图层</div></div>
              </div>
              <div class="pipeline-step-body">
                <div class="row wrap" style="gap:16px">
                  <label v-for="ix in INDICES" :key="ix.key" class="check-row">
                    <input type="checkbox" :value="ix.key" v-model="config.indices">
                    <span><b>{{ ix.label }}</b> <span class="muted tiny">{{ ix.desc }}</span></span>
                  </label>
                </div>
                <div class="help-text" style="margin-top:8px">NDVI/NDRE 等指数需要对应的波段中心波长；缺失时会给出质量检查提示。</div>
              </div>
            </div>
          </section>

          <section class="card" v-if="quality">
            <div class="card-head">
              <div>
                <div class="card-title"><app-icon name="check-circle" :size="17"/>预处理质量检查 <span class="badge" :class="quality.level === 'ok' ? 'green' : quality.level === 'warning' ? 'red' : 'amber'">{{ qualityLevelLabel }}</span></div>
                <div class="card-sub">不拦截流程，仅提示潜在问题；红色项建议调整参数后重新处理</div>
              </div>
              <span class="tag" v-if="mask">有效像元 {{ pct(mask.ratio, 1) }}</span>
            </div>
            <div class="quality-list">
              <div v-for="item in quality.items" :key="item.key" class="quality-item" :class="item.status">
                <span class="qi-icon"><app-icon :name="item.status === 'ok' ? 'check' : (item.status === 'warning' ? 'alert-triangle' : 'info')" :size="13"/></span>
                <div style="flex:1;min-width:0">
                  <div class="qi-title">{{ item.title }}</div>
                  <div class="qi-msg">{{ item.message }}</div>
                </div>
                <span class="qi-status">{{ item.status === 'ok' ? '正常' : item.status === 'warning' ? '警告' : '提示' }}</span>
              </div>
            </div>
          </section>

          <section class="card" v-if="processed">
            <div class="card-head">
              <div>
                <div class="card-title"><app-icon name="image" :size="17"/>预处理结果预览</div>
                <div class="card-sub">{{ processed.width }} × {{ processed.height }} 像元 · {{ processed.bands }} 个标准波段 · 完成于 {{ processed.created_at ? processed.created_at.slice(0, 16).replace('T', ' ') : '' }}</div>
              </div>
              <div class="preview-layer-tabs">
                <button v-for="layer in previewLayers" :key="layer" class="btn btn-sm" :class="previewLayer === layer ? 'btn-soft' : 'btn-ghost'" @click="previewLayer = layer">{{ layersLabel[layer] || layer }}</button>
              </div>
            </div>
            <div class="preview-frame">
              <img :src="previewUrl" :alt="layersLabel[previewLayer]" v-if="previewUrl">
              <div class="pf-hint">{{ layersLabel[previewLayer] }} · 由预处理结果实时生成</div>
            </div>
            <div class="index-cards" v-if="indices.length">
              <div class="index-card" v-for="ix in indices" :key="ix.key">
                <div class="ix-n">{{ ix.label || ix.key }}</div>
                <div class="ix-v">{{ ix.mean !== undefined && ix.mean !== null ? formatNumber(ix.mean, 3) : '—' }}</div>
                <div class="ix-m">均值 · 范围 {{ ix.min !== undefined ? formatNumber(ix.min, 2) : '—' }} ~ {{ ix.max !== undefined ? formatNumber(ix.max, 2) : '—' }}</div>
              </div>
            </div>
          </section>
        </div>

        <aside class="preprocess-side">
          <section class="card run-card">
            <div class="card-title"><app-icon name="play" :size="17"/>执行预处理</div>
            <p class="run-sub">按当前参数依次执行反射率转换、平滑、波段对齐、掩膜与指数计算。处理结果会保存到项目目录。</p>
            <div class="summary-box" style="border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);border-radius:10px;padding:10px 11px;margin-top:12px">
              <div class="row" style="justify-content:space-between;font-size:11.5px;color:#b5cdbb"><span>输入影像</span><strong style="color:#f0f8f1;max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ image.name }}</strong></div>
              <div class="row" style="justify-content:space-between;font-size:11.5px;color:#b5cdbb;margin-top:4px"><span>输入 / 输出波段</span><span style="color:#f0f8f1">{{ image.bands }} → {{ targetBands }}</span></div>
              <div class="row" style="justify-content:space-between;font-size:11.5px;color:#b5cdbb;margin-top:4px"><span>植被掩膜</span><span style="color:#f0f8f1">{{ config.mask.enabled ? 'NDVI ≥ ' + config.mask.ndvi_threshold : '未启用' }}</span></div>
            </div>
            <button class="btn btn-primary btn-block btn-lg" style="margin-top:14px" @click="run" :disabled="running">
              <span v-if="running" class="spinner"></span>
              <app-icon v-else name="play" :size="16"/>
              {{ running ? '正在处理…' : (processed ? '重新执行预处理' : '开始预处理') }}
            </button>
            <div class="run-status" v-if="running || (job && job.status === 'failed')">
              <div class="progress-track"><div class="progress-fill" :style="{ width: progress + '%' }"></div></div>
              <div class="progress-meta"><span>{{ jobStage }}</span><span>{{ progress }}%</span></div>
              <div class="tiny" style="color:#a9c6b0;margin-top:5px">{{ jobMessage }}</div>
              <div class="hint-box danger" style="margin-top:8px" v-if="job && job.status === 'failed'">{{ job.error }}</div>
            </div>
            <div v-if="startError" class="hint-box danger" style="margin-top:10px;background:rgba(192,57,43,.15);border-color:rgba(192,57,43,.3);color:#ffd9d4">{{ startError }}</div>
            <div class="run-status" v-if="quality">
              <div class="run-sub" style="margin:0">最近一次质量检查：<b style="color:#fff">{{ qualityLevelLabel }}</b> · 有效像元 {{ mask ? pct(mask.ratio, 1) : '—' }}</div>
            </div>
          </section>

          <section class="card">
            <div class="card-title" style="margin-bottom:9px"><app-icon name="image" :size="16"/>当前影像</div>
            <div class="info-list">
              <div class="info-row"><span class="k">状态</span><span class="v"><span class="status-badge" :class="statusMeta.cls" style="height:20px;font-size:10px"><i class="dot"></i>{{ statusMeta.label }}</span></span></div>
              <div class="info-row"><span class="k">行列数</span><span class="v mono">{{ image.width }} × {{ image.height }}</span></div>
              <div class="info-row"><span class="k">空间分辨率</span><span class="v mono">{{ resolutionText(image) }}</span></div>
              <div class="info-row"><span class="k">数据类型</span><span class="v mono">{{ image.dtype }}</span></div>
              <div class="info-row"><span class="k">中心波长</span><span class="v">{{ (image.wavelengths || []).length === image.bands ? '已提供' : '缺失' }}</span></div>
            </div>
            <button class="btn btn-sm btn-ghost btn-block" style="margin-top:10px" @click="go('viewer')"><app-icon name="eye" :size="14"/>查看原始影像</button>
          </section>

          <section class="card">
            <div class="card-title" style="margin-bottom:7px"><app-icon name="info" :size="16"/>参数说明</div>
            <div class="help-text" style="display:flex;flex-direction:column;gap:7px">
              <p>· 波段对齐默认将影像重采样到 <b>400–900 nm / 10 nm</b>，与内置模型训练波长一致。</p>
              <p>· 平滑窗口为偶数时会自动加 1；窗口大于波段数时自动缩小。</p>
              <p>· 重新预处理会覆盖处理结果并更新状态，旧反演结果文件仍保留以备追溯。</p>
            </div>
          </section>
        </aside>
      </div>
    </template>

    <div v-else class="card">
      <EmptyState icon="sliders" title="还没有可预处理的影像" text="请先在数据导入页上传影像，再回到这里执行预处理。">
        <button class="btn btn-primary" @click="go('import')"><app-icon name="upload" :size="16"/>去导入影像</button>
      </EmptyState>
    </div>
  </div>`,
};
