/* 结果分析页 */
import { api } from '../api.js';
import { store, go, notify, refreshProject, loadModels, pollJob } from '../store.js';
import {
  formatDate, formatNumber, pct, percentValue, downloadUrl, shortName,
  dateFieldLabel,
} from '../utils.js';
import { AppIcon } from '../components/icons.js';
import { EmptyState, Modal } from '../components/ui.js';
import { ImageCanvas } from '../components/image-canvas.js';
import { EChart, histogramOption, trendOption } from '../components/charts.js';

const { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } = window.Vue;

const TABS = [
  { key: 'map', label: '氮素专题图' },
  { key: 'histogram', label: '直方图' },
  { key: 'stats', label: '统计指标' },
  { key: 'compare', label: '多期对比' },
];

export const ResultsPage = {
  name: 'ResultsPage',
  components: { AppIcon, EmptyState, ImageCanvas, EChart, Modal },
  setup() {
    const loading = ref(true);
    const results = ref([]);
    const history = ref([]);
    const selectedId = ref('');
    const tab = ref('map');
    const mapMode = ref('classified');
    const valueInfo = ref(null);
    const valueLoading = ref(false);
    const mapRef = ref(null);
    const exportBusy = ref('');
    const compareIds = ref([]);
    const fewShotOpen = ref(false);
    const fewShotFileInput = ref(null);
    const fewShotFileName = ref('');
    const fewShotRows = ref([]);
    const fewShotActiveRow = ref(0);
    const fewShotCoordinateMode = ref('pixel');
    const fewShotUnit = ref('');
    const fewShotName = ref('');
    const fewShotPreview = ref(null);
    const fewShotError = ref('');
    const fewShotBusy = ref('');
    const fewShotJob = ref(null);
    const fewShotProgress = ref(0);
    const fewShotForce = ref(false);
    let stopFewShotPolling = null;
    const TABS_LIST = TABS;

    const resultOptions = computed(() => results.value);
    const selectedResult = computed(() => results.value.find((r) => r.image_id === selectedId.value) || results.value[0] || null);
    const imageMeta = computed(() => {
      if (!selectedResult.value) return null;
      return (store.project?.images || []).find((im) => im.id === selectedResult.value.image_id) || null;
    });
    const stats = computed(() => selectedResult.value?.stats || {});
    const classStats = computed(() => selectedResult.value?.class_stats || []);
    const classes = computed(() => {
      const base = selectedResult.value?.classes || classStats.value || [];
      return base.map((c) => {
        const st = classStats.value.find((s) => s.key === c.key) || {};
        return {
          ...st,
          ...c,
          ratio: st.ratio !== undefined ? st.ratio : (c.ratio || 0),
          area_mu: st.area_mu !== undefined ? st.area_mu : (c.area_mu || 0),
          range: c.range || st.range,
        };
      });
    });
    const thresholds = computed(() => selectedResult.value?.thresholds || { low: 22, high: 28 });
    const area = computed(() => selectedResult.value?.area || {});
    const model = computed(() => selectedResult.value?.model || {});
    const unit = computed(() => String(selectedResult.value?.unit || model.value.unit || '待确认'));
    const trace = computed(() => selectedResult.value?.trace || {});
    const alignmentTrace = computed(() => trace.value.alignment || {});
    const preprocessTrace = computed(() => trace.value.preprocessing || {});
    const fewShotTrace = computed(() => trace.value.model?.few_shot || model.value.few_shot || null);
    const demoModel = computed(() => !!(model.value.demo || model.value.synthetic || model.value.dataset?.synthetic || trace.value.model?.synthetic));
    const displayRange = computed(() => selectedResult.value?.display_range || { min: 0, max: 1 });
    const mapUrl = computed(() => {
      if (!selectedResult.value) return '';
      return mapMode.value === 'continuous'
        ? selectedResult.value.preview_continuous_url
        : selectedResult.value.preview_classified_url;
    });
    const lowClass = computed(() => classStats.value.find((c) => c.key === 'low'));
    const suitableClass = computed(() => classStats.value.find((c) => c.key === 'suitable'));
    const highClass = computed(() => classStats.value.find((c) => c.key === 'high'));
    const mapWidth = computed(() => selectedResult.value?.width || 1);
    const mapHeight = computed(() => selectedResult.value?.height || 1);

    const histogramRows = computed(() => {
      const hist = selectedResult.value?.histogram || {};
      const edges = hist.edges || [];
      const counts = hist.counts || [];
      const total = counts.reduce((a, b) => a + b, 0) || 1;
      return counts.map((count, i) => ({
        range: `${formatNumber(edges[i], 1)} – ${formatNumber(edges[i + 1], 1)}`,
        count,
        ratio: count / total,
      }));
    });
    const chartOption = computed(() => histogramOption(selectedResult.value));
    const compareResults = computed(() => results.value.filter((r) => compareIds.value.includes(r.image_id)));
    const comparePeriods = computed(() => [...compareResults.value].sort((a, b) => {
      const da = a.flight_date || a.created_at || '';
      const db = b.flight_date || b.created_at || '';
      return String(da).localeCompare(String(db));
    }));
    const compareOption = computed(() => trendOption(comparePeriods.value));
    const compareRows = computed(() => comparePeriods.value.map((r) => {
      const cs = r.class_stats || [];
      const ratio = (key) => {
        const item = cs.find((c) => c.key === key);
        return item ? pct(item.ratio, 1) : '—';
      };
      return {
        id: r.image_id,
        date: r.flight_date || '未填日期',
        name: r.image_name,
        mean: r.stats?.mean,
        std: r.stats?.std,
        cv: r.stats?.cv,
        unit: r.unit || r.model?.unit || '待确认',
        low: ratio('low'),
        suitable: ratio('suitable'),
        high: ratio('high'),
        model: r.model?.name || '—',
      };
    }));
    const thresholdTicks = computed(() => {
      const min = Number(displayRange.value.min);
      const max = Number(displayRange.value.max);
      if (!(max > min)) return [];
      return [thresholds.value.low, thresholds.value.high]
        .filter((v) => v > min && v < max)
        .map((v) => ({ value: v, left: `${((v - min) / (max - min)) * 100}%` }));
    });
    const fewShotCalibration = computed(() => fewShotPreview.value?.calibration || {});
    const fewShotCanApply = computed(() => {
      return !!fewShotPreview.value
        && Number(fewShotPreview.value.sample_count || 0) >= 1
        && !fewShotBusy.value;
    });
    const fewShotFormula = computed(() => {
      const c = fewShotCalibration.value;
      if (!fewShotPreview.value) return '';
      const slope = formatNumber(c.slope, 4);
      return `校正值 = ${slope} × 父模型值 ${Number(c.intercept) >= 0 ? '+' : '−'} ${Math.abs(Number(c.intercept)).toFixed(4)}`;
    });

    async function loadResults() {
      loading.value = true;
      try {
        const data = await api.projectResults(store.project.id);
        results.value = data.results || [];
        history.value = data.history || [];
        if (results.value.length) {
          const active = results.value.find((r) => r.image_id === store.activeImageId);
          selectedId.value = (active || results.value[0]).image_id;
          compareIds.value = results.value.map((r) => r.image_id);
        } else {
          selectedId.value = '';
          compareIds.value = [];
        }
      } catch (err) {
        notify(err.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    onMounted(async () => {
      await loadResults();
      const requestedTab = new URLSearchParams(location.search).get('tab');
      if (TABS.some((item) => item.key === requestedTab) && results.value.length) {
        tab.value = requestedTab;
      }
      if (new URLSearchParams(location.search).get('fewshot') === '1' && results.value.length) {
        await nextTick();
        openFewShot();
      }
    });
    onBeforeUnmount(() => {
      if (stopFewShotPolling) stopFewShotPolling();
    });

    watch(selectedId, () => {
      valueInfo.value = null;
      fewShotPreview.value = null;
      fewShotError.value = '';
      nextTick(() => { if (mapRef.value) mapRef.value.clearMarks(); });
    });
    watch(mapMode, () => {
      valueInfo.value = null;
      nextTick(() => { if (mapRef.value) mapRef.value.clearMarks(); });
    });
    watch(fewShotRows, () => {
      if (!fewShotBusy.value) fewShotPreview.value = null;
    }, { deep: true });
    watch(fewShotUnit, () => {
      if (!fewShotBusy.value) fewShotPreview.value = null;
    });

    function selectResult(e) {
      const id = e.target.value;
      selectedId.value = id;
      const found = results.value.find((r) => r.image_id === id);
      if (found) store.activeImageId = found.image_id;
    }
    function selectTab(key) {
      tab.value = key;
      if (key === 'map') nextTick(() => { if (mapRef.value) mapRef.value.clearMarks(); });
    }
    function newFewShotRow(index = 0) {
      return {
        _key: `fs_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        sample_id: `sample_${index + 1}`,
        col: '',
        row: '',
        lon: '',
        lat: '',
        nmass: '',
      };
    }
    function resetFewShotRows() {
      fewShotRows.value = [0, 1, 2].map((i) => newFewShotRow(i));
      fewShotActiveRow.value = 0;
      fewShotFileName.value = '';
      fewShotPreview.value = null;
      fewShotError.value = '';
      fewShotJob.value = null;
      fewShotProgress.value = 0;
      fewShotForce.value = false;
    }
    function openFewShot() {
      if (!selectedResult.value || fewShotBusy.value) return;
      if (!fewShotRows.value.length) resetFewShotRows();
      fewShotUnit.value = unit.value;
      fewShotName.value = `${model.value.name || '当前模型'} · 实测校正`;
      fewShotError.value = '';
      fewShotPreview.value = null;
      fewShotOpen.value = true;
    }
    function closeFewShot() {
      if (fewShotBusy.value === 'apply') return;
      fewShotOpen.value = false;
      fewShotError.value = '';
      fewShotPreview.value = null;
    }
    function addFewShotRow() {
      const row = newFewShotRow(fewShotRows.value.length);
      fewShotRows.value = [...fewShotRows.value, row];
      fewShotActiveRow.value = fewShotRows.value.length - 1;
    }
    function removeFewShotRow(index) {
      fewShotRows.value = fewShotRows.value.filter((_, i) => i !== index);
      if (!fewShotRows.value.length) addFewShotRow();
      fewShotActiveRow.value = Math.max(0, Math.min(fewShotActiveRow.value, fewShotRows.value.length - 1));
      fewShotPreview.value = null;
    }
    function fillFromCurrentPixel(index) {
      if (!valueInfo.value || valueInfo.value.x === undefined || valueInfo.value.y === undefined) {
        notify('请先在结果专题图中点击一个有效像元', 'info');
        return;
      }
      const row = { ...fewShotRows.value[index] };
      row.col = valueInfo.value.x;
      row.row = valueInfo.value.y;
      fewShotRows.value = fewShotRows.value.map((item, i) => (i === index ? row : item));
      fewShotActiveRow.value = index;
    }
    function splitCsvLine(line) {
      const delimiter = line.includes('\t') && !line.includes(',') ? '\t' : ',';
      const cells = [];
      let value = '';
      let quoted = false;
      for (let i = 0; i < line.length; i += 1) {
        const ch = line[i];
        if (ch === '"') {
          if (quoted && line[i + 1] === '"') {
            value += '"';
            i += 1;
          } else {
            quoted = !quoted;
          }
        } else if (ch === delimiter && !quoted) {
          cells.push(value.trim());
          value = '';
        } else {
          value += ch;
        }
      }
      cells.push(value.trim());
      return cells;
    }
    function normalizedHeader(value) {
      return String(value || '')
        .replace(/^\uFEFF/, '')
        .toLowerCase()
        .replace(/[\s_\-（）()]/g, '');
    }
    function parseCsvSamples(text) {
      const lines = String(text || '')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      if (!lines.length) throw new Error('CSV 文件为空');
      const parsed = lines.map(splitCsvLine);
      const aliases = {
        sample_id: ['sampleid', '编号', '样本编号'],
        col: ['col', 'column', '列', '列号', '像元列'],
        row: ['row', '行', '行号', '像元行'],
        lon: ['lon', 'lng', 'longitude', '经度'],
        lat: ['lat', 'latitude', '纬度'],
        nmass: ['nmass', 'n', '氮素', '实测氮素', '实测值', 'measured'],
      };
      const first = parsed[0].map(normalizedHeader);
      const headerIndex = {};
      Object.entries(aliases).forEach(([key, names]) => {
        headerIndex[key] = first.findIndex((value) => names.includes(value));
      });
      const hasPixelHeader = headerIndex.col >= 0 && headerIndex.row >= 0 && headerIndex.nmass >= 0;
      const hasLonLatHeader = headerIndex.lon >= 0 && headerIndex.lat >= 0 && headerIndex.nmass >= 0;
      const hasHeader = hasPixelHeader || hasLonLatHeader;
      if (!hasHeader && (headerIndex.nmass >= 0 || headerIndex.col >= 0 || headerIndex.lon >= 0)) {
        throw new Error('CSV 表头需包含 col,row,nmass 或 lon,lat,nmass');
      }
      const coordinateMode = hasLonLatHeader && !hasPixelHeader ? 'lonlat' : 'pixel';
      const dataLines = hasHeader ? parsed.slice(1) : parsed;
      if (!dataLines.length) throw new Error('CSV 中没有样点数据');
      if (dataLines.length > 200) throw new Error('一次最多导入 200 个样点');
      const rows = dataLines.map((cells, index) => {
        const offset = !hasHeader && cells.length >= 4 ? 1 : 0;
        const get = (key, positional) => {
          if (hasHeader) return headerIndex[key] >= 0 ? cells[headerIndex[key]] : '';
          if (key === 'sample_id') return offset ? cells[0] : `sample_${index + 1}`;
          return cells[positional + offset];
        };
        const longitudes = get('lon', 0);
        const latitudes = get('lat', 1);
        return {
          _key: `fs_csv_${Date.now()}_${index}`,
          sample_id: String(get('sample_id', 0) || `sample_${index + 1}`).trim(),
          col: String(get('col', 0) || '').trim(),
          row: String(get('row', 1) || '').trim(),
          lon: String(longitudes || '').trim(),
          lat: String(latitudes || '').trim(),
          nmass: String(get('nmass', 2) || '').trim(),
        };
      });
      return { rows, coordinateMode };
    }
    async function onFewShotFile(event) {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        const parsed = parseCsvSamples(await file.text());
        fewShotCoordinateMode.value = parsed.coordinateMode;
        fewShotRows.value = parsed.rows;
        fewShotFileName.value = file.name;
        fewShotPreview.value = null;
        fewShotError.value = '';
        fewShotActiveRow.value = 0;
        notify(`已读取 ${parsed.rows.length} 个${parsed.coordinateMode === 'lonlat' ? '经纬度' : '像元'}样点，请先预览校正`, 'success');
      } catch (err) {
        fewShotError.value = err.message;
      } finally {
        event.target.value = '';
      }
    }
    function downloadFewShotTemplate() {
      if (!selectedResult.value) return;
      downloadUrl(api.fewShotTemplateUrl(
        store.project.id,
        selectedResult.value.image_id,
        fewShotCoordinateMode.value,
      ));
    }
    function setFewShotCoordinateMode(mode) {
      if (fewShotCoordinateMode.value === mode) return;
      fewShotCoordinateMode.value = mode;
      fewShotRows.value = [0, 1, 2].map((i) => newFewShotRow(i));
      fewShotActiveRow.value = 0;
      fewShotPreview.value = null;
      fewShotError.value = '';
      fewShotFileName.value = '';
    }
    function collectFewShotSamples() {
      const samples = [];
      fewShotRows.value.forEach((row, index) => {
        const coordinateValues = fewShotCoordinateMode.value === 'lonlat'
          ? [row.lon, row.lat]
          : [row.col, row.row];
        const hasAny = [...coordinateValues, row.nmass].some((v) => String(v ?? '').trim() !== '');
        if (!hasAny) return;
        const sampleId = String(row.sample_id || '').trim() || `sample_${index + 1}`;
        const nmass = Number(row.nmass);
        if (!Number.isFinite(nmass) || nmass <= 0) throw new Error(`${sampleId}：实测 Nmass 必须是有限正数`);
        if (fewShotCoordinateMode.value === 'lonlat') {
          const lon = Number(row.lon);
          const lat = Number(row.lat);
          if (!Number.isFinite(lon) || lon < -180 || lon > 180) throw new Error(`${sampleId}：经度 lon 必须位于 -180–180`);
          if (!Number.isFinite(lat) || lat < -90 || lat > 90) throw new Error(`${sampleId}：纬度 lat 必须位于 -90–90`);
          samples.push({ sample_id: sampleId, lon, lat, nmass });
        } else {
          const col = Number(row.col);
          const rowNo = Number(row.row);
          if (!Number.isInteger(col) || col < 0) throw new Error(`${sampleId}：像元列 col 必须是非负整数`);
          if (!Number.isInteger(rowNo) || rowNo < 0) throw new Error(`${sampleId}：像元行 row 必须是非负整数`);
          samples.push({ sample_id: sampleId, col, row: rowNo, nmass });
        }
      });
      if (!samples.length) throw new Error('至少需要 1 个实测样点；建议使用至少 5 个覆盖浓度范围的样点');
      return samples;
    }
    async function previewFewShot() {
      if (!selectedResult.value || fewShotBusy.value) return;
      fewShotError.value = '';
      try {
        const samples = collectFewShotSamples();
        fewShotBusy.value = 'preview';
        const data = await api.previewFewShot(store.project.id, selectedResult.value.image_id, {
          samples,
          unit: fewShotUnit.value,
        });
        fewShotPreview.value = data;
        if (data.sample_count < 2) {
          notify('当前只有 1 个有效样点，只能做整体偏置校正，无法验证稳定性', 'info');
        } else if (data.rejected_samples && data.rejected_samples.length) {
          notify(`有 ${data.rejected_samples.length} 个样点未通过匹配，请查看预览表`, 'info');
        } else {
          notify('校正预览已生成，请确认误差后应用', 'success');
        }
      } catch (err) {
        fewShotPreview.value = null;
        fewShotError.value = err.message;
      } finally {
        fewShotBusy.value = '';
      }
    }
    async function applyFewShot() {
      if (!fewShotCanApply.value || !selectedResult.value) return;
      fewShotError.value = '';
      fewShotBusy.value = 'apply';
      fewShotProgress.value = 0;
      const targetId = selectedResult.value.image_id;
      const samples = fewShotPreview.value.samples.map((item) => ({
        sample_id: item.sample_id,
        col: item.col,
        row: item.row,
        nmass: item.nmass,
      }));
      try {
        const data = await api.applyFewShot(store.project.id, targetId, {
          samples,
          unit: fewShotUnit.value,
          name: fewShotName.value,
          force: fewShotForce.value,
        });
        fewShotJob.value = {
          status: 'queued',
          progress: 0,
          stage: '排队中',
          message: '派生模型已创建，正在准备校正反演',
        };
        if (stopFewShotPolling) stopFewShotPolling();
        stopFewShotPolling = pollJob(data.job_id, {
          onUpdate: (job) => {
            fewShotJob.value = job;
            fewShotProgress.value = Number(job.progress || 0);
          },
          onDone: async (job) => {
            fewShotJob.value = job;
            fewShotProgress.value = Number(job.progress || 0);
            if (job.status === 'completed') {
              await refreshProject();
              await loadModels();
              await loadResults();
              selectedId.value = targetId;
              store.activeImageId = targetId;
              notify('实测校正结果已生成，原始结果已保留快照', 'success', 'few-shot 完成');
              fewShotBusy.value = '';
              fewShotOpen.value = false;
            } else {
              fewShotError.value = job.error || '校正反演失败';
              fewShotBusy.value = '';
              notify(fewShotError.value, 'error');
            }
          },
        });
      } catch (err) {
        fewShotError.value = err.message;
        fewShotBusy.value = '';
      }
    }
    function zoomIn() { if (mapRef.value) mapRef.value.zoomIn(); }
    function zoomOut() { if (mapRef.value) mapRef.value.zoomOut(); }
    function resetView() { if (mapRef.value) mapRef.value.reset(); }

    async function onPixel(pt) {
      if (!selectedResult.value) return;
      valueLoading.value = true;
      try {
        const info = await api.resultValue(store.project.id, selectedResult.value.image_id, pt.x, pt.y);
        valueInfo.value = { ...info, x: pt.x, y: pt.y };
      } catch (err) {
        notify(err.message, 'error');
      } finally {
        valueLoading.value = false;
      }
    }

    function classRange(c) {
      const min = c.min !== undefined ? c.min : (c.range ? c.range.min : null);
      const max = c.max !== undefined ? c.max : (c.range ? c.range.max : null);
      if (min === null || min === undefined) return `< ${max}`;
      if (max === null || max === undefined) return `> ${min}`;
      return `${min} – ${max}`;
    }
    function classRatio(c) { return pct(c.ratio || 0, 1); }
    function classArea(c) { return `${formatNumber(c.area_mu || 0, 2)} 亩`; }

    function toggleCompare(id) {
      if (compareIds.value.includes(id)) compareIds.value = compareIds.value.filter((x) => x !== id);
      else compareIds.value = [...compareIds.value, id];
    }
    function allCompare() { compareIds.value = results.value.map((r) => r.image_id); }

    function exportFile(kind) {
      if (!selectedResult.value || exportBusy.value) return;
      exportBusy.value = kind;
      notify(`正在生成 ${kind === 'png' ? 'PNG 专题图' : 'PDF 报告'}…`, 'info', '导出任务');
      const url = api.exportUrl(store.project.id, selectedResult.value.image_id, kind);
      downloadUrl(url);
      setTimeout(() => { exportBusy.value = ''; }, 2600);
    }

    function fmt(value, digits = 2) { return formatNumber(value, digits); }

    return {
      store, go, loading, results, history, selectedId, tab, mapMode, valueInfo, valueLoading,
      mapRef, exportBusy, compareIds, TABS_LIST, resultOptions, selectedResult, imageMeta,
      stats, classStats, classes, thresholds, area, model, unit, trace, alignmentTrace, preprocessTrace, fewShotTrace, demoModel,
      displayRange, mapUrl, mapWidth, mapHeight,
      lowClass, suitableClass, highClass, histogramRows, chartOption, compareResults,
      comparePeriods, compareOption, compareRows, thresholdTicks,
      fewShotOpen, fewShotFileInput, fewShotFileName, fewShotRows, fewShotActiveRow,
      fewShotCoordinateMode,
      fewShotUnit, fewShotName, fewShotPreview, fewShotError, fewShotBusy, fewShotJob,
      fewShotProgress, fewShotForce, fewShotCalibration, fewShotCanApply, fewShotFormula,
      selectResult, selectTab, zoomIn, zoomOut, resetView, onPixel, classRange, classRatio, classArea,
      addFewShotRow, removeFewShotRow, fillFromCurrentPixel, openFewShot, closeFewShot,
      onFewShotFile, downloadFewShotTemplate, setFewShotCoordinateMode, previewFewShot, applyFewShot,
      toggleCompare, allCompare, exportFile, fmt, formatDate, formatNumber, pct, percentValue, shortName,
      dateFieldLabel,
    };
  },
  template: `
  <div class="page results-page">
    <div class="page-head">
      <div class="ph-copy">
        <div class="eyebrow">RESULTS & REPORT</div>
        <h1>结果分析</h1>
        <p class="ph-sub">查看氮素分级专题图、分布直方图、田块统计与多期变化趋势；一键导出专题图与完整 PDF 报告。</p>
      </div>
      <div class="ph-actions results-head-actions">
        <select class="select" style="width:280px" :value="selectedId" @change="selectResult" v-if="results.length">
          <option v-for="r in results" :key="r.image_id" :value="r.image_id">{{ shortName(r.image_name, 32) }}</option>
        </select>
        <button class="btn btn-ghost" @click="openFewShot" :disabled="!selectedResult || !!fewShotBusy">
          <app-icon name="sliders" :size="16"/>实测微调
        </button>
        <button class="btn btn-ghost" @click="exportFile('png')" :disabled="!selectedResult || !!exportBusy">
          <span v-if="exportBusy === 'png'" class="spinner"></span>
          <app-icon v-else name="download" :size="16"/>导出 PNG
        </button>
        <button class="btn btn-primary" @click="exportFile('pdf')" :disabled="!selectedResult || !!exportBusy">
          <span v-if="exportBusy === 'pdf'" class="spinner light"></span>
          <app-icon v-else name="file-text" :size="16"/>导出 PDF
        </button>
      </div>
    </div>

    <div v-if="loading" class="card"><div class="loading-line" style="padding:40px;justify-content:center"><span class="spinner"></span>正在读取反演结果…</div></div>

    <template v-else-if="selectedResult">
      <div class="hint-box warn" v-if="demoModel" style="margin-bottom:14px">
        <app-icon name="alert-triangle" :size="15"/>
        <span>当前使用合成数据训练的示例模型「{{ model.name }}」。结果仅用于流程演示，不代表真实田间标定精度，不能直接作为施肥决策依据。</span>
      </div>
      <div class="summary-cards">
        <div class="stat-card">
          <div class="sc-icon"><app-icon name="gauge" :size="17"/></div>
          <div><div class="sc-label">平均 Nmass</div><div class="sc-value">{{ formatNumber(stats.mean, 2) }}<small>{{ unit }}</small></div><div class="sc-sub">中位数 {{ formatNumber(stats.p50, 2) }} {{ unit }}</div></div>
        </div>
        <div class="stat-card">
          <div class="sc-icon"><app-icon name="activity" :size="17"/></div>
          <div><div class="sc-label">变异系数</div><div class="sc-value">{{ formatNumber(stats.cv, 1) }}<small>%</small></div><div class="sc-sub">标准差 {{ formatNumber(stats.std, 2) }}</div></div>
        </div>
        <div class="stat-card">
          <div class="sc-icon" style="background:#fbece4;color:#b4541b"><app-icon name="target" :size="17"/></div>
          <div><div class="sc-label">低氮占比</div><div class="sc-value">{{ lowClass ? pct(lowClass.ratio, 1) : '—' }}</div><div class="sc-sub">需追肥区域</div></div>
        </div>
        <div class="stat-card">
          <div class="sc-icon"><app-icon name="map" :size="17"/></div>
          <div><div class="sc-label">有效面积</div><div class="sc-value">{{ formatNumber(area.valid_mu, 2) }}<small>亩</small></div><div class="sc-sub">{{ formatNumber(area.valid_ha, 2) }} 公顷</div></div>
        </div>
        <div class="stat-card">
          <div class="sc-icon"><app-icon name="cpu" :size="17"/></div>
          <div><div class="sc-label">反演模型</div><div class="sc-value model-name-value">{{ model.name || '—' }}</div><div class="sc-sub">{{ model.r2 !== undefined && model.r2 !== null ? 'R² ' + formatNumber(model.r2, 3) + ' · RMSE ' + formatNumber(model.rmse, 2) : '流程演示模型 · 未提供案例精度' }}</div></div>
        </div>
      </div>

      <div class="card" style="padding:0 16px;margin-bottom:14px">
        <div class="tabs" style="margin:0">
          <button v-for="t in TABS_LIST" :key="t.key" class="tab-btn" :class="{ active: tab === t.key }" @click="selectTab(t.key)">{{ t.label }}</button>
        </div>
      </div>

      <template v-if="tab === 'map'">
        <div class="result-map-grid">
          <div class="result-map-panel">
            <ImageCanvas ref="mapRef" :src="mapUrl" :natural-width="mapWidth" :natural-height="mapHeight" @pixel="onPixel"/>
            <div class="result-map-tools">
              <div class="segmented sm">
                <button class="seg-btn" :class="{ active: mapMode === 'classified' }" @click="mapMode = 'classified'">分级</button>
                <button class="seg-btn" :class="{ active: mapMode === 'continuous' }" @click="mapMode = 'continuous'">连续</button>
              </div>
              <div class="map-zoom">
                <button class="icon-btn" @click="zoomIn" title="放大"><app-icon name="zoom-in" :size="15"/></button>
                <button class="icon-btn" @click="zoomOut" title="缩小"><app-icon name="zoom-out" :size="15"/></button>
                <button class="icon-btn" @click="resetView" title="重置"><app-icon name="maximize" :size="15"/></button>
              </div>
            </div>

            <div class="result-legend">
              <div class="rl-title">{{ mapMode === 'classified' ? '氮素分级图例' : '氮素含量色带' }}</div>
              <template v-if="mapMode === 'classified'">
                <div v-for="c in classes" :key="c.key" class="legend-class">
                  <span class="sw" :style="{ background: c.color }"></span>
                  <div style="flex:1;min-width:0">
                    <div class="lc-name">{{ c.name }}</div>
                    <div class="lc-range">{{ classRange(c) }} {{ unit }}</div>
                  </div>
                  <span class="lc-pct">{{ classRatio(c) }}</span>
                </div>
                <div class="legend-mask"><i></i>非植被 / 无效像元（掩膜，不参与统计）</div>
              </template>
              <template v-else>
                <div class="colorbar-block">
                  <div class="colorbar-track"></div>
                  <div class="colorbar-labels"><span>{{ formatNumber(displayRange.min, 1) }}</span><span>{{ formatNumber(displayRange.max, 1) }} {{ unit }}</span></div>
                  <div class="colorbar-ticks">
                    <span v-for="t in thresholdTicks" :key="t.value" :style="{ left: t.left }">{{ t.value }}</span>
                  </div>
                </div>
                <div class="legend-mask" style="margin-top:8px"><i></i>深色区域为非植被 / 掩膜，不参与统计</div>
              </template>
            </div>

            <div class="value-pop" v-if="valueInfo">
              <span class="vp-dot" :style="{ background: valueInfo.color || '#98ab9d' }"></span>
              <div>
                <div class="vp-value" v-if="valueInfo.value !== null">{{ formatNumber(valueInfo.value, 2) }}<small>{{ valueInfo.unit || unit }}</small></div>
                <div class="vp-value" v-else style="font-size:13px">掩膜 / 无数据</div>
                <div class="vp-meta">像素 ({{ valueInfo.x }}, {{ valueInfo.y }})<template v-if="valueInfo.class_name"> · {{ valueInfo.class_name }}</template></div>
              </div>
              <button class="vp-close" @click="valueInfo = null"><app-icon name="x" :size="14"/></button>
            </div>
            <div class="value-pop" v-else-if="valueLoading"><span class="spinner light"></span><span class="vp-meta">正在读取像元值…</span></div>
            <div class="canvas-hint">滚轮缩放 · 拖动漫游 · 点击查询 Nmass</div>
          </div>

          <aside class="result-side">
            <section class="card">
              <div class="card-head"><div><div class="card-title"><app-icon name="bar-chart" :size="16"/>分级面积占比</div><div class="card-sub">支撑变量施肥分区决策</div></div></div>
              <div v-for="c in classStats" :key="c.key" class="class-stat-row">
                <span class="cs-sw" :style="{ background: c.color }"></span>
                <div>
                  <div class="cs-name">{{ c.name }} <span class="cs-range">{{ classRange(c) }} {{ unit }}</span></div>
                  <div class="class-bar"><i :style="{ width: (c.ratio * 100) + '%', background: c.color }"></i></div>
                </div>
                <div class="cs-num">{{ classRatio(c) }}<div class="cs-pct">{{ classArea(c) }}</div></div>
              </div>
            </section>

            <section class="card">
              <div class="card-title" style="margin-bottom:8px"><app-icon name="info" :size="16"/>结果信息</div>
              <div class="info-list">
                <div class="info-row" v-if="imageMeta"><span class="k">{{ dateFieldLabel(imageMeta) }}</span><span class="v">{{ formatDate(imageMeta.flight_date) }}</span></div>
                <div class="info-row"><span class="k">反演时间</span><span class="v">{{ formatDate(selectedResult.created_at, true) }}</span></div>
                <div class="info-row"><span class="k">分级阈值</span><span class="v">{{ thresholds.low }} / {{ thresholds.high }} {{ unit }}</span></div>
                <div class="info-row"><span class="k">有效像元</span><span class="v">{{ pct(stats.valid_ratio, 1) }}</span></div>
                <div class="info-row"><span class="k">像元大小</span><span class="v">{{ formatNumber(Math.sqrt(area.pixel_area_m2 || 0), 3) }} m</span></div>
                <div class="info-row"><span class="k">结果文件</span><span class="v" style="font-size:10.5px">{{ selectedResult.files ? selectedResult.files.tif : '—' }}</span></div>
              </div>
            </section>

            <section class="card">
              <div class="card-title" style="margin-bottom:7px"><app-icon name="leaf" :size="16"/>管理建议</div>
              <div class="help-text" style="display:flex;flex-direction:column;gap:6px">
                <p v-for="c in classStats" :key="c.key"><span :style="{ color: c.color, fontWeight: 700 }">■</span> {{ c.name }}区 {{ classRatio(c) }}，{{ c.action }}。</p>
              </div>
            </section>
          </aside>
        </div>
      </template>

      <template v-else-if="tab === 'histogram'">
        <section class="card chart-card">
          <div class="chart-title">
            <div>
              <h3>氮素含量分布直方图</h3>
              <p>按有效植被像元统计 · 共 {{ stats.count ? stats.count.toLocaleString('zh-CN') : 0 }} 个像元</p>
            </div>
            <div class="tag-row">
              <span class="tag plain">均值 {{ formatNumber(stats.mean, 2) }} {{ unit }}</span>
              <span class="tag plain">中位数 {{ formatNumber(stats.p50, 2) }}</span>
            </div>
          </div>
          <div class="chart-h"><EChart :option="chartOption"/></div>
          <div class="chart-note">
            <span class="legend-key"><i style="background:#D97706"></i>均值</span>
            <span class="legend-key"><i style="background:#8a9a8d"></i>分级阈值（低氮 &lt;{{ thresholds.low }}，高氮 &gt;{{ thresholds.high }}）</span>
          </div>
          <details class="data-details">
            <summary><app-icon name="table" :size="12"/>查看分箱数据表</summary>
            <div class="dd-body">
              <table class="data-table">
                <thead><tr><th>Nmass 区间（{{ unit }}）</th><th>像元数</th><th>占比</th></tr></thead>
                <tbody><tr v-for="(row, i) in histogramRows" :key="i"><td class="mono">{{ row.range }}</td><td class="mono">{{ row.count.toLocaleString('zh-CN') }}</td><td class="mono">{{ pct(row.ratio, 2) }}</td></tr></tbody>
              </table>
            </div>
          </details>
        </section>
      </template>

      <template v-else-if="tab === 'stats'">
        <div class="stats-grid">
          <section class="card">
            <div class="card-title" style="margin-bottom:10px"><app-icon name="activity" :size="16"/>田块统计指标</div>
            <div class="detail-stat-grid">
              <div class="detail-stat"><span class="k">均值</span><span class="v">{{ formatNumber(stats.mean, 3) }} {{ unit }}</span></div>
              <div class="detail-stat"><span class="k">标准差</span><span class="v">{{ formatNumber(stats.std, 3) }}</span></div>
              <div class="detail-stat"><span class="k">变异系数</span><span class="v">{{ formatNumber(stats.cv, 2) }}%</span></div>
              <div class="detail-stat"><span class="k">最小值</span><span class="v">{{ formatNumber(stats.min, 3) }}</span></div>
              <div class="detail-stat"><span class="k">最大值</span><span class="v">{{ formatNumber(stats.max, 3) }}</span></div>
              <div class="detail-stat"><span class="k">中位数</span><span class="v">{{ formatNumber(stats.p50, 3) }}</span></div>
              <div class="detail-stat"><span class="k">P5</span><span class="v">{{ formatNumber(stats.p5, 3) }}</span></div>
              <div class="detail-stat"><span class="k">P25</span><span class="v">{{ formatNumber(stats.p25, 3) }}</span></div>
              <div class="detail-stat"><span class="k">P75</span><span class="v">{{ formatNumber(stats.p75, 3) }}</span></div>
              <div class="detail-stat"><span class="k">P95</span><span class="v">{{ formatNumber(stats.p95, 3) }}</span></div>
              <div class="detail-stat"><span class="k">有效像元</span><span class="v">{{ stats.count ? stats.count.toLocaleString('zh-CN') : '—' }}</span></div>
              <div class="detail-stat"><span class="k">有效像元占比</span><span class="v">{{ pct(stats.valid_ratio, 2) }}</span></div>
              <div class="detail-stat"><span class="k">有效面积</span><span class="v">{{ formatNumber(area.valid_mu, 2) }} 亩</span></div>
              <div class="detail-stat"><span class="k">总像元</span><span class="v">{{ stats.total_count ? stats.total_count.toLocaleString('zh-CN') : '—' }}</span></div>
            </div>
          </section>

          <section class="card">
            <div class="card-title" style="margin-bottom:10px"><app-icon name="target" :size="16"/>分级面积统计</div>
            <table class="data-table">
              <thead><tr><th>等级</th><th>Nmass（{{ unit }}）</th><th>面积（亩）</th><th>占比</th><th>建议</th></tr></thead>
              <tbody>
                <tr v-for="c in classStats" :key="c.key">
                  <td><span class="status-badge" style="background:transparent;padding:0;height:auto"><i class="dot" :style="{ background: c.color }"></i>{{ c.name }}</span></td>
                  <td class="mono">{{ classRange(c) }}</td>
                  <td class="mono">{{ formatNumber(c.area_mu, 2) }}</td>
                  <td class="mono">{{ classRatio(c) }}</td>
                  <td>{{ c.action }}</td>
                </tr>
              </tbody>
            </table>
            <div class="divider"></div>
            <div class="detail-stat-grid">
              <div class="detail-stat"><span class="k">有效面积</span><span class="v">{{ formatNumber(area.valid_mu, 2) }} 亩</span></div>
              <div class="detail-stat"><span class="k">总面积</span><span class="v">{{ formatNumber(area.total_mu, 2) }} 亩</span></div>
              <div class="detail-stat"><span class="k">像元面积</span><span class="v">{{ formatNumber(area.pixel_area_m2, 4) }} m²</span></div>
              <div class="detail-stat"><span class="k">单位</span><span class="v">{{ unit }}</span></div>
            </div>
          </section>

          <section class="card">
            <div class="card-title" style="margin-bottom:10px"><app-icon name="cpu" :size="16"/>模型与参数</div>
            <div class="info-list">
              <div class="info-row"><span class="k">模型名称</span><span class="v">{{ model.name || '—' }}</span></div>
              <div class="info-row"><span class="k">算法</span><span class="v">{{ model.algorithm || '—' }}</span></div>
              <div class="info-row"><span class="k">适用作物 / 传感器</span><span class="v">{{ (model.crops || []).join('、') || '—' }} / {{ (model.sensors || []).join('、') || '—' }}</span></div>
              <div class="info-row"><span class="k">精度指标</span><span class="v">{{ model.r2 !== undefined && model.r2 !== null ? 'R² ' + formatNumber(model.r2, 4) + ' · RMSE ' + formatNumber(model.rmse, 3) + ' ' + unit : '未提供与该案例对应的独立精度指标' }}</span></div>
              <div class="info-row"><span class="k">训练数据</span><span class="v">{{ model.training_data || '—' }}</span></div>
              <div class="info-row"><span class="k">模型版本 / 哈希</span><span class="v mono" style="font-size:10px">{{ model.version || '—' }} / {{ (model.file_sha256 || (trace.model && trace.model.file_sha256) || '—').slice(0, 16) }}</span></div>
              <div class="info-row" v-if="fewShotTrace"><span class="k">实测 few-shot 校正</span><span class="v">{{ fewShotTrace.sample_count }} 个样点 · {{ fewShotTrace.mode === 'offset' ? '偏置' : '收缩线性' }}</span></div>
              <div class="info-row" v-if="fewShotTrace"><span class="k">校正公式</span><span class="v mono" style="font-size:10px">{{ formatNumber(fewShotTrace.slope, 4) }} × 父模型值 {{ Number(fewShotTrace.intercept) >= 0 ? '+' : '−' }} {{ Math.abs(Number(fewShotTrace.intercept || 0)).toFixed(4) }}</span></div>
              <div class="info-row" v-if="fewShotTrace"><span class="k">父模型</span><span class="v">{{ fewShotTrace.parent_model_name || fewShotTrace.parent_model_id || '—' }}</span></div>
              <div class="info-row" v-if="fewShotTrace"><span class="k">留一验证</span><span class="v">RMSE {{ formatNumber(fewShotTrace.leave_one_out?.rmse, 3) }} · MAE {{ formatNumber(fewShotTrace.leave_one_out?.mae, 3) }} {{ unit }}</span></div>
              <div class="info-row"><span class="k">训练波长</span><span class="v">{{ trace.model && trace.model.training_wavelengths && trace.model.training_wavelengths.length ? (trace.model.training_wavelengths[0] + '–' + trace.model.training_wavelengths[trace.model.training_wavelengths.length - 1] + ' nm · ' + trace.model.training_wavelengths.length + ' 波段') : '—' }}</span></div>
              <div class="info-row"><span class="k">重采样 / 边缘填充</span><span class="v">精确 {{ alignmentTrace.exact_bands ?? 0 }} · 插值 {{ alignmentTrace.interpolated_bands ?? 0 }} · 填充 {{ alignmentTrace.edge_filled_bands ?? 0 }}</span></div>
              <div class="info-row"><span class="k">强制忽略警告</span><span class="v">{{ trace.force_used ? '是' : '否' }}</span></div>
              <div class="info-row"><span class="k">分级阈值</span><span class="v">低氮 &lt; {{ thresholds.low }} {{ unit }} · 高氮 &gt; {{ thresholds.high }} {{ unit }}</span></div>
              <div class="info-row"><span class="k">反演耗时</span><span class="v">{{ selectedResult.duration_sec !== undefined ? selectedResult.duration_sec + ' s' : '—' }}</span></div>
            </div>
          </section>

          <section class="card">
            <div class="card-title" style="margin-bottom:10px"><app-icon name="layers" :size="16"/>数据与质量控制</div>
            <div class="info-list">
              <div class="info-row"><span class="k">结果尺寸</span><span class="v mono">{{ selectedResult.width }} × {{ selectedResult.height }}</span></div>
              <div class="info-row"><span class="k">坐标系</span><span class="v">{{ selectedResult.crs || '—' }}</span></div>
              <div class="info-row"><span class="k">有效像元占比</span><span class="v">{{ pct(stats.valid_ratio, 2) }}</span></div>
              <div class="info-row"><span class="k">结果单位</span><span class="v">{{ unit }}</span></div>
              <div class="info-row"><span class="k">预处理配置哈希</span><span class="v mono" style="font-size:10px">{{ preprocessTrace.config_sha256 || '—' }}</span></div>
              <div class="info-row"><span class="k">预处理质量</span><span class="v">{{ preprocessTrace.quality_level || '—' }}</span></div>
              <div class="info-row"><span class="k">GeoTIFF</span><span class="v" style="font-size:10.5px">{{ selectedResult.files ? selectedResult.files.tif : '—' }}</span></div>
            </div>
            <div class="hint-box" style="margin-top:11px">
              <app-icon name="shield" :size="14"/>
              <span>反演仅统计植被掩膜内的有效像元；非植被、云及无效值不参与均值与面积计算。</span>
            </div>
          </section>
        </div>
      </template>

      <template v-else-if="tab === 'compare'">
        <section class="card chart-card">
          <div class="chart-title">
            <div>
              <h3>多期氮素变化趋势</h3>
              <p>折线为各期均值，误差线为标准差；浅绿背景为适宜区间（{{ thresholds.low }}–{{ thresholds.high }} {{ unit }}）。卫星数据日期标签使用“采集日期”。</p>
            </div>
            <button class="btn btn-sm btn-ghost" @click="allCompare"><app-icon name="check" :size="14"/>全选各期</button>
          </div>
          <div class="compare-selector">
            <button v-for="r in results" :key="r.image_id" class="compare-chip" :class="{ active: compareIds.includes(r.image_id) }" @click="toggleCompare(r.image_id)">
              <span class="dot"></span>{{ r.flight_date || '未填日期' }} · {{ r.image_name }}
            </button>
          </div>
          <div v-if="comparePeriods.length" class="chart-h"><EChart :option="compareOption"/></div>
          <div v-else class="compare-empty">请至少选择一期有反演结果的影像</div>
          <div class="chart-note">
            <span class="legend-key"><i style="background:#2f7d4a"></i>平均 Nmass</span>
            <span class="legend-key"><i style="background:#2f7d4a;opacity:.5"></i>标准差</span>
            <span class="legend-key"><i style="background:rgba(0,158,115,.2)"></i>适宜区间</span>
          </div>
        </section>

        <section class="card" style="margin-top:14px">
          <div class="card-title" style="margin-bottom:10px"><app-icon name="table" :size="16"/>各期统计摘要</div>
          <table class="data-table">
            <thead><tr><th>日期</th><th>影像</th><th>均值（单位）</th><th>标准差</th><th>变异系数</th><th>低氮</th><th>适宜</th><th>高氮</th><th>模型</th></tr></thead>
            <tbody>
              <tr v-for="row in compareRows" :key="row.id">
                <td class="mono">{{ row.date }}</td>
                <td>{{ row.name }}</td>
                <td class="mono">{{ formatNumber(row.mean, 2) }} {{ row.unit }}</td>
                <td class="mono">{{ formatNumber(row.std, 2) }}</td>
                <td class="mono">{{ formatNumber(row.cv, 1) }}%</td>
                <td class="mono">{{ row.low }}</td>
                <td class="mono">{{ row.suitable }}</td>
                <td class="mono">{{ row.high }}</td>
                <td style="font-size:11px">{{ row.model }}</td>
              </tr>
            </tbody>
          </table>
          <div class="hint-box" style="margin-top:11px" v-if="compareRows.length < 2">
            <app-icon name="info" :size="14"/><span>{{ imageMeta ? dateFieldLabel(imageMeta) : '日期' }}仅有一期结果；至少需要两期影像才能观察变化趋势。</span>
          </div>
        </section>
      </template>
    </template>

    <div v-else class="card">
      <EmptyState icon="chart" title="还没有反演结果" text="完成一次模型反演后，这里会展示氮素专题图、统计指标、直方图与多期对比。">
        <div class="row" style="gap:8px">
          <button class="btn btn-primary" @click="go('inversion')"><app-icon name="cpu" :size="16"/>去执行反演</button>
          <button class="btn btn-ghost" @click="go('import')" v-if="!(store.project && store.project.image_count)"><app-icon name="upload" :size="16"/>先导入影像</button>
        </div>
      </EmptyState>
    </div>

    <Modal :open="fewShotOpen" title="基于实测样点的 few-shot 校正" subtitle="父模型权重保持不变；样点用于学习偏置或斜率校正，并生成可复用派生模型" icon="sliders" wide :close-on-backdrop="false" @close="closeFewShot">
      <div class="fewshot-intro">
        <div><b>校正原理</b><p>先使用父模型得到当前影像的预测值，再把与实测 Nmass 配对的样点拟合为“校正值 = slope × 父模型值 + intercept”。1–2 个样点只做整体偏置，3–4 个样点采用收缩线性校正，建议至少 5 个。</p></div>
        <div><b>坐标要求</b><p v-if="fewShotCoordinateMode === 'pixel'">CSV 使用 <code>sample_id,col,row,nmass</code>，其中列号和行号是当前预处理栅格从 0 开始的像元坐标。无效像元、掩膜外像元和重复坐标会被拒绝。</p><p v-else>CSV 使用 <code>sample_id,lon,lat,nmass</code>；经纬度按 WGS84 解释，并自动转换到当前结果栅格坐标。超出范围、掩膜外和重复像元会被拒绝。</p></div>
      </div>

      <div class="fewshot-toolbar">
        <div class="segmented sm">
          <button class="seg-btn" :class="{ active: fewShotCoordinateMode === 'pixel' }" @click="setFewShotCoordinateMode('pixel')">像元坐标</button>
          <button class="seg-btn" :class="{ active: fewShotCoordinateMode === 'lonlat' }" @click="setFewShotCoordinateMode('lonlat')">经纬度</button>
        </div>
        <input ref="fewShotFileInput" type="file" accept=".csv,text/csv" hidden @change="onFewShotFile">
        <button class="btn btn-sm btn-ghost" @click="fewShotFileInput && fewShotFileInput.click()"><app-icon name="upload" :size="14"/>导入 CSV</button>
        <button class="btn btn-sm btn-text" @click="downloadFewShotTemplate"><app-icon name="download" :size="14"/>{{ fewShotCoordinateMode === 'pixel' ? '下载像元模板' : '下载经纬度模板' }}</button>
        <span class="tiny muted" v-if="fewShotFileName">{{ fewShotFileName }}</span>
        <span class="tiny muted" v-else>也可在下方手动录入样点</span>
      </div>

      <div class="form-grid" style="margin-top:12px">
        <div class="field full"><label>派生模型名称</label><input class="input" v-model="fewShotName" placeholder="例如：田块A DNN 实测校正模型"></div>
        <div class="field"><label>实测数据单位</label><input class="input" v-model="fewShotUnit" placeholder="必须与当前结果单位一致"></div>
        <div class="field"><label>当前父模型</label><input class="input" :value="model.name || '—'" disabled></div>
      </div>

      <div class="fewshot-table-wrap">
        <table class="data-table fewshot-table">
          <thead>
            <tr>
              <th>样本编号</th>
              <th v-if="fewShotCoordinateMode === 'pixel'">列 col</th>
              <th v-if="fewShotCoordinateMode === 'pixel'">行 row</th>
              <th v-if="fewShotCoordinateMode === 'lonlat'">经度 lon</th>
              <th v-if="fewShotCoordinateMode === 'lonlat'">纬度 lat</th>
              <th>实测 Nmass</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in fewShotRows" :key="row._key">
              <td><input class="input sm" v-model="row.sample_id" @focus="fewShotActiveRow = index"></td>
              <td v-if="fewShotCoordinateMode === 'pixel'"><input class="input sm mono" type="number" min="0" step="1" v-model="row.col" @focus="fewShotActiveRow = index"></td>
              <td v-if="fewShotCoordinateMode === 'pixel'"><input class="input sm mono" type="number" min="0" step="1" v-model="row.row" @focus="fewShotActiveRow = index"></td>
              <td v-if="fewShotCoordinateMode === 'lonlat'"><input class="input sm mono" type="number" step="0.000001" v-model="row.lon" @focus="fewShotActiveRow = index"></td>
              <td v-if="fewShotCoordinateMode === 'lonlat'"><input class="input sm mono" type="number" step="0.000001" v-model="row.lat" @focus="fewShotActiveRow = index"></td>
              <td><input class="input sm mono" type="number" step="0.01" v-model="row.nmass" @focus="fewShotActiveRow = index"></td>
              <td class="fewshot-actions">
                <button class="btn btn-sm btn-text" v-if="fewShotCoordinateMode === 'pixel'" @click="fillFromCurrentPixel(index)" :disabled="!valueInfo">填入当前像元</button>
                <button class="icon-btn" @click="removeFewShotRow(index)" title="删除样点"><app-icon name="trash" :size="14"/></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="row" style="justify-content:space-between;margin-top:8px;align-items:center">
        <button class="btn btn-sm btn-soft" @click="addFewShotRow"><app-icon name="plus" :size="14"/>添加样点</button>
        <span class="tiny muted">已录入 {{ fewShotRows.length }} 行 · 建议至少 5 个有效样点并覆盖低、中、高 Nmass</span>
      </div>

      <div v-if="fewShotError" class="hint-box danger" style="margin-top:10px"><app-icon name="alert-circle" :size="14"/><span>{{ fewShotError }}</span></div>

      <template v-if="fewShotPreview">
        <div class="fewshot-result-head">
          <div>
            <div class="card-title">校正预览</div>
            <div class="card-sub">{{ fewShotPreview.parent_model.name }} → {{ fewShotName || '待命名派生模型' }}</div>
          </div>
          <div class="tag-row">
            <span class="tag green">{{ fewShotPreview.sample_count }} 个有效样点</span>
            <span class="tag amber" v-if="fewShotPreview.rejected_samples.length">{{ fewShotPreview.rejected_samples.length }} 个未使用</span>
            <span class="tag">{{ fewShotCalibration.mode === 'offset' ? '偏置校正' : '收缩线性校正' }}</span>
          </div>
        </div>
        <div class="fewshot-formula">{{ fewShotFormula }}</div>
        <div class="fewshot-metrics">
          <div><span>校正前 RMSE</span><strong>{{ formatNumber(fewShotCalibration.before?.rmse, 3) }} {{ fewShotPreview.unit }}</strong></div>
          <div><span>校正后 RMSE</span><strong>{{ formatNumber(fewShotCalibration.after_in_sample?.rmse, 3) }} {{ fewShotPreview.unit }}</strong></div>
          <div><span>留一验证 RMSE</span><strong>{{ formatNumber(fewShotCalibration.leave_one_out?.rmse, 3) }} {{ fewShotPreview.unit }}</strong></div>
          <div><span>校正前偏差</span><strong>{{ formatNumber(fewShotCalibration.before?.bias, 3) }} {{ fewShotPreview.unit }}</strong></div>
        </div>
        <div class="fewshot-table-wrap" style="margin-top:10px">
          <table class="data-table">
            <thead><tr><th>样点</th><th>列 / 行</th><th>预测值</th><th>实测值</th><th>校正后</th><th>残差</th></tr></thead>
            <tbody>
              <tr v-for="s in fewShotPreview.samples" :key="s.sample_id">
                <td>{{ s.sample_id }}</td><td class="mono">{{ s.col }} / {{ s.row }}</td>
                <td class="mono">{{ formatNumber(s.base_prediction, 3) }}</td><td class="mono">{{ formatNumber(s.nmass, 3) }}</td>
                <td class="mono">{{ formatNumber(s.calibrated_prediction, 3) }}</td><td class="mono">{{ formatNumber(s.calibrated_residual, 3) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="fewShotPreview.rejected_samples.length" class="hint-box warn" style="margin-top:10px">
          <app-icon name="alert-triangle" :size="14"/>
          <div><div class="strong">未使用的样点</div><div v-for="r in fewShotPreview.rejected_samples" :key="r.sample_id">{{ r.sample_id }}：{{ r.error }}</div></div>
        </div>
        <div v-for="w in (fewShotCalibration.warnings || [])" :key="w" class="hint-box warn" style="margin-top:8px">
          <app-icon name="alert-triangle" :size="14"/><span>{{ w }}</span>
        </div>
        <label class="check-row" style="margin-top:10px">
          <input type="checkbox" v-model="fewShotForce"> 如派生模型预检仍有警告，确认强制应用
        </label>
      </template>

      <div v-if="fewShotBusy === 'apply' || fewShotJob" class="inline-progress">
        <div class="progress-track"><div class="progress-fill" :style="{ width: fewShotProgress + '%' }"></div></div>
        <div class="progress-meta"><span>{{ fewShotJob?.stage || '准备中' }} · {{ fewShotJob?.message || '正在创建派生模型' }}</span><span>{{ fewShotProgress }}%</span></div>
      </div>

      <template #footer>
        <button class="btn btn-ghost" @click="closeFewShot" :disabled="fewShotBusy === 'apply'">取消</button>
        <button class="btn btn-ghost" @click="previewFewShot" :disabled="!!fewShotBusy || !fewShotRows.length">
          <span v-if="fewShotBusy === 'preview'" class="spinner"></span>
          <app-icon v-else name="refresh" :size="15"/>预览校正
        </button>
        <button class="btn btn-primary" @click="applyFewShot" :disabled="!fewShotCanApply">
          <span v-if="fewShotBusy === 'apply'" class="spinner light"></span>
          <app-icon v-else name="check" :size="15"/>应用并重新反演
        </button>
      </template>
    </Modal>
  </div>`,
};
