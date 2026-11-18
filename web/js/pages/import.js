/* 数据导入页 */
import { api } from '../api.js';
import { store, go, refreshProject, notify, projectSteps } from '../store.js';
import {
  formatFileSize, formatDate, parseNumberList,
  imageStatusMeta,
} from '../utils.js';
import { AppIcon } from '../components/icons.js';
import { EmptyState, Modal } from '../components/ui.js';

const { ref, reactive, computed, watch } = window.Vue;

const ALLOWED_EXT = ['.tif', '.tiff', '.dat', '.img', '.bil', '.bsq', '.hdr', '.json', '.xml', '.xmp'];
const ENVI_DATA_EXT = ['.dat', '.img', '.bil', '.bsq'];
const SIDECAR_EXT = ['.json', '.xml', '.xmp'];
const SOURCE_LABELS = {
  envi_header: 'ENVI 头文件',
  geotiff_tags: 'GeoTIFF 标签',
  band_tags: '波段标签',
  xmp_sidecar: 'XMP 侧车',
  aux_xml: 'AUX.XML',
  json_sidecar: 'JSON 侧车',
  xml_sidecar: 'XML 侧车',
  filename_rule: '文件名推断',
  user: '用户已修改',
  default: '默认值',
};

function extOf(name) {
  const i = String(name).lastIndexOf('.');
  return i >= 0 ? String(name).slice(i).toLowerCase() : '';
}

export const ImportPage = {
  name: 'ImportPage',
  components: { AppIcon, EmptyState, Modal },
  setup() {
    const fileInput = ref(null);
    const files = ref([]);
    const dragging = ref(false);
    const uploading = ref(false);
    const uploadPct = ref(0);
    const uploadErrors = ref([]);
    const saving = ref(false);
    const editorError = ref('');
    const savedHint = ref(false);
    const reparseOpen = ref(false);
    const reparseData = ref(null);
    const reparsing = ref(false);

    const meta = reactive({
      sensor: '',
      flight_date: '',
      field: '',
      note: '',
    });
    const editor = reactive({
      sensor: '',
      flight_date: '',
      field: '',
      note: '',
      wavelengthsText: '',
      bandwidthsText: '',
    });

    const images = computed(() => store.project?.images || []);
    const activeImage = computed(() => images.value.find((im) => im.id === store.activeImageId) || images.value[0] || null);
    const steps = computed(() => projectSteps());

    const enviIssues = computed(() => {
      const names = files.value.map((f) => f.name.toLowerCase());
      const issues = new Set();
      const hdrStems = new Set(names.filter((n) => n.endsWith('.hdr')).map((n) => n.slice(0, -4)));
      names.forEach((n) => {
        const m = n.match(/^(.*)\.(dat|img|bil|bsq)$/);
        if (m && !hdrStems.has(m[1]) && !hdrStems.has(n)) issues.add(`缺少 ENVI 头文件：${m[1]}.hdr`);
      });
      names.filter((n) => n.endsWith('.hdr')).forEach((n) => {
        const stem = n.slice(0, -4);
        const baseStem = ENVI_DATA_EXT.some((ext) => stem.endsWith(ext)) ? stem.slice(0, -4) : stem;
        if (!ENVI_DATA_EXT.some((ext) => names.includes(baseStem + ext))) {
          issues.add(`头文件 ${stem}.hdr 未找到配对的数据文件`);
        }
      });
      return Array.from(issues);
    });
    const metadataWarnings = computed(() => {
      const im = activeImage.value;
      if (!im) return [];
      const conflicts = (im.metadata_conflicts || []).map((c) => typeof c === 'string' ? c : (c.message || `${c.field || '元数据'}存在来源冲突`));
      return Array.from(new Set([...(im.metadata_warnings || []), ...conflicts]));
    });

    const bandRows = computed(() => {
      const im = activeImage.value;
      if (!im) return [];
      const count = Number(im.bands) || 0;
      const wl = im.wavelengths || [];
      const bw = im.bandwidths || [];
      const desc = im.descriptions || [];
      const names = im.band_names || [];
      const rows = [];
      for (let i = 0; i < count; i++) {
        rows.push({
          index: i + 1,
          wavelength: wl.length === count && wl[i] !== null && wl[i] !== undefined ? `${Math.round(Number(wl[i]) * 10) / 10} nm` : '—',
          bandwidth: bw.length === count && bw[i] !== null && bw[i] !== undefined ? `${bw[i]} nm` : '—',
          name: names[i] || `Band ${i + 1}`,
          description: desc[i] || '',
        });
      }
      return rows;
    });

    const parsedWavelengths = computed(() => {
      const im = activeImage.value;
      return !!im && Array.isArray(im.wavelengths) && im.wavelengths.length === Number(im.bands);
    });
    const metadataFieldRows = computed(() => {
      const im = activeImage.value;
      if (!im) return [];
      const fields = [
        ['sensor_name', '传感器', im.sensor_name || im.sensor],
        ['acquisition_datetime', '采集时间（原始）', im.acquisition_datetime],
        ['flight_date', dateLabel(im), im.flight_date],
        ['field_id', '田块 / 地点', im.field_id || im.field],
        ['image_type', '影像类型', im.image_type],
        ['value_type', '值类型', im.value_type],
        ['processing_level', '处理级别', im.processing_level],
        ['wavelengths', '中心波长', im.wavelengths],
        ['bandwidths', '带宽（FWHM）', im.bandwidths],
      ];
      return fields.map(([key, label, value]) => ({
        key,
        label,
        value,
        source: sourceOf(key, im),
      }));
    });

    function sourceOf(field, im = activeImage.value) {
      if (!im) return '';
      return (im.metadata_sources && im.metadata_sources[field])
        || ((im.metadata || {}).metadata_sources || {})[field]
        || '';
    }
    function sourceLabel(source) {
      if (!source) return '待人工确认';
      return SOURCE_LABELS[source] || source;
    }
    function sourceClass(source) {
      return source === 'user' ? 'green' : (source === 'filename_rule' ? 'amber' : (source ? 'blue' : 'gray'));
    }
    function dateLabel(im = activeImage.value) {
      const text = `${im?.sensor || ''} ${im?.sensor_name || ''} ${im?.image_type || ''}`.toLowerCase();
      return /satellite|卫星|planetscope|sentinel|landsat/.test(text) ? '采集日期' : '飞行日期';
    }

    function fillEditor(im) {
      if (!im) return;
      editor.sensor = im.sensor || '';
      editor.flight_date = im.flight_date || '';
      editor.field = im.field || '';
      editor.note = im.note || '';
      editor.wavelengthsText = parsedWavelengths.value ? (im.wavelengths || []).join(', ') : '';
      editor.bandwidthsText = Array.isArray(im.bandwidths) && im.bandwidths.length === Number(im.bands)
        ? im.bandwidths.map((v) => (v === null ? '' : v)).filter((v) => v !== '').join(', ')
        : '';
      editorError.value = '';
      savedHint.value = false;
    }

    watch(() => activeImage.value && activeImage.value.id, () => fillEditor(activeImage.value), { immediate: true });
    watch(() => store.project && store.project.id, () => {
      if (store.project) meta.field = store.project.field || '';
    });

    function addFiles(list) {
      const incoming = Array.from(list || []);
      const seen = new Set(files.value.map((f) => `${f.name}|${f.size}`));
      const next = [...files.value];
      let ignored = 0;
      incoming.forEach((f) => {
        if (!ALLOWED_EXT.includes(extOf(f.name))) { ignored++; return; }
        const key = `${f.name}|${f.size}`;
        if (seen.has(key)) return;
        seen.add(key);
        next.push(f);
      });
      files.value = next;
      if (ignored) notify(`已忽略 ${ignored} 个不支持的文件`, 'info');
    }

    function onPick(e) {
      addFiles(e.target.files);
      e.target.value = '';
    }
    function onDrop(e) {
      dragging.value = false;
      addFiles(e.dataTransfer.files);
    }
    function openPicker() { if (fileInput.value) fileInput.value.click(); }
    function removeFile(index) { files.value = files.value.filter((_, i) => i !== index); }
    function clearFiles() { files.value = []; uploadErrors.value = []; }

    function selectImage(id) {
      store.activeImageId = id;
    }

    async function upload() {
      if (!files.value.length || uploading.value) return;
      uploading.value = true;
      uploadPct.value = 0;
      uploadErrors.value = [];
      try {
        const result = await api.uploadImages(store.project.id, files.value, meta, (p) => { uploadPct.value = p; });
        const imported = result.imported || [];
        uploadErrors.value = result.errors || [];
        if (imported.length) {
          store.activeImageId = imported[0].id;
          await refreshProject();
          notify(`成功导入 ${imported.length} 幅影像`, 'success');
          const errNames = new Set(uploadErrors.value.map((e) => e.file));
          files.value = files.value.filter((f) => errNames.has(f.name));
        }
        if (uploadErrors.value.length) {
          notify(`${uploadErrors.value.length} 个文件未导入，请查看提示`, 'error');
        }
      } catch (err) {
        uploadErrors.value = [{ file: '', message: err.message }];
        notify(err.message, 'error');
      } finally {
        uploading.value = false;
        uploadPct.value = 0;
      }
    }

    async function saveMeta() {
      if (!activeImage.value) return;
      editorError.value = '';
      const im = activeImage.value;
      const payload = {
        sensor: editor.sensor.trim(),
        flight_date: editor.flight_date,
        field: editor.field.trim(),
        note: editor.note.trim(),
      };
      const wl = parseNumberList(editor.wavelengthsText);
      if (editor.wavelengthsText.trim()) {
        if (wl.length !== Number(im.bands)) {
          editorError.value = `中心波长数量（${wl.length}）与波段数（${im.bands}）不一致`;
          return;
        }
        if (wl.some((v) => v < 100 || v > 20000)) {
          editorError.value = '中心波长应在 100–20000 nm 之间';
          return;
        }
        payload.wavelengths = wl;
        payload.wavelength_unit = 'nm';
      }
      if (editor.bandwidthsText.trim()) {
        const bw = parseNumberList(editor.bandwidthsText);
        if (bw.length !== Number(im.bands)) {
          editorError.value = `带宽数量（${bw.length}）与波段数（${im.bands}）不一致`;
          return;
        }
        payload.bandwidths = bw;
      }
      saving.value = true;
      try {
        await api.updateImage(store.project.id, im.id, payload);
        await refreshProject();
        savedHint.value = true;
        notify('影像元数据已保存', 'success');
        setTimeout(() => { savedHint.value = false; }, 2600);
      } catch (err) {
        editorError.value = err.message;
        notify(err.message, 'error');
      } finally {
        saving.value = false;
      }
    }

    async function openReparse() {
      if (!activeImage.value || reparsing.value) return;
      reparsing.value = true;
      editorError.value = '';
      try {
        reparseData.value = await api.reparseImageMetadata(store.project.id, activeImage.value.id, false);
        reparseOpen.value = true;
      } catch (err) {
        editorError.value = err.message;
        notify(err.message, 'error');
      } finally {
        reparsing.value = false;
      }
    }

    async function applyReparse() {
      if (!activeImage.value || reparsing.value) return;
      reparsing.value = true;
      try {
        const result = await api.reparseImageMetadata(store.project.id, activeImage.value.id, true);
        await refreshProject();
        fillEditor(activeImage.value);
        reparseOpen.value = false;
        notify(`头文件重新解析完成，保留用户字段 ${result.preserved_user_fields?.length || 0} 个`, 'success');
      } catch (err) {
        notify(err.message, 'error');
      } finally {
        reparsing.value = false;
      }
    }

    function metadataValue(value) {
      if (Array.isArray(value)) return value.length > 12 ? `${value.slice(0, 12).join(', ')} …（${value.length} 项）` : value.join(', ');
      if (value === null || value === undefined || value === '') return '待人工确认';
      return String(value);
    }

    function goNext() {
      if (!images.value.length) {
        notify('请先导入至少一幅影像', 'info');
        return;
      }
      go('viewer');
    }

    return {
      store, go, fileInput, files, dragging, uploading, uploadPct, uploadErrors, saving, editorError, savedHint,
      reparseOpen, reparseData, reparsing, metadataWarnings, metadataFieldRows,
      meta, editor, images, activeImage, steps, enviIssues, bandRows, parsedWavelengths,
      onPick, onDrop, openPicker, removeFile, clearFiles, selectImage, upload, saveMeta,
      openReparse, applyReparse, sourceOf, sourceLabel, sourceClass, dateLabel, metadataValue, goNext,
      formatFileSize, formatDate, imageStatusMeta, extOf, SIDECAR_EXT,
    };
  },
  template: `
  <div class="page import-page">
    <div class="page-head">
      <div class="ph-copy">
        <div class="eyebrow">DATA INPUT</div>
        <h1>数据导入</h1>
        <p class="ph-sub">支持 GeoTIFF 与 ENVI 多光谱/高光谱影像。可一次选择多幅影像，平台自动解析波段中心波长与带宽。</p>
      </div>
      <div class="ph-actions">
        <button class="btn btn-ghost" @click="go('viewer')" :disabled="!images.length"><app-icon name="image" :size="16"/>影像查看</button>
        <button class="btn btn-primary" @click="goNext" :disabled="!images.length"><app-icon name="arrow-right" :size="16"/>下一步：预处理</button>
      </div>
    </div>

    <div class="import-layout">
      <div class="import-main">
        <section class="card">
          <div class="card-head">
            <div class="step-title">
              <span class="step-num" :class="{ done: files.length }">{{ files.length ? '✓' : '1' }}</span>
              <div><div class="card-title">选择影像文件</div><div class="card-sub">可多选；ENVI 请同时选择 .hdr 与数据文件</div></div>
            </div>
            <span class="tag" v-if="files.length">{{ files.length }} 个文件</span>
          </div>

          <div class="dropzone" :class="{ dragging }" @click="openPicker" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
            <input ref="fileInput" type="file" multiple hidden accept=".tif,.tiff,.dat,.img,.bil,.bsq,.hdr,.json,.xml,.xmp" @change="onPick">
            <div class="dz-icon"><app-icon name="upload-cloud" :size="23"/></div>
            <h3>拖拽影像到此处，或点击选择</h3>
            <p>GeoTIFF（.tif / .tiff） · ENVI（.hdr + .dat / .img） · 可同时选择 JSON/XML/XMP 伴随元数据</p>
            <div class="dz-formats">
              <span class="tag green">GeoTIFF</span>
              <span class="tag green">ENVI</span>
              <span class="tag">批量导入</span>
              <span class="tag">自动解析波长</span>
              <span class="tag">侧车元数据</span>
            </div>
          </div>

          <ul class="file-list" v-if="files.length">
            <li v-for="(f, i) in files" :key="f.name + f.size" class="file-row">
              <div class="file-icon"><app-icon :name="(extOf(f.name) === '.hdr' || SIDECAR_EXT.includes(extOf(f.name))) ? 'file-text' : 'image'" :size="16"/></div>
              <div class="file-main">
                <div class="file-name">{{ f.name }}</div>
                <div class="file-meta">{{ formatFileSize(f.size) }} · {{ extOf(f.name).replace('.', '').toUpperCase() }}</div>
              </div>
              <span class="badge gray">待导入</span>
              <button class="icon-btn row-remove" @click.stop="removeFile(i)" title="移除"><app-icon name="x" :size="15"/></button>
            </li>
          </ul>

          <div class="hint-box warn" style="margin-top:12px" v-if="enviIssues.length">
            <app-icon name="alert-triangle" :size="15"/>
            <div><div class="strong">ENVI 文件配对检查</div><div v-for="msg in enviIssues" :key="msg">{{ msg }}</div></div>
          </div>

          <div class="batch-meta" v-if="files.length">
            <div class="form-grid four">
              <div class="field">
                <label>传感器 <span class="unit">（可选）</span></label>
                <input class="input sm" v-model="meta.sensor" placeholder="例如：MicaSense RedEdge-P">
              </div>
              <div class="field">
                <label>飞行 / 采集日期 <span class="unit">（留空则自动识别）</span></label>
                <input class="input sm" type="date" v-model="meta.flight_date">
              </div>
              <div class="field">
                <label>田块 / 地点 <span class="unit">（留空则自动识别）</span></label>
                <input class="input sm" v-model="meta.field" :placeholder="store.project?.field || '例如：A区-03'">
              </div>
              <div class="field">
                <label>备注</label>
                <input class="input sm" v-model="meta.note" placeholder="架次 / 天气等">
              </div>
            </div>
            <div class="row" style="justify-content:flex-end;margin-top:12px">
              <button class="btn btn-ghost btn-sm" @click="clearFiles" :disabled="uploading">清空</button>
              <button class="btn btn-primary btn-sm" @click="upload" :disabled="uploading">
                <span v-if="uploading" class="spinner light"></span>
                <app-icon v-else name="upload" :size="15"/>
                {{ uploading ? '上传中 ' + uploadPct + '%' : '导入到项目' }}
              </button>
            </div>
            <div class="upload-progress" v-if="uploading">
              <div class="progress-track"><div class="progress-fill" :style="{ width: uploadPct + '%' }"></div></div>
              <div class="progress-meta"><span>正在上传影像文件…</span><span>{{ uploadPct }}%</span></div>
            </div>
          </div>

          <div v-if="uploadErrors.length" class="hint-box danger" style="margin-top:12px;flex-direction:column;align-items:stretch">
            <div class="row" style="margin-bottom:4px"><app-icon name="alert-circle" :size="15"/><span class="strong">以下文件未导入</span></div>
            <div v-for="(e, i) in uploadErrors" :key="i" class="small" style="padding:2px 0">
              <b v-if="e.file">{{ e.file }}：</b>{{ e.message }}
            </div>
          </div>
        </section>

        <section class="card" v-if="images.length">
          <div class="card-head">
            <div class="step-title">
              <span class="step-num done">✓</span>
              <div><div class="card-title">元数据确认与数据备注</div><div class="card-sub">自动识别头文件、GeoTIFF、JSON/XML/XMP 和文件名；用户修改优先保留</div></div>
            </div>
            <button class="btn btn-sm btn-ghost" @click="openReparse" :disabled="reparsing">
              <span v-if="reparsing" class="spinner"></span>
              <app-icon v-else name="refresh" :size="14"/>
              重新解析头文件
            </button>
          </div>

          <div class="compare-selector" style="margin-bottom:12px">
            <button v-for="im in images" :key="im.id" class="compare-chip" :class="{ active: im.id === (activeImage && activeImage.id) }" @click="selectImage(im.id)">
              <span class="dot"></span>{{ im.name }}
            </button>
          </div>

          <div v-if="activeImage">
            <div class="row wrap" style="margin-bottom:12px">
              <span class="badge blue">{{ activeImage.format }}</span>
              <span class="badge gray">{{ activeImage.bands }} 个波段</span>
              <span class="badge gray">{{ activeImage.width }} × {{ activeImage.height }}</span>
              <span class="badge" :class="parsedWavelengths ? 'green' : 'amber'">
                <i class="dot"></i>{{ parsedWavelengths ? '已解析中心波长' : '缺少中心波长' }}
              </span>
              <span class="tag">{{ activeImage.crs || '无坐标系' }}</span>
            </div>

            <div class="form-grid four" style="margin-bottom:12px">
              <div class="detail-stat"><span class="k">传感器</span><span class="v">{{ activeImage.sensor || activeImage.sensor_name || '待人工确认' }}</span><span class="badge" :class="sourceClass(sourceOf('sensor_name'))">{{ sourceLabel(sourceOf('sensor_name')) }}</span></div>
              <div class="detail-stat"><span class="k">{{ dateLabel(activeImage) }}</span><span class="v">{{ activeImage.flight_date || '待人工确认' }}</span><span class="badge" :class="sourceClass(sourceOf('flight_date'))">{{ sourceLabel(sourceOf('flight_date')) }}</span></div>
              <div class="detail-stat"><span class="k">田块 / 地点</span><span class="v">{{ activeImage.field || activeImage.field_id || '待人工确认' }}</span><span class="badge" :class="sourceClass(sourceOf('field_id'))">{{ sourceLabel(sourceOf('field_id')) }}</span></div>
              <div class="detail-stat"><span class="k">值类型 / 处理级别</span><span class="v">{{ activeImage.value_type || '—' }} / {{ activeImage.processing_level || '—' }}</span></div>
            </div>

            <details style="margin-bottom:12px">
              <summary class="table-toggle"><app-icon name="database" :size="14"/>字段来源与识别状态</summary>
              <div class="table-wrap" style="margin-top:8px">
                <table class="data-table">
                  <thead><tr><th>字段</th><th>当前值</th><th>来源 / 状态</th><th>置信度</th></tr></thead>
                  <tbody>
                    <tr v-for="row in metadataFieldRows" :key="row.key">
                      <td>{{ row.label }}</td>
                      <td>{{ metadataValue(row.value) }}</td>
                      <td><span class="badge" :class="sourceClass(row.source)">{{ sourceLabel(row.source) }}</span></td>
                      <td class="mono">{{ row.source ? (((activeImage.metadata_confidence || {})[row.key] || 0) * 100).toFixed(0) + '%' : '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>

            <div class="hint-box warn" v-for="message in metadataWarnings" :key="message" style="margin-bottom:10px">
              <app-icon name="alert-triangle" :size="15"/><span>{{ message }}</span>
            </div>

            <div class="hint-box warn" v-if="!parsedWavelengths" style="margin-bottom:12px">
              <app-icon name="alert-triangle" :size="15"/>
              <span>未从文件头解析到中心波长。请在下方按波段顺序填写（单位 nm，逗号分隔），否则光谱横轴只能使用波段序号，且波段对齐/植被指数可能无法计算。</span>
            </div>

            <div class="form-grid">
              <div class="field"><label>传感器 <span class="badge" :class="sourceClass(sourceOf('sensor_name'))">{{ sourceLabel(sourceOf('sensor_name')) }}</span></label><input class="input" v-model="editor.sensor" placeholder="例如：Headwall Nano-Hyperspec"></div>
              <div class="field"><label>{{ dateLabel(activeImage) }} <span class="badge" :class="sourceClass(sourceOf('flight_date'))">{{ sourceLabel(sourceOf('flight_date')) }}</span></label><input class="input" type="date" v-model="editor.flight_date"></div>
              <div class="field"><label>田块 / 地点 <span class="badge" :class="sourceClass(sourceOf('field_id'))">{{ sourceLabel(sourceOf('field_id')) }}</span></label><input class="input" v-model="editor.field" placeholder="例如：B区-01"></div>
              <div class="field"><label>备注</label><input class="input" v-model="editor.note" placeholder="数据来源 / 处理说明"></div>
              <div class="field full">
                <label>波段中心波长 <span class="unit">（nm，按波段顺序，逗号分隔）</span> <span class="badge" :class="sourceClass(sourceOf('wavelengths'))">{{ sourceLabel(sourceOf('wavelengths')) }}</span></label>
                <textarea class="textarea" v-model="editor.wavelengthsText" rows="2" :placeholder="'例如：475, 560, 668, 717, 840（共 ' + activeImage.bands + ' 个）'"></textarea>
              </div>
              <details class="full" style="grid-column:1/-1">
                <summary class="table-toggle"><app-icon name="sliders" :size="14"/>高级：波段带宽（FWHM，可选）</summary>
                <div class="field" style="margin-top:8px">
                  <textarea class="textarea" v-model="editor.bandwidthsText" rows="2" placeholder="例如：20, 20, 20, 20, 40"></textarea>
                </div>
              </details>
            </div>

            <div class="band-table-wrap">
              <table class="band-table">
                <thead><tr><th style="width:64px">波段</th><th style="width:110px">中心波长</th><th style="width:100px">带宽</th><th>波段名称 / 描述</th></tr></thead>
                <tbody>
                  <tr v-for="b in bandRows" :key="b.index">
                    <td class="mono">B{{ b.index }}</td>
                    <td class="wavelength mono">{{ b.wavelength }}</td>
                    <td class="mono">{{ b.bandwidth }}</td>
                    <td>{{ b.name }}<span v-if="b.description && b.description !== b.name" class="muted"> · {{ b.description }}</span></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <details style="margin-top:10px">
              <summary class="table-toggle"><app-icon name="database" :size="14"/>查看原始元数据与来源</summary>
              <pre class="raw-metadata">{{ JSON.stringify(activeImage.raw_metadata || {}, null, 2) }}</pre>
            </details>

            <div v-if="editorError" class="hint-box danger" style="margin-top:11px"><app-icon name="alert-circle" :size="15"/>{{ editorError }}</div>
            <div class="row" style="justify-content:flex-end;margin-top:12px">
              <span v-if="savedHint" class="badge green" style="margin-right:6px"><i class="dot"></i>已保存</span>
              <button class="btn btn-primary btn-sm" @click="saveMeta" :disabled="saving">
                <span v-if="saving" class="spinner light"></span>
                <app-icon v-else name="save" :size="15"/>
                {{ saving ? '保存中…' : '保存元数据' }}
              </button>
            </div>
          </div>
        </section>

        <section class="card" v-if="images.length">
          <div class="card-head">
            <div><div class="card-title">已导入影像 <span class="badge gray">{{ images.length }}</span></div><div class="card-sub">点击卡片切换当前影像</div></div>
            <button class="btn btn-sm btn-ghost" @click="goNext"><app-icon name="arrow-right" :size="14"/>进入影像查看</button>
          </div>
          <div class="imported-image-grid">
            <div v-for="im in images" :key="im.id" class="imported-mini" style="cursor:pointer" :style="im.id === (activeImage && activeImage.id) ? 'border-color:var(--green-2);background:var(--green-100)' : ''" @click="selectImage(im.id)">
              <img :src="im.thumb_url" alt="" loading="lazy">
              <div style="min-width:0;flex:1">
                <div class="im-name">{{ im.name }}</div>
                <div class="im-meta">{{ im.bands }} 波段 · {{ formatDate(im.flight_date) }}</div>
                <span class="status-badge" :class="imageStatusMeta(im.status, im.has_result).cls" style="margin-top:4px;height:19px;font-size:10px">
                  <i class="dot"></i>{{ imageStatusMeta(im.status, im.has_result).label }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section class="card" v-if="!images.length">
          <EmptyState icon="image" title="项目尚无影像" text="选择文件并导入后，可在此确认波段元数据并填写数据来源备注。" />
        </section>
      </div>

      <aside class="import-side">
        <section class="card">
          <div class="card-title" style="margin-bottom:10px"><app-icon name="activity" :size="16"/>项目进度</div>
          <div class="quality-list">
            <div v-for="(s, i) in steps" :key="s.key" class="quality-item" :class="s.done ? 'ok' : 'notice'" style="background:#fff">
              <span class="qi-icon"><app-icon :name="s.done ? 'check' : 'circle'" :size="13"/></span>
              <div style="flex:1"><div class="qi-title">{{ s.label }}</div><div class="qi-msg">{{ s.done ? '已完成' : '待处理' }}</div></div>
              <span class="qi-status" :style="s.done ? '' : 'color:var(--muted-2)'">{{ s.done ? '✓' : '—' }}</span>
            </div>
          </div>
          <button class="btn btn-soft btn-block" style="margin-top:12px" @click="go('viewer')" :disabled="!images.length">
            前往影像查看 <app-icon name="arrow-right" :size="15"/>
          </button>
        </section>

        <section class="card">
          <div class="card-title" style="margin-bottom:8px"><app-icon name="info" :size="16"/>导入须知</div>
          <div class="help-text" style="display:flex;flex-direction:column;gap:8px">
            <p><b>GeoTIFF：</b>单文件直接导入，自动读取波段描述中的波长。</p>
            <p><b>ENVI：</b>必须同时选择同名 .hdr 头文件，缺少时将无法读取波长。</p>
            <p><b>波长：</b>若文件未包含元数据，请手动填写；数量必须与波段数一致。</p>
            <p><b>大影像：</b>上传与本机处理均在本机完成，首次预览生成需要几秒。</p>
          </div>
        </section>

        <section class="card" style="background:linear-gradient(155deg,#1d4a30,#153724);border-color:#1c442b;color:#e6f2e8">
          <div class="card-title" style="color:#fff"><app-icon name="shield" :size="16"/>本地数据安全</div>
          <p style="font-size:11.5px;color:#a9c6b0;margin-top:6px;line-height:1.6">影像与结果保存在项目目录中，不会上传到外网。项目可整体备份或迁移。</p>
          <div class="tiny" style="color:#8fb59a;margin-top:8px;word-break:break-all">data/projects/{{ store.project.id }}</div>
        </section>
      </aside>
    </div>

    <Modal :open="reparseOpen" title="头文件重新解析确认" subtitle="仅应用自动字段变化；用户已修改字段默认保留" icon="refresh" wide @close="reparseOpen = false">
      <div v-if="reparseData">
        <div class="row wrap" style="margin-bottom:10px">
          <span class="badge green">变更 {{ reparseData.changes.length }}</span>
          <span class="badge amber">保留用户字段 {{ reparseData.preserved_user_fields.length }}</span>
          <span class="badge gray">冲突 {{ reparseData.conflicts.length }}</span>
        </div>
        <div v-if="!reparseData.changes.length" class="hint-box">
          <app-icon name="check-circle" :size="14"/><span>重新解析结果与当前元数据一致，无需应用变更。</span>
        </div>
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead><tr><th>字段</th><th>当前值</th><th>新解析值</th><th>保留后结果</th><th>处理</th></tr></thead>
            <tbody>
              <tr v-for="item in reparseData.changes" :key="item.field">
                <td class="mono">{{ item.field }}</td>
                <td>{{ metadataValue(item.old) }}</td>
                <td>{{ metadataValue(item.incoming) }}</td>
                <td>{{ metadataValue(item.final) }}</td>
                <td><span class="badge" :class="item.preserved_user_value ? 'green' : 'amber'">{{ item.preserved_user_value ? '保留用户值' : '将更新' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="hint-box warn" v-for="message in (reparseData.metadata_warnings || [])" :key="message" style="margin-top:8px">
          <app-icon name="alert-triangle" :size="14"/><span>{{ message }}</span>
        </div>
      </div>
      <template #footer>
        <button class="btn btn-ghost" @click="reparseOpen = false">取消</button>
        <button class="btn btn-primary" @click="applyReparse" :disabled="reparsing || !reparseData || !reparseData.changes.length">
          <span v-if="reparsing" class="spinner light"></span>应用非用户字段
        </button>
      </template>
    </Modal>
  </div>`,
};
