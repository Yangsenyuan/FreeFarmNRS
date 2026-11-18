/* 模型反演页 */
import { api } from '../api.js';
import { store, go, notify, refreshProject, loadModels, pollJob } from '../store.js';
import { formatDate, formatNumber, imageStatusMeta, jobStatusMeta, jobTypeLabel, dateFieldLabel } from '../utils.js';
import { AppIcon } from '../components/icons.js';
import { EmptyState, Modal } from '../components/ui.js';

const { ref, reactive, computed, watch, onMounted, onBeforeUnmount } = window.Vue;

const MODEL_ORDER = {
  vi_ndre_general: 0,
  rf_multispectral_demo: 1,
  dnn_multispectral_demo: 2,
  plsr_nmass_demo: 3,
  rf_nmass_demo: 4,
  svr_nmass_demo: 5,
  dnn_nmass_demo: 6,
};

const CARD_STATUS = {
  recommended: { label: '推荐', cls: 'green' },
  resample: { label: '可重采样', cls: 'amber' },
  incompatible: { label: '不兼容', cls: 'red' },
  unknown: { label: '信息不足', cls: 'amber' },
  confirm: { label: '需确认', cls: 'amber' },
  checking: { label: '检查中', cls: 'gray' },
};

function defaultWavelengths(im) {
  if (im && Array.isArray(im.wavelengths) && im.wavelengths.length === Number(im.bands)) {
    return im.wavelengths.join(', ');
  }
  return Array.from({ length: 51 }, (_, i) => 400 + i * 10).join(', ');
}

export const InversionPage = {
  name: 'InversionPage',
  components: { AppIcon, EmptyState, Modal },
  setup() {
    const selectedModelId = ref('');
    const low = ref(22);
    const high = ref(28);
    const autoPreprocess = ref(true);
    const running = ref(false);
    const activeJob = ref(null);
    const jobMode = ref('single');
    const startError = ref('');
    const jobsList = ref([]);
    const jobsLoading = ref(false);
    const batchOpen = ref(false);
    const batchIds = ref([]);
    const batchError = ref('');
    const compatibilityByModel = ref({});
    const compatibilityLoading = ref(false);
    const compatibilityError = ref('');
    const compatConfirmOpen = ref(false);
    const pendingRunMode = ref('single');
    const batchCompatibility = ref(null);
    const batchCompatibilityLoading = ref(false);
    const batchForce = ref(false);
    const importOpen = ref(false);
    const importFile = ref(null);
    const importInput = ref(null);
    const importing = ref(false);
    const importError = ref('');
    const importedTrial = ref(null);
    let stopPolling = null;

    const importForm = reactive({
      name: '',
      algorithm: 'PLSR',
      crops: '玉米',
      sensors: '多光谱',
      wavelengths: '',
      training_data: '',
      r2: '',
      rmse: '',
      unit: 'mg/g',
      low: '22',
      high: '28',
      trusted_source: false,
      version: '1.0',
      description: '',
    });

    const images = computed(() => store.project?.images || []);
    const image = computed(() => images.value.find((im) => im.id === store.activeImageId) || images.value[0] || null);
    const models = computed(() => [...store.models].sort((a, b) => {
      const oa = MODEL_ORDER[a.id] ?? 50;
      const ob = MODEL_ORDER[b.id] ?? 50;
      if (oa !== ob) return oa - ob;
      return String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN');
    }));
    const selectedModel = computed(() => models.value.find((m) => m.id === selectedModelId.value) || null);
    const selectedUnit = computed(() => String(selectedModel.value?.unit || '待确认'));
    const importNeedsTrust = computed(() => !!importFile.value && !String(importFile.value.name || '').toLowerCase().endsWith('.onnx'));
    const selectedCompatibility = computed(() => compatibilityByModel.value[selectedModelId.value] || null);
    const needsPreprocess = computed(() => !!image.value && !image.value.has_processed);
    const warnings = computed(() => {
      const out = [...((selectedCompatibility.value?.errors || [])), ...((selectedCompatibility.value?.warnings || []))];
      if (!autoPreprocess.value && needsPreprocess.value) {
        out.push('当前影像尚未预处理，且未勾选自动预处理，反演将无法执行。');
      }
      return out;
    });
    const recommendationRank = (m) => {
      const a = compatibilityByModel.value[m.id];
      if (!a || m.available === false || a.status === 'incompatible') return 999;
      const order = MODEL_ORDER[m.id] ?? 50;
      return (a.status === 'warning' ? 100 : 0)
        + (a.crop_match === 'match' ? 0 : 10)
        + (a.sensor_match === 'match' ? 0 : 5)
        + (a.edge_filled_bands || 0)
        + order / 100;
    };
    const recommendedModelId = computed(() => {
      const candidates = models.value
        .filter((m) => m.available !== false)
        .map((m) => ({ model: m, rank: recommendationRank(m) }))
        .filter((item) => item.rank < 999)
        .sort((a, b) => a.rank - b.rank);
      return candidates[0]?.model.id || '';
    });
    const jobPercent = computed(() => Math.max(0, Math.min(100, Number(activeJob.value?.progress) || 0)));
    const jobDone = computed(() => activeJob.value && ['completed', 'failed'].includes(activeJob.value.status));
    const jobSingleSummary = computed(() => {
      if (!activeJob.value || jobMode.value !== 'single') return null;
      const result = activeJob.value.result;
      return result && result.summary ? result.summary : null;
    });
    const jobBatchSummary = computed(() => {
      if (!activeJob.value || jobMode.value !== 'batch') return null;
      const result = activeJob.value.result;
      return result && Array.isArray(result.results) ? result : null;
    });
    const batchImages = computed(() => images.value.filter((im) => batchIds.value.includes(im.id)));
    const canRun = computed(() => !!image.value && !!selectedModel.value && !running.value
      && !!selectedCompatibility.value
      && selectedCompatibility.value.status !== 'incompatible'
      && (autoPreprocess.value || !needsPreprocess.value));

    function cardStatus(model) {
      if (model.available === false) return 'incompatible';
      const assessment = compatibilityByModel.value[model.id];
      if (!assessment) return 'checking';
      if (model.id === recommendedModelId.value && assessment.status !== 'incompatible') return 'recommended';
      return assessment.card_status || (assessment.status === 'warning' ? 'unknown' : 'recommended');
    }
    function cardStatusMeta(model) { return CARD_STATUS[cardStatus(model)] || CARD_STATUS.checking; }

    let compatibilitySeq = 0;
    async function refreshCompatibility({ imageChanged = false } = {}) {
      if (!store.project || !image.value) {
        compatibilityByModel.value = {};
        return;
      }
      const seq = ++compatibilitySeq;
      compatibilityLoading.value = true;
      compatibilityError.value = '';
      try {
        const data = await api.imageModelCompatibility(store.project.id, image.value.id);
        if (seq !== compatibilitySeq) return;
        const map = {};
        (data.assessments || []).forEach((item) => {
          if (item.model_id) map[item.model_id] = item;
        });
        compatibilityByModel.value = map;
        const current = map[selectedModelId.value];
        const recommended = recommendedModelId.value;
        if (
          recommended
          && (!selectedModelId.value || !models.value.some((m) => m.id === selectedModelId.value))
        ) {
          selectedModelId.value = recommended;
        } else if (imageChanged && current && current.status === 'incompatible' && recommended) {
          const previous = selectedModel.value?.name || '原模型';
          selectedModelId.value = recommended;
          notify(`${previous} 当前不兼容，已切换为推荐模型`, 'info', '模型已重选');
        }
      } catch (err) {
        if (seq === compatibilitySeq) compatibilityError.value = err.message;
      } finally {
        if (seq === compatibilitySeq) compatibilityLoading.value = false;
      }
    }

    watch(() => models.value.map((m) => m.id).join('|'), () => {
      refreshCompatibility();
    }, { immediate: true });
    watch(() => image.value && image.value.id, () => {
      compatibilityByModel.value = {};
      refreshCompatibility({ imageChanged: true });
    });
    watch(selectedModel, (m) => {
      if (!m) return;
      low.value = Number(m.thresholds && m.thresholds.low !== undefined ? m.thresholds.low : 22);
      high.value = Number(m.thresholds && m.thresholds.high !== undefined ? m.thresholds.high : 28);
    }, { immediate: true });
    watch(batchOpen, (open) => { if (!open) batchError.value = ''; });
    watch(selectedModelId, () => {
      if (batchOpen.value) loadBatchCompatibility();
    });
    watch(importOpen, (open) => {
      if (open) {
        importError.value = '';
        importFile.value = null;
        importedTrial.value = null;
        importForm.trusted_source = false;
        importForm.wavelengths = defaultWavelengths(image.value);
      }
    });

    function selectImage(id) { store.activeImageId = id; }
    function selectModel(id) { selectedModelId.value = id; }

    async function loadBatchCompatibility() {
      if (!batchOpen.value || !selectedModelId.value || !batchIds.value.length) {
        batchCompatibility.value = null;
        return;
      }
      batchCompatibilityLoading.value = true;
      try {
        batchCompatibility.value = await api.batchModelCompatibility(store.project.id, {
          image_ids: batchIds.value,
          model_id: selectedModelId.value,
        });
        if (!batchCompatibility.value.requires_confirmation) batchForce.value = false;
      } catch (err) {
        batchError.value = err.message;
        batchCompatibility.value = null;
      } finally {
        batchCompatibilityLoading.value = false;
      }
    }

    async function loadJobs() {
      if (!store.project) return;
      jobsLoading.value = true;
      try {
        const data = await api.listJobs(store.project.id);
        jobsList.value = data.jobs || [];
      } catch (e) { /* ignore */ }
      finally { jobsLoading.value = false; }
    }

    function validateThresholds() {
      if (!(Number(low.value) < Number(high.value))) {
        startError.value = '高氮阈值必须大于低氮阈值';
        notify(startError.value, 'error');
        return false;
      }
      return true;
    }

    function beginFollow(jobId, mode) {
      jobMode.value = mode;
      running.value = true;
      stopPolling && stopPolling();
      stopPolling = pollJob(jobId, {
        onUpdate: (j) => { activeJob.value = j; },
        onDone: async (j) => {
          running.value = false;
          activeJob.value = j;
          await refreshProject();
          await loadJobs();
          if (j.status === 'completed') {
            if (mode === 'single') {
              const summary = j.result && j.result.summary;
              notify(summary ? `反演完成：均值 ${summary.stats.mean} ${summary.unit || '待确认'}` : '反演完成', 'success');
            } else {
              const r = j.result || {};
              notify(`批量反演完成：成功 ${(r.results || []).length} 幅${r.errors && r.errors.length ? '，失败 ' + r.errors.length + ' 幅' : ''}`, 'success');
            }
          } else {
            notify(j.error || '反演失败', 'error');
          }
        },
      });
    }

    async function submitSingle(force = false) {
      if (!image.value || !selectedModel.value || running.value) return;
      startError.value = '';
      if (!validateThresholds()) return;
      if (!autoPreprocess.value && needsPreprocess.value) {
        startError.value = '当前影像尚未预处理，请先执行预处理或勾选自动预处理';
        return;
      }
      try {
        const { job_id } = await api.startInversion(store.project.id, image.value.id, {
          model_id: selectedModelId.value,
          low: Number(low.value),
          high: Number(high.value),
          auto_preprocess: autoPreprocess.value,
          force,
        });
        activeJob.value = { status: 'queued', progress: 0, stage: '排队中', message: '已提交任务' };
        beginFollow(job_id, 'single');
      } catch (err) {
        startError.value = err.message;
        notify(err.message, 'error');
      }
    }

    async function runSingle() {
      if (!image.value || !selectedModel.value || running.value) return;
      startError.value = '';
      if (!validateThresholds()) return;
      if (!autoPreprocess.value && needsPreprocess.value) {
        startError.value = '当前影像尚未预处理，请先执行预处理或勾选自动预处理';
        return;
      }
      const assessment = selectedCompatibility.value;
      if (!assessment) {
        startError.value = '模型适用性预检尚未完成，请稍后重试';
        return;
      }
      if (assessment.status === 'incompatible') {
        startError.value = (assessment.errors || []).join('；') || '当前模型与影像不兼容';
        notify(startError.value, 'error');
        return;
      }
      if (assessment.status === 'warning') {
        pendingRunMode.value = 'single';
        compatConfirmOpen.value = true;
        return;
      }
      await submitSingle(false);
    }

    function openBatch() {
      batchError.value = '';
      batchIds.value = images.value.map((im) => im.id);
      batchForce.value = false;
      batchOpen.value = true;
      loadBatchCompatibility();
    }
    function toggleBatch(id) {
      if (batchIds.value.includes(id)) batchIds.value = batchIds.value.filter((x) => x !== id);
      else batchIds.value = [...batchIds.value, id];
      loadBatchCompatibility();
    }
    function selectAllBatch() { batchIds.value = images.value.map((im) => im.id); loadBatchCompatibility(); }
    function clearBatch() { batchIds.value = []; loadBatchCompatibility(); }

    async function submitBatch(force = false) {
      if (!batchIds.value.length || !selectedModelId.value || running.value) return;
      batchError.value = '';
      if (!validateThresholds()) { batchError.value = startError.value; return; }
      try {
        const { job_id } = await api.startBatchInversion(store.project.id, {
          image_ids: batchIds.value,
          model_id: selectedModelId.value,
          low: Number(low.value),
          high: Number(high.value),
          auto_preprocess: autoPreprocess.value,
          force,
        });
        activeJob.value = { status: 'queued', progress: 0, stage: '排队中', message: '批量任务已提交' };
        beginFollow(job_id, 'batch');
        batchOpen.value = false;
      } catch (err) {
        batchError.value = err.message;
        notify(err.message, 'error');
      }
    }

    async function runBatch() {
      if (!batchIds.value.length || !selectedModelId.value || running.value) return;
      batchError.value = '';
      if (!validateThresholds()) { batchError.value = startError.value; return; }
      if (!batchCompatibility.value) {
        await loadBatchCompatibility();
      }
      const view = batchCompatibility.value;
      if (!view) {
        batchError.value = '尚未取得逐幅兼容性预检结果';
        return;
      }
      if (!view.can_run) {
        batchError.value = '预检表包含不兼容影像，请更换模型或调整所选影像';
        return;
      }
      if (view.requires_confirmation) {
        pendingRunMode.value = 'batch';
        compatConfirmOpen.value = true;
        return;
      }
      await submitBatch(false);
    }

    async function confirmCompatibilityRun() {
      compatConfirmOpen.value = false;
      if (pendingRunMode.value === 'batch') {
        batchForce.value = true;
        await submitBatch(true);
      } else {
        await submitSingle(true);
      }
    }

    function openImport() {
      importOpen.value = true;
    }
    function chooseModelFile() { if (importInput.value) importInput.value.click(); }
    function onModelFile(e) {
      importFile.value = e.target.files && e.target.files[0] ? e.target.files[0] : null;
    }
    async function submitImport() {
      importError.value = '';
      if (!importFile.value) { importError.value = '请选择模型文件（.pkl / .joblib / .onnx）'; return; }
      if (!importForm.name.trim()) { importError.value = '请填写模型名称'; return; }
      if (importNeedsTrust.value && !importForm.trusted_source) { importError.value = '请先确认模型文件来自可信来源'; return; }
      if (!(Number(importForm.low) < Number(importForm.high))) { importError.value = '高氮阈值必须大于低氮阈值'; return; }
      const fd = new FormData();
      fd.append('file', importFile.value, importFile.value.name);
      Object.entries(importForm).forEach(([key, value]) => fd.append(key, value === null || value === undefined ? '' : value));
      importing.value = true;
      try {
        const model = await api.importModel(fd);
        await loadModels();
        selectedModelId.value = model.id;
        importedTrial.value = model.validation?.trial_inference
          ? { ...model.validation.trial_inference, file_sha256: model.file_sha256, unit: model.unit }
          : null;
        notify(`模型「${model.name}」已导入并通过试推理`, 'success');
      } catch (err) {
        importError.value = err.message;
      } finally {
        importing.value = false;
      }
    }

    function goResults() {
      const summary = jobSingleSummary.value;
      if (summary && summary.image_id) store.activeImageId = summary.image_id;
      go('results');
    }
    function goJobResults(job) {
      const result = job.result || {};
      if (job.type === 'inversion' && result.summary && result.summary.image_id) {
        store.activeImageId = result.summary.image_id;
      }
      go('results');
    }

    onMounted(loadJobs);
    onBeforeUnmount(() => { stopPolling && stopPolling(); });

    return {
      store, go, images, image, models, selectedModel, selectedUnit, selectedModelId, low, high, autoPreprocess,
      running, activeJob, jobMode, startError, warnings, needsPreprocess, canRun, jobPercent, jobDone,
      selectedCompatibility, compatibilityLoading, compatibilityError, compatibilityByModel, recommendedModelId,
      compatConfirmOpen, pendingRunMode, confirmCompatibilityRun, cardStatus, cardStatusMeta,
      jobSingleSummary, jobBatchSummary, jobsList, jobsLoading,
      batchOpen, batchIds, batchError, batchImages, batchCompatibility, batchCompatibilityLoading, batchForce,
      importOpen, importFile, importInput, importing, importError, importForm, importedTrial, importNeedsTrust,
      selectImage, selectModel, runSingle, openBatch, toggleBatch, selectAllBatch, clearBatch, runBatch,
      loadBatchCompatibility,
      openImport, chooseModelFile, onModelFile, submitImport, goResults, goJobResults,
      loadJobs, formatDate, formatNumber, imageStatusMeta, jobStatusMeta, jobTypeLabel,
      dateFieldLabel,
    };
  },
  template: `
  <div class="page inversion-page">
    <div class="page-head">
      <div class="ph-copy">
        <div class="eyebrow">MODEL INFERENCE</div>
        <h1>模型反演</h1>
        <p class="ph-sub">选择影像与模型，一键输出田块 Nmass 分布。支持批量排队处理，进度实时可见；也可导入自己的训练模型。</p>
      </div>
      <div class="ph-actions">
        <button class="btn btn-ghost" @click="openImport"><app-icon name="file-plus" :size="16"/>导入模型</button>
        <button class="btn btn-primary" @click="go('results')"><app-icon name="chart" :size="16"/>查看结果</button>
      </div>
    </div>

    <div class="inversion-grid">
      <div class="inversion-col imgs">
        <section class="card">
          <div class="card-head">
            <div class="card-title"><app-icon name="image" :size="16"/>① 选择影像</div>
            <span class="badge gray">{{ images.length }}</span>
          </div>
          <div class="pick-list" v-if="images.length">
            <button v-for="im in images" :key="im.id" class="pick-item" :class="{ active: image && image.id === im.id }" @click="selectImage(im.id)">
              <img :src="im.thumb_url" alt="" loading="lazy">
              <div class="pi-main">
                <div class="pi-name">{{ im.name }}</div>
                <div class="pi-meta">
                  <span class="status-badge" :class="imageStatusMeta(im.status, im.has_result).cls" style="height:18px;font-size:9.5px;padding:0 6px"><i class="dot"></i>{{ imageStatusMeta(im.status, im.has_result).label }}</span>
                  <span>{{ im.bands }} 波段</span>
                  <span v-if="!im.has_processed" style="color:var(--warning)">将自动预处理</span>
                </div>
              </div>
            </button>
          </div>
          <EmptyState v-else icon="image" title="暂无影像" text="请先在数据导入页上传影像。">
            <button class="btn btn-sm btn-primary" @click="go('import')">去导入</button>
          </EmptyState>
          <div class="hint-box" style="margin-top:10px" v-if="image">
            <app-icon name="info" :size="14"/>
            <span>{{ image.has_processed ? '该影像已完成预处理，将直接使用现有结果反演。' : '该影像尚未预处理，一键反演时会自动执行默认预处理流程。' }}</span>
          </div>
        </section>
      </div>

      <div class="inversion-col models">
        <section class="card">
          <div class="model-top">
            <div>
              <div class="card-title"><app-icon name="cpu" :size="16"/>② 选择模型 <span class="badge gray">{{ models.length }}</span></div>
              <div class="card-sub">模型卡片展示适用作物、传感器与精度指标</div>
            </div>
            <button class="btn btn-sm btn-ghost" @click="openImport"><app-icon name="plus" :size="14"/>导入</button>
          </div>

          <div class="model-grid" v-if="models.length">
            <button v-for="m in models" :key="m.id" class="model-card" :class="{ selected: m.id === selectedModelId, unavailable: m.available === false || cardStatus(m) === 'incompatible' }" :disabled="m.available === false || cardStatus(m) === 'incompatible'" @click="selectModel(m.id)">
              <div class="mc-head">
                <div style="min-width:0">
                  <div class="mc-name">{{ m.name }}</div>
                  <div class="tag-row" style="margin-top:5px">
                    <span class="tag green">{{ m.algorithm }}</span>
                    <span class="tag amber" v-if="m.demo">示例</span>
                    <span class="tag gray" v-if="m.builtin">内置</span>
                    <span class="tag violet" v-if="m.model_kind === 'few_shot_calibrated'">实测校正</span>
                    <span class="tag" :class="cardStatusMeta(m).cls">{{ cardStatusMeta(m).label }}</span>
                  </div>
                </div>
                <span class="model-check"><app-icon v-if="m.id === selectedModelId" name="check" :size="12" :stroke-width="2.8"/></span>
              </div>
              <div class="mc-desc">{{ m.description }}</div>
              <div class="tag-row">
                <span class="tag" v-for="c in (m.crops || []).slice(0,3)" :key="c">{{ c }}</span>
                <span class="tag" v-for="s in (m.sensors || []).slice(0,1)" :key="s">{{ s }}</span>
              </div>
              <div class="mc-metrics">
                <div class="metric"><span class="m-l">R²</span><span class="m-v">{{ m.r2 !== undefined && m.r2 !== null ? formatNumber(m.r2, 3) : '—' }}</span></div>
                <div class="metric"><span class="m-l">RMSE</span><span class="m-v">{{ m.rmse !== undefined && m.rmse !== null ? formatNumber(m.rmse, 2) : '—' }}</span></div>
                <div class="metric"><span class="m-l">单位</span><span class="m-v" style="font-size:12px">{{ m.unit || '待确认' }}</span></div>
              </div>
              <div class="tiny muted" style="margin-top:7px" v-if="compatibilityByModel[m.id]">
                精确 {{ compatibilityByModel[m.id].exact_bands }} · 插值 {{ compatibilityByModel[m.id].interpolated_bands }} · 边缘填充 {{ compatibilityByModel[m.id].edge_filled_bands }}
              </div>
              <div class="tiny muted" style="margin-top:5px" v-if="m.model_kind === 'few_shot_calibrated'">
                父模型：{{ m.parent_model_name || m.parent_model_id || '—' }} · {{ m.few_shot?.sample_count || 0 }} 个实测样点
              </div>
            </button>
          </div>
          <EmptyState v-else icon="cpu" title="模型库为空" text="导入 .pkl / .joblib 模型后即可开始反演。">
            <button class="btn btn-sm btn-primary" @click="openImport">导入模型</button>
          </EmptyState>

          <div v-if="selectedModel" class="hint-box" style="margin-top:12px">
            <app-icon name="info" :size="14"/>
            <span>{{ selectedModel.description }}</span>
          </div>
        </section>
      </div>

      <aside class="inversion-col inversion-side">
        <section class="card run-card run-panel">
          <div class="card-title"><app-icon name="play" :size="16"/>③ 执行反演</div>
          <p class="run-sub">当前影像与模型将执行整图逐块反演，无效像元不参与统计。</p>

          <div class="summary-box">
            <div class="sb-row"><span>影像</span><strong>{{ image ? image.name : '未选择' }}</strong></div>
            <div class="sb-row"><span>模型</span><strong>{{ selectedModel ? selectedModel.name : '未选择' }}</strong></div>
            <div class="sb-row"><span>波段</span><strong>{{ image ? image.bands : '—' }}</strong></div>
            <div class="sb-row"><span>适用性</span><strong v-if="selectedCompatibility">{{ cardStatusMeta(selectedModel || {}).label }}</strong><strong v-else>检查中…</strong></div>
          </div>

          <div class="form-grid" style="grid-template-columns:1fr 1fr;gap:8px;margin-top:12px">
            <div class="field">
              <label style="color:#b5cdbb;font-size:11px">低氮阈值 &lt;</label>
              <input class="input sm" type="number" step="0.5" v-model.number="low" style="background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.15);color:#f0f8f1">
            </div>
            <div class="field">
              <label style="color:#b5cdbb;font-size:11px">高氮阈值 &gt;</label>
              <input class="input sm" type="number" step="0.5" v-model.number="high" style="background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.15);color:#f0f8f1">
            </div>
          </div>
          <label class="check-row" style="margin-top:9px;color:#c5dac9;font-size:11.5px">
            <input type="checkbox" v-model="autoPreprocess"> 若未预处理，自动执行默认预处理
          </label>

          <div class="hint-box warn" style="margin-top:10px;background:rgba(253,244,227,.1);border-color:rgba(217,154,43,.25);color:#f1d9a8" v-for="w in warnings" :key="w">
            <app-icon name="alert-triangle" :size="13"/><span>{{ w }}</span>
          </div>
          <div class="hint-box" style="margin-top:10px;background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.14);color:#d9e8dc" v-if="selectedCompatibility">
            <app-icon name="info" :size="13"/><span>{{ selectedCompatibility.recommendation }}</span>
          </div>
          <div class="hint-box danger" style="margin-top:8px" v-if="compatibilityError">{{ compatibilityError }}</div>

          <button class="btn btn-primary btn-block btn-lg" style="margin-top:13px" @click="runSingle" :disabled="!canRun">
            <span v-if="running && jobMode === 'single'" class="spinner"></span>
            <app-icon v-else name="play" :size="16"/>
            {{ running && jobMode === 'single' ? '正在反演…' : '一键反演' }}
          </button>
          <button class="btn btn-ghost btn-block btn-sm" style="margin-top:7px" @click="openBatch" :disabled="running || !images.length">
            <app-icon name="layers" :size="14"/>批量反演（多期 / 多田块）
          </button>
          <div v-if="startError" class="hint-box danger" style="margin-top:10px;background:rgba(192,57,43,.15);border-color:rgba(192,57,43,.3);color:#ffd9d4">{{ startError }}</div>

          <div class="run-status" v-if="activeJob">
            <div class="progress-track"><div class="progress-fill" :style="{ width: jobPercent + '%' }"></div></div>
            <div class="progress-meta"><span>{{ activeJob.stage || '处理中' }} · {{ jobMode === 'batch' ? '批量' : '单幅' }}</span><span>{{ jobPercent }}%</span></div>
            <div class="tiny" style="color:#a9c6b0;margin-top:4px">{{ activeJob.message }}</div>
            <div class="hint-box danger" style="margin-top:8px" v-if="activeJob.status === 'failed'">{{ activeJob.error }}</div>
            <div class="summary-box" style="margin-top:9px" v-if="activeJob.status === 'completed'">
              <div class="sb-row" v-if="jobSingleSummary"><span>反演均值</span><strong>{{ formatNumber(jobSingleSummary.stats.mean, 2) }} {{ jobSingleSummary.unit || selectedUnit }}</strong></div>
              <div class="sb-row" v-if="jobSingleSummary"><span>变异系数</span><strong>{{ formatNumber(jobSingleSummary.stats.cv, 1) }}%</strong></div>
              <div class="sb-row" v-if="jobBatchSummary"><span>成功 / 总数</span><strong>{{ jobBatchSummary.results.length }} / {{ jobBatchSummary.total }}</strong></div>
              <div class="sb-row" v-if="jobBatchSummary && jobBatchSummary.errors.length"><span>失败</span><strong>{{ jobBatchSummary.errors.length }} 幅</strong></div>
            </div>
            <button class="btn btn-ghost btn-block btn-sm" style="margin-top:8px;background:rgba(255,255,255,.08);border-color:rgba(255,255,255,.18);color:#e4f2e6" v-if="activeJob.status === 'completed'" @click="goResults">
              查看结果 <app-icon name="arrow-right" :size="14"/>
            </button>
          </div>
        </section>
      </aside>
    </div>

    <section class="card" style="margin-top:16px" v-if="jobsList.length">
      <div class="card-head">
        <div><div class="card-title"><app-icon name="activity" :size="16"/>最近任务</div><div class="card-sub">本次运行期间提交的预处理与反演任务</div></div>
        <button class="btn btn-sm btn-ghost" @click="loadJobs" :disabled="jobsLoading"><app-icon name="refresh" :size="14"/>刷新</button>
      </div>
      <div class="job-list">
        <div v-for="job in jobsList.slice(0, 8)" :key="job.id" class="job-row" :class="job.status">
          <span class="ji"><app-icon :name="job.type === 'batch_inversion' ? 'layers' : (job.type === 'preprocess' ? 'sliders' : 'cpu')" :size="15"/></span>
          <div class="jm">
            <div class="jn">{{ job.label || jobTypeLabel(job.type) }}</div>
            <div class="js">
              {{ jobTypeLabel(job.type) }} · {{ jobStatusMeta(job.status).label }}
              <template v-if="job.error"> · {{ job.error }}</template>
              <template v-if="job.elapsed !== undefined && job.elapsed !== null"> · {{ job.elapsed }}s</template>
            </div>
          </div>
          <div class="jp" v-if="job.status === 'running' || job.status === 'queued'">
            <div class="progress-track"><div class="progress-fill" :style="{ width: (job.progress || 0) + '%' }"></div></div>
            <div class="progress-meta"><span>{{ job.stage }}</span><span>{{ job.progress || 0 }}%</span></div>
          </div>
          <button v-else-if="job.status === 'completed' && job.type !== 'preprocess'" class="btn btn-sm btn-ghost" @click="goJobResults(job)">查看结果</button>
          <span v-else class="badge" :class="jobStatusMeta(job.status).cls">{{ jobStatusMeta(job.status).label }}</span>
        </div>
      </div>
    </section>

    <Modal :open="batchOpen" title="批量反演" subtitle="多幅影像将按顺序排队处理" icon="layers" wide @close="batchOpen = false">
      <div class="row" style="justify-content:space-between;margin-bottom:8px">
        <div class="small muted">已选择 <b style="color:var(--ink)">{{ batchIds.length }}</b> / {{ images.length }} 幅影像</div>
        <div class="row" style="gap:4px">
          <button class="btn btn-sm btn-text" @click="selectAllBatch">全选</button>
          <button class="btn btn-sm btn-text" @click="clearBatch">清空</button>
        </div>
      </div>
      <div class="batch-list">
        <label v-for="im in images" :key="im.id" class="batch-row" style="cursor:pointer">
          <input type="checkbox" :checked="batchIds.includes(im.id)" @change="toggleBatch(im.id)">
          <img :src="im.thumb_url" alt="" style="width:38px;height:38px;border-radius:6px;object-fit:cover">
          <div class="br-main"><div class="br-name">{{ im.name }}</div><div class="br-meta">{{ im.bands }} 波段 · {{ dateFieldLabel(im) }} {{ im.flight_date || '未填' }} · {{ im.has_processed ? '已预处理' : '将自动预处理' }}</div></div>
        </label>
      </div>
      <div class="card-sub" style="margin:14px 0 6px">逐影像服务端预检</div>
      <div v-if="batchCompatibilityLoading" class="loading-line"><span class="spinner"></span>正在计算兼容性…</div>
      <div v-else-if="batchCompatibility" class="table-wrap">
        <table class="data-table">
          <thead><tr><th>影像</th><th>波段</th><th>状态</th><th>精确 / 插值 / 填充</th><th>确认信息</th></tr></thead>
          <tbody>
            <tr v-for="row in batchCompatibility.images" :key="row.image_id">
              <td>{{ row.image_name }}</td>
              <td class="mono">{{ row.bands }}</td>
              <td>
                <span class="tag" :class="row.assessment.status === 'compatible' ? 'green' : (row.assessment.status === 'warning' ? 'amber' : 'red')">
                  {{ row.assessment.status === 'compatible' ? '兼容' : (row.assessment.status === 'warning' ? '需确认' : '不兼容') }}
                </span>
              </td>
              <td class="mono">{{ row.assessment.exact_bands }} / {{ row.assessment.interpolated_bands }} / {{ row.assessment.edge_filled_bands }}</td>
              <td class="small">{{ [...(row.assessment.errors || []), ...(row.assessment.warnings || [])].join('；') || '无' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="hint-box" style="margin-top:10px">
        <app-icon name="cpu" :size="14"/>
        <span>当前模型：<b>{{ selectedModel ? selectedModel.name : '未选择' }}</b> · 阈值 {{ low }} / {{ high }} {{ selectedUnit }}</span>
      </div>
      <div v-if="batchError" class="hint-box danger" style="margin-top:8px">{{ batchError }}</div>
      <template #footer>
        <button class="btn btn-ghost" @click="batchOpen = false">取消</button>
        <button class="btn btn-primary" @click="runBatch" :disabled="running || !batchIds.length || !selectedModel || batchCompatibilityLoading || (batchCompatibility && !batchCompatibility.can_run)">
          <span v-if="running" class="spinner light"></span>{{ batchCompatibility && batchCompatibility.requires_confirmation ? '确认后批量反演' : '开始批量反演' }}
        </button>
      </template>
    </Modal>

    <Modal :open="compatConfirmOpen" title="确认忽略适用性警告" subtitle="服务端仍会保留硬性阻断；此确认将写入结果追溯信息" icon="alert-triangle" wide @close="compatConfirmOpen = false">
      <div class="hint-box warn" v-for="message in ((pendingRunMode === 'batch' ? (batchCompatibility?.images || []).flatMap(row => row.assessment.warnings || []) : (selectedCompatibility?.warnings || [])))" :key="message">
        <app-icon name="alert-triangle" :size="14"/><span>{{ message }}</span>
      </div>
      <div class="hint-box" style="margin-top:10px">
        <app-icon name="info" :size="14"/>
        <span>{{ pendingRunMode === 'batch' ? '批量预检中存在需确认项。继续后每幅影像仍会在执行时重新校验。' : (selectedCompatibility?.recommendation || '建议更换为推荐模型。') }} 合成模型结果仅用于流程演示，不代表真实田间标定精度。</span>
      </div>
      <template #footer>
        <button class="btn btn-ghost" @click="compatConfirmOpen = false">返回重新选择</button>
        <button class="btn btn-primary" @click="confirmCompatibilityRun">确认并继续</button>
      </template>
    </Modal>

    <Modal :open="importOpen" title="导入自定义模型" subtitle="支持 .pkl / .joblib（scikit-learn）与 .onnx" icon="file-plus" wide @close="importOpen = false">
      <div class="form-grid">
        <div class="field">
          <label>模型文件 <span class="required">*</span></label>
          <input ref="importInput" type="file" accept=".pkl,.joblib,.onnx" hidden @change="onModelFile">
          <button class="btn btn-ghost" @click="chooseModelFile"><app-icon name="file-plus" :size="15"/>选择模型文件</button>
          <div class="help-text" v-if="importFile">{{ importFile.name }} · {{ (importFile.size / 1024).toFixed(1) }} KB</div>
          <div class="help-text" v-else>模型需接受形状为 (N, 波段数) 的光谱矩阵</div>
        </div>
        <div class="field">
          <label>模型名称 <span class="required">*</span></label>
          <input class="input" v-model="importForm.name" placeholder="例如：田块A 玉米 PLSR 模型">
        </div>
        <div class="field">
          <label>算法类型</label>
          <select class="select" v-model="importForm.algorithm">
            <option>PLSR</option><option>SVR</option><option>随机森林</option><option>XGBoost</option><option>ONNX</option><option>其他</option>
          </select>
        </div>
        <div class="field">
          <label>单位</label><input class="input" v-model="importForm.unit" placeholder="mg/g">
        </div>
        <div class="field"><label>低氮阈值 &lt;</label><input class="input" type="number" step="0.1" v-model="importForm.low"></div>
        <div class="field"><label>高氮阈值 &gt;</label><input class="input" type="number" step="0.1" v-model="importForm.high"></div>
        <div class="field full">
          <label>训练波段中心波长 <span class="required">*</span> <span class="unit">（nm，逗号分隔，数量需与模型输入特征数一致）</span></label>
          <textarea class="textarea" v-model="importForm.wavelengths" rows="2"></textarea>
        </div>
        <div class="field">
          <label>适用作物</label><input class="input" v-model="importForm.crops" placeholder="玉米, 小麦">
        </div>
        <div class="field">
          <label>适用传感器</label><input class="input" v-model="importForm.sensors" placeholder="多光谱, 高光谱">
        </div>
        <div class="field"><label>R²</label><input class="input" type="number" step="0.001" min="0" max="1" v-model="importForm.r2" placeholder="例如 0.86"></div>
        <div class="field"><label>RMSE</label><input class="input" type="number" step="0.01" v-model="importForm.rmse" placeholder="例如 2.1"></div>
        <div class="field"><label>训练数据说明</label><input class="input" v-model="importForm.training_data" placeholder="实测数据 / 模拟数据"></div>
        <div class="field"><label>版本</label><input class="input" v-model="importForm.version" placeholder="1.0"></div>
        <div class="field full"><label>模型说明</label><textarea class="textarea" v-model="importForm.description" rows="2" placeholder="训练方法、适用范围、注意事项"></textarea></div>
      </div>
      <div class="hint-box" style="margin-top:10px">
        <app-icon name="info" :size="14"/>
        <span>导入时平台会用模拟光谱执行一次试推理。反演时自动将影像波段对齐到填写的训练波长。ONNX 支持：{{ store.onnxAvailable ? '已安装 onnxruntime' : '当前环境未安装 onnxruntime' }}。</span>
      </div>
      <div class="hint-box warn" style="margin-top:8px" v-if="!String(importForm.unit || '').trim()">
        <app-icon name="alert-triangle" :size="14"/><span>未填写输出单位。模型可导入，但结果单位和阈值会标记为“待确认”。</span>
      </div>
      <label class="check-row" style="margin-top:10px;font-size:11.5px" v-if="importNeedsTrust">
        <input type="checkbox" v-model="importForm.trusted_source">
        我确认该 .pkl / .joblib 模型来自可信来源；pickle 文件可能执行本机代码
      </label>
      <div v-if="importedTrial" class="hint-box" style="margin-top:8px;border-color:#9ecfad;background:#eef8f0">
        <app-icon name="check-circle" :size="14"/>
        <div>
          <div class="strong">试推理通过 · 哈希 {{ (importedTrial.file_sha256 || '').slice(0, 16) }}</div>
          <div class="small">输入 ({{ importedTrial.samples }}, {{ importedTrial.feature_count }}) → 输出 {{ importedTrial.output_shape }}，范围 {{ importedTrial.min }}–{{ importedTrial.max }} {{ importedTrial.unit || importForm.unit }}</div>
        </div>
      </div>
      <div v-if="importError" class="hint-box danger" style="margin-top:8px">{{ importError }}</div>
      <template #footer>
        <button class="btn btn-ghost" @click="importOpen = false">取消</button>
        <button class="btn btn-primary" @click="submitImport" :disabled="importing">
          <span v-if="importing" class="spinner light"></span>导入模型
        </button>
      </template>
    </Modal>
  </div>`,
};
