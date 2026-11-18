/* 影像查看页 */
import { api } from '../api.js';
import { store, go, notify } from '../store.js';
import {
  formatDate, formatFileSize, resolutionText, pickViewerBands, nearestBand,
  bandLabel, bandShort, imageStatusMeta, clamp, shortName, dateFieldLabel,
} from '../utils.js';
import { AppIcon } from '../components/icons.js';
import { EmptyState } from '../components/ui.js';
import { ImageCanvas, CompareCanvas } from '../components/image-canvas.js';
import { EChart, spectrumOption, SERIES_COLORS } from '../components/charts.js';

const { ref, reactive, computed, watch, nextTick, onMounted } = window.Vue;

const MODES = [
  { key: 'rgb', label: '真彩色' },
  { key: 'false', label: '假彩色' },
  { key: 'gray', label: '单波段' },
  { key: 'pseudo', label: '伪彩色' },
  { key: 'compare', label: '对比' },
];

const CMAPS = [
  { value: 'greens', label: '绿色梯度' },
  { value: 'terrain', label: '地形色带' },
  { value: 'viridis', label: 'Viridis' },
  { value: 'nmass', label: '氮素色带' },
  { value: 'ndvi', label: '植被色带' },
];

export const ViewerPage = {
  name: 'ViewerPage',
  components: { AppIcon, EmptyState, ImageCanvas, CompareCanvas, EChart },
  setup() {
    const mode = ref('rgb');
    const trueBands = ref([3, 2, 1]);
    const falseBands = ref([5, 3, 2]);
    const band = ref(1);
    const cmap = ref('greens');
    const compareImageId = ref('');
    const pickMode = ref('point');
    const spectra = ref([]);
    const spectrumLoading = ref(false);
    const sideTab = ref('spectrum');
    const canvasRef = ref(null);
    const compareRef = ref(null);
    const MODES_LIST = MODES;
    const CMAPS_LIST = CMAPS;

    const images = computed(() => store.project?.images || []);
    const image = computed(() => images.value.find((im) => im.id === store.activeImageId) || images.value[0] || null);
    const compareImage = computed(() => images.value.find((im) => im.id === compareImageId.value) || images.value.find((im) => im.id !== image.value?.id) || image.value);
    const version = computed(() => (image.value ? (image.value.updated_at || image.value.imported_at || '') : ''));
    const statusMeta = computed(() => image.value ? imageStatusMeta(image.value.status, image.value.has_result) : { label: '', cls: '' });
    const metadataMessages = computed(() => {
      const im = image.value;
      if (!im) return [];
      const conflicts = (im.metadata_conflicts || []).map((c) => typeof c === 'string' ? c : (c.message || `${c.field || '元数据'}存在来源冲突`));
      return Array.from(new Set([...(im.metadata_warnings || []), ...conflicts]));
    });

    const bandOptions = computed(() => {
      const im = image.value;
      if (!im) return [];
      const wl = Array.isArray(im.wavelengths) && im.wavelengths.length === Number(im.bands) ? im.wavelengths : [];
      return Array.from({ length: Number(im.bands) || 0 }, (_, i) => ({
        value: i + 1,
        label: wl[i] !== undefined && wl[i] !== null ? `B${i + 1} · ${Math.round(Number(wl[i]))}nm` : `B${i + 1}`,
      }));
    });

    const bandRows = computed(() => {
      const im = image.value;
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

    const hasWavelengths = computed(() => {
      const im = image.value;
      return !!im && Array.isArray(im.wavelengths) && im.wavelengths.length === Number(im.bands);
    });

    function normalizeBands(arr) {
      const im = image.value;
      if (!im) return [1, 1, 1];
      const n = Number(im.bands) || 1;
      return arr.map((v) => Math.min(Math.max(Number(v) || 1, 1), n));
    }

    watch(() => image.value && image.value.id, () => {
      const im = image.value;
      if (!im) return;
      trueBands.value = pickViewerBands(im, 'rgb');
      falseBands.value = pickViewerBands(im, 'false');
      band.value = 1;
      spectra.value = [];
      compareImageId.value = (images.value.find((x) => x.id !== im.id) || im).id;
      nextTick(() => { if (canvasRef.value) canvasRef.value.clearMarks(); });
    }, { immediate: true });

    function activeBandSet() {
      return mode.value === 'false' ? falseBands.value : trueBands.value;
    }
    function bandValue(slot) {
      return activeBandSet()[slot];
    }
    function setBandValue(slot, value) {
      const arr = activeBandSet();
      arr[slot] = Math.min(Math.max(Number(value) || 1, 1), Number(image.value?.bands) || 1);
    }

    const previewUrl = computed(() => {
      const im = image.value;
      if (!im) return '';
      const pid = store.project.id;
      const params = { size: 1600, v: version.value };
      if (mode.value === 'gray') return api.previewUrl(pid, im.id, { ...params, mode: 'gray', band: band.value });
      if (mode.value === 'pseudo') return api.previewUrl(pid, im.id, { ...params, mode: 'pseudo', band: band.value, cmap: cmap.value });
      const bands = normalizeBands(mode.value === 'false' ? falseBands.value : trueBands.value);
      return api.previewUrl(pid, im.id, { ...params, mode: 'rgb', bands: bands.join(',') });
    });

    function compareUrl(im) {
      if (!im) return '';
      const bands = pickViewerBands(im, 'false');
      return api.previewUrl(store.project.id, im.id, { mode: 'rgb', bands: bands.join(','), size: 1400, v: im.updated_at || im.imported_at || '' });
    }
    const compareUrlA = computed(() => compareUrl(image.value));
    const compareUrlB = computed(() => compareUrl(compareImage.value));

    function selectImage(event) {
      store.activeImageId = event.target.value;
    }
    function setMode(key) {
      if (key === 'compare' && images.value.length < 2) {
        notify('卷帘对比至少需要 2 幅影像，请先在数据导入页添加另一期影像', 'info');
        return;
      }
      mode.value = key;
      if (key === 'compare') {
        const other = images.value.find((im) => im.id !== image.value?.id);
        if (other) compareImageId.value = compareImage.value?.id === image.value?.id ? other.id : compareImageId.value;
      }
      nextTick(() => { if (canvasRef.value) canvasRef.value.clearMarks(); });
    }
    function setPickMode(key) {
      pickMode.value = key;
      nextTick(() => { if (canvasRef.value) canvasRef.value.clearMarks(); });
    }
    function zoomIn() {
      const target = mode.value === 'compare' ? compareRef.value : canvasRef.value;
      if (target) target.zoomIn();
    }
    function zoomOut() {
      const target = mode.value === 'compare' ? compareRef.value : canvasRef.value;
      if (target) target.zoomOut();
    }
    function resetView() {
      const target = mode.value === 'compare' ? compareRef.value : canvasRef.value;
      if (target) target.reset();
    }

    function nextColor() {
      const used = new Set(spectra.value.map((s) => s.color));
      return SERIES_COLORS.find((c) => !used.has(c)) || SERIES_COLORS[0];
    }

    async function addSpectrum(item, kindLabel) {
      if (spectra.value.length >= 5) {
        notify('最多同时对比 5 条光谱，请先移除一条', 'info');
        return;
      }
      const entry = {
        ...item,
        color: nextColor(),
        label: item.label || kindLabel,
      };
      spectra.value = [...spectra.value, entry];
      sideTab.value = 'spectrum';
    }

    async function queryPoint(pt) {
      if (!image.value) return;
      if (spectra.value.length >= 5 && pickMode.value === 'multi') {
        notify('最多同时对比 5 条光谱，请先移除一条', 'info');
        return;
      }
      spectrumLoading.value = true;
      try {
        const item = await api.spectrumPoint(store.project.id, image.value.id, pt.x, pt.y);
        item.label = `P${spectra.value.length + 1} (${pt.x}, ${pt.y})`;
        if (pickMode.value === 'multi') {
          await addSpectrum(item);
        } else {
          spectra.value = [{ ...item, color: SERIES_COLORS[0] }];
          sideTab.value = 'spectrum';
        }
      } catch (err) {
        notify(err.message, 'error');
      } finally {
        spectrumLoading.value = false;
      }
    }

    async function queryRegion(rect) {
      if (!image.value) return;
      if (spectra.value.length >= 5) {
        notify('最多同时对比 5 条光谱，请先移除一条', 'info');
        return;
      }
      spectrumLoading.value = true;
      try {
        const result = await api.spectraBatch(store.project.id, image.value.id, {
          points: [],
          regions: [{ x: rect.x, y: rect.y, w: rect.w, h: rect.h, shape: 'box', label: `区域${spectra.value.length + 1} (${rect.w}×${rect.h})` }],
        });
        const item = result.spectra && result.spectra[0];
        if (item) await addSpectrum(item, '区域平均光谱');
      } catch (err) {
        notify(err.message, 'error');
      } finally {
        spectrumLoading.value = false;
      }
    }

    onMounted(() => {
      const params = new URLSearchParams(location.search);
      const x = Number(params.get('point_x'));
      const y = Number(params.get('point_y'));
      if (Number.isFinite(x) && Number.isFinite(y) && image.value) {
        queryPoint({ x: Math.max(0, Math.round(x)), y: Math.max(0, Math.round(y)) });
      }
    });

    function onPixel(pt) {
      if (pickMode.value === 'region') return;
      queryPoint(pt);
    }
    function onRegion(rect) {
      queryRegion(rect);
    }
    function removeSpectrum(index) {
      spectra.value = spectra.value.filter((_, i) => i !== index);
    }
    function clearSpectra() {
      spectra.value = [];
      if (canvasRef.value) canvasRef.value.clearMarks();
    }

    const spectrumUnit = computed(() => {
      if (!spectra.value.length) return hasWavelengths.value ? 'nm' : 'band';
      return spectra.value[0].unit || (hasWavelengths.value ? 'nm' : 'band');
    });
    const spectrumSeries = computed(() => spectra.value.map((s) => ({
      label: s.label,
      wavelengths: s.wavelengths,
      values: s.values,
      color: s.color,
    })));
    const chartOption = computed(() => spectrumOption(spectrumSeries.value, spectrumUnit.value));

    const spectrumTable = computed(() => {
      const list = spectra.value;
      if (!list.length) return { series: [], rows: [] };
      const len = Math.max(...list.map((s) => (s.values || []).length));
      const wl = list[0].wavelengths || [];
      const rows = [];
      for (let i = 0; i < len; i++) {
        rows.push({
          x: wl[i] !== undefined ? wl[i] : i + 1,
          values: list.map((s) => (s.values || [])[i]),
        });
      }
      return { series: list, rows };
    });

    const canvasHint = computed(() => {
      if (mode.value === 'compare') return '拖动中轴卷帘对比 · 滚轮缩放 · 拖动漫游';
      if (pickMode.value === 'region') return '按住拖拽框选区域，计算区域平均光谱';
      if (pickMode.value === 'multi') return '点击添加光谱曲线，最多 5 条';
      return '滚轮缩放 · 拖动漫游 · 点击查询像素光谱';
    });

    const lastSpectrum = computed(() => spectra.value[spectra.value.length - 1] || null);

    function formatCell(value) {
      return value === null || value === undefined || Number.isNaN(Number(value)) ? '—' : Number(value).toFixed(3);
    }
    function spectrumKind(s) {
      return s.kind === 'region' ? `区域均值 · ${s.count || 0} 像素` : '单像素';
    }

    return {
      store, go, mode, trueBands, falseBands, band, cmap, compareImageId, pickMode,
      spectra, spectrumLoading, sideTab, canvasRef, compareRef, MODES_LIST, CMAPS_LIST,
      images, image, compareImage, statusMeta, metadataMessages, bandOptions, bandRows, hasWavelengths,
      previewUrl, compareUrlA, compareUrlB, selectImage, setMode, setPickMode,
      bandValue, setBandValue,
      zoomIn, zoomOut, resetView, onPixel, onRegion, removeSpectrum, clearSpectra,
      spectrumSeries, chartOption, spectrumUnit, spectrumTable, canvasHint,
      lastSpectrum, formatCell, spectrumKind,
      formatDate, formatFileSize, resolutionText, bandShort, imageStatusMeta, shortName, dateFieldLabel,
    };
  },
  template: `
  <div class="page viewer-page">
    <div class="page-head">
      <div class="ph-copy">
        <div class="eyebrow">IMAGE EXPLORER</div>
        <h1>影像查看</h1>
        <p class="ph-sub">真/假彩色合成、单波段与伪彩色浏览；点击像素查询光谱曲线，框选计算区域平均光谱。</p>
      </div>
      <div class="ph-actions">
        <select class="select" style="width:320px" :value="image && image.id" @change="selectImage" v-if="images.length">
          <option v-for="im in images" :key="im.id" :value="im.id">{{ shortName(im.name, 30) }}</option>
        </select>
        <button class="btn btn-primary" @click="go('preprocess')" :disabled="!image"><app-icon name="arrow-right" :size="16"/>下一步：预处理</button>
      </div>
    </div>

    <template v-if="image">
      <div class="viewer-toolbar card">
        <div class="segmented">
          <button v-for="m in MODES_LIST" :key="m.key" class="seg-btn" :class="{ active: mode === m.key }" :disabled="m.key === 'compare' && images.length < 2" :title="m.key === 'compare' && images.length < 2 ? '至少需要 2 幅影像才能卷帘对比' : m.label" @click="setMode(m.key)">{{ m.label }}</button>
        </div>

        <div class="tb-group" v-if="mode === 'rgb' || mode === 'false'">
          <span class="tb-label">{{ mode === 'rgb' ? 'RGB 通道' : '假彩色通道' }}</span>
          <div class="band-pick"><span class="ch r">R</span><select class="select" :value="bandValue(0)" @change="setBandValue(0, $event.target.value)"><option v-for="b in bandOptions" :key="b.value" :value="b.value">{{ b.label }}</option></select></div>
          <div class="band-pick"><span class="ch g">G</span><select class="select" :value="bandValue(1)" @change="setBandValue(1, $event.target.value)"><option v-for="b in bandOptions" :key="b.value" :value="b.value">{{ b.label }}</option></select></div>
          <div class="band-pick"><span class="ch b">B</span><select class="select" :value="bandValue(2)" @change="setBandValue(2, $event.target.value)"><option v-for="b in bandOptions" :key="b.value" :value="b.value">{{ b.label }}</option></select></div>
        </div>

        <div class="tb-group" v-else-if="mode === 'gray' || mode === 'pseudo'">
          <span class="tb-label">波段</span>
          <select class="select" style="width:150px" v-model.number="band">
            <option v-for="b in bandOptions" :key="b.value" :value="b.value">{{ b.label }}</option>
          </select>
          <template v-if="mode === 'pseudo'">
            <span class="tb-label">色带</span>
            <select class="select" style="width:120px" v-model="cmap">
              <option v-for="c in CMAPS_LIST" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </template>
        </div>

        <div class="tb-group" v-else-if="mode === 'compare'">
          <span class="tb-label">对比影像</span>
          <select class="select" style="width:250px" v-model="compareImageId">
            <option v-for="im in images" :key="im.id" :value="im.id" :disabled="im.id === image.id">{{ shortName(im.name, 28) }}</option>
          </select>
          <span class="tag" v-if="images.length < 2">至少需要 2 幅影像才能对比</span>
        </div>

        <span class="spacer"></span>
        <span class="pill">{{ image.width }} × {{ image.height }} px</span>
        <span class="pill">{{ image.bands }} 波段</span>
      </div>

      <div class="viewer-grid">
        <div class="canvas-panel">
          <ImageCanvas v-if="mode !== 'compare'" ref="canvasRef" :src="previewUrl" :natural-width="image.width" :natural-height="image.height"
            :mode="pickMode === 'region' ? 'region' : 'point'" @pixel="onPixel" @region="onRegion" />
          <CompareCanvas v-else ref="compareRef" :src-a="compareUrlA" :src-b="compareUrlB"
            :label-a="image.name" :label-b="compareImage ? compareImage.name : '请选择对比影像'" />
          <div class="canvas-actions">
            <button class="icon-btn" @click="zoomIn" title="放大"><app-icon name="zoom-in" :size="16"/></button>
            <button class="icon-btn" @click="zoomOut" title="缩小"><app-icon name="zoom-out" :size="16"/></button>
            <button class="icon-btn" @click="resetView" title="重置视图"><app-icon name="maximize" :size="16"/></button>
          </div>
          <div class="canvas-badge" v-if="mode !== 'compare'">
            <span v-if="spectra.length">已选 {{ spectra.length }} 条光谱<template v-if="lastSpectrum"> · {{ lastSpectrum.label }}</template></span>
            <span v-else>{{ pickMode === 'region' ? '框选模式' : (pickMode === 'multi' ? '多点对比模式' : '像素查询模式') }}</span>
          </div>
          <div class="canvas-hint">{{ canvasHint }}</div>
        </div>

        <aside class="viewer-side card">
          <div class="tabs">
            <button class="tab-btn" :class="{ active: sideTab === 'spectrum' }" @click="sideTab = 'spectrum'">光谱曲线</button>
            <button class="tab-btn" :class="{ active: sideTab === 'bands' }" @click="sideTab = 'bands'">波段信息</button>
            <button class="tab-btn" :class="{ active: sideTab === 'info' }" @click="sideTab = 'info'">影像信息</button>
          </div>
          <div class="side-body">
            <template v-if="sideTab === 'spectrum'">
              <div class="spectrum-toolbar" v-if="mode !== 'compare'">
                <div class="segmented sm">
                  <button class="seg-btn" :class="{ active: pickMode === 'point' }" @click="setPickMode('point')">单点</button>
                  <button class="seg-btn" :class="{ active: pickMode === 'region' }" @click="setPickMode('region')">框选</button>
                  <button class="seg-btn" :class="{ active: pickMode === 'multi' }" @click="setPickMode('multi')">多点</button>
                </div>
                <button class="btn btn-sm btn-text" @click="clearSpectra" :disabled="!spectra.length">清空</button>
              </div>

              <div v-if="mode === 'compare'" class="spectrum-empty" style="min-height:200px">
                <app-icon name="columns" :size="28"/>
                <div class="strong">当前为对比模式</div>
                <p class="small muted">拖动画布中轴可卷帘查看两期影像；切换回其他模式即可查询光谱。</p>
              </div>
              <template v-else>
                <div v-if="spectrumLoading" class="loading-line" style="padding:10px 0"><span class="spinner"></span>正在提取光谱…</div>
                <div v-if="spectra.length" class="spectrum-chart">
                  <EChart :option="chartOption" dark/>
                </div>
                <div v-else-if="!spectrumLoading" class="spectrum-empty">
                  <app-icon name="mouse-pointer" :size="30"/>
                  <div class="strong">在影像上点击查询</div>
                  <p class="small muted">单点模式查询像素光谱；框选模式计算区域平均光谱；多点模式最多叠加 5 条曲线。</p>
                </div>

                <div class="spectrum-legend" v-if="spectra.length">
                  <span v-for="(s, i) in spectra" :key="i" class="spectrum-chip">
                    <i :style="{ background: s.color }"></i>{{ s.label }}
                    <button @click="removeSpectrum(i)" title="移除"><app-icon name="x" :size="11"/></button>
                  </span>
                </div>

                <div class="tiny muted" style="margin-top:8px" v-if="spectra.length && spectrumUnit === 'band'">
                  未提供波段中心波长，横轴使用波段序号。
                </div>
                <div class="tiny muted" style="margin-top:8px" v-if="spectra.length && lastSpectrum && lastSpectrum.kind === 'region'">
                  区域平均光谱：{{ lastSpectrum.width }}×{{ lastSpectrum.height }} 像元，有效像素 {{ lastSpectrum.count }} 个。
                </div>

                <details class="data-details" v-if="spectra.length">
                  <summary><app-icon name="table" :size="12"/>查看数据表</summary>
                  <div class="dd-body">
                    <table class="data-table">
                      <thead><tr><th>{{ spectrumUnit === 'nm' ? '波长 (nm)' : '波段' }}</th><th v-for="s in spectrumTable.series" :key="s.label">{{ s.label }}</th></tr></thead>
                      <tbody>
                        <tr v-for="(row, i) in spectrumTable.rows" :key="i">
                          <td class="mono">{{ row.x }}</td>
                          <td v-for="(v, j) in row.values" :key="j" class="mono">{{ formatCell(v) }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </details>
              </template>
            </template>

            <template v-else-if="sideTab === 'bands'">
              <div class="hint-box warn" style="margin-bottom:10px" v-if="!hasWavelengths">
                <app-icon name="alert-triangle" :size="14"/>
                <span>该影像未提供波段中心波长，光谱横轴使用波段序号。可前往「数据导入」补充元数据。</span>
              </div>
              <div class="band-table-wrap" style="max-height:calc(100vh - 360px)">
                <table class="band-table">
                  <thead><tr><th>波段</th><th>波长</th><th>带宽</th><th>名称</th></tr></thead>
                  <tbody>
                    <tr v-for="b in bandRows" :key="b.index">
                      <td class="mono">B{{ b.index }}</td>
                      <td class="wavelength mono">{{ b.wavelength }}</td>
                      <td class="mono">{{ b.bandwidth }}</td>
                      <td>{{ b.name }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>

            <template v-else>
              <div class="info-list">
                <div class="info-row"><span class="k">影像名称</span><span class="v">{{ image.name }}</span></div>
                <div class="info-row"><span class="k">文件格式</span><span class="v">{{ image.format }} · {{ (image.file || '').split('.').pop().toUpperCase() }}</span></div>
                <div class="info-row"><span class="k">传感器</span><span class="v">{{ image.sensor || '—' }}</span></div>
                <div class="info-row" v-if="image.instrument"><span class="k">仪器</span><span class="v">{{ image.instrument }}</span></div>
                <div class="info-row" v-if="image.scene_id"><span class="k">Scene ID</span><span class="v mono" style="font-size:10px">{{ image.scene_id }}</span></div>
                <div class="info-row"><span class="k">{{ dateFieldLabel(image) }}</span><span class="v">{{ formatDate(image.flight_date) }}</span></div>
                <div class="info-row" v-if="image.acquisition_datetime"><span class="k">采集时间（原始）</span><span class="v mono" style="font-size:10px">{{ image.acquisition_datetime }}</span></div>
                <div class="info-row" v-if="image.planet_scope?.xml?.sun_elevation"><span class="k">太阳高度 / 方位</span><span class="v mono">{{ Number(image.planet_scope.xml.sun_elevation).toFixed(2) }}° / {{ Number(image.planet_scope.xml.sun_azimuth).toFixed(2) }}°</span></div>
                <div class="info-row" v-if="image.planet_scope?.xml?.off_nadir"><span class="k">侧摆角</span><span class="v mono">{{ Number(image.planet_scope.xml.off_nadir).toFixed(2) }}°</span></div>
                <div class="info-row"><span class="k">田块 / 地点</span><span class="v">{{ image.field || '—' }}</span></div>
                <div class="info-row"><span class="k">行列数</span><span class="v mono">{{ image.width }} × {{ image.height }}</span></div>
                <div class="info-row"><span class="k">波段数</span><span class="v mono">{{ image.bands }}</span></div>
                <div class="info-row"><span class="k">空间分辨率</span><span class="v mono">{{ resolutionText(image) }}</span></div>
                <div class="info-row"><span class="k">数据类型</span><span class="v mono">{{ image.dtype }}</span></div>
                <div class="info-row" v-if="image.value_type"><span class="k">值类型 / 级别</span><span class="v">{{ image.value_type }} / {{ image.processing_level || '—' }}</span></div>
                <div class="info-row"><span class="k">坐标系</span><span class="v">{{ image.crs || '—' }}</span></div>
                <div class="info-row"><span class="k">无效值</span><span class="v mono">{{ image.nodata === null || image.nodata === undefined ? '未设置' : image.nodata }}</span></div>
                <div class="info-row"><span class="k">文件大小</span><span class="v mono">{{ formatFileSize(image.file_size) }}</span></div>
                <div class="info-row"><span class="k">导入时间</span><span class="v">{{ formatDate(image.imported_at, true) }}</span></div>
                <div class="info-row" v-if="image.note"><span class="k">备注</span><span class="v">{{ image.note }}</span></div>
              </div>
              <div class="hint-box" style="margin-top:12px">
                <app-icon name="info" :size="14"/><span>波段波长可在「数据导入」页修改；修改后需重新执行预处理。</span>
              </div>
              <div class="hint-box warn" style="margin-top:8px" v-for="message in metadataMessages" :key="message">
                <app-icon name="alert-triangle" :size="14"/><span>{{ message }}</span>
              </div>
            </template>
          </div>
        </aside>
      </div>
    </template>

    <div v-else class="card">
      <EmptyState icon="image" title="项目还没有影像" text="先导入 GeoTIFF 或 ENVI 影像，再回来查看真彩色、光谱曲线与多期对比。">
        <button class="btn btn-primary" @click="go('import')"><app-icon name="upload" :size="16"/>去导入影像</button>
      </EmptyState>
    </div>
  </div>`,
};
