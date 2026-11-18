/* ECharts 封装与图表配置 */
import { formatDate } from '../utils.js';

const { ref, onMounted, onBeforeUnmount, watch } = window.Vue;

export const SERIES_COLORS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181'];
export const BRAND_GREEN = '#2f7d4a';
export const SUITABLE_GREEN = '#009E73';

export function resultUnit(result) {
  return String(result?.unit || result?.model?.unit || '待确认');
}

const DARK = {
  ink: '#e8f0ea',
  muted: '#98ab9d',
  grid: '#1d2a22',
  axis: '#33443a',
  tooltipBg: '#13201a',
  tooltipBorder: '#2c3c32',
};
const LIGHT = {
  ink: '#1c2a21',
  muted: '#6a7c6f',
  grid: '#e6eee7',
  axis: '#c9d6cc',
  tooltipBg: '#ffffff',
  tooltipBorder: '#e1e9df',
};

export const EChart = {
  name: 'EChart',
  props: {
    option: { type: Object, required: true },
    dark: { type: Boolean, default: false },
  },
  setup(props) {
    const el = ref(null);
    let chart = null;
    let ro = null;
    function render() {
      if (!el.value) return;
      if (!chart) {
        chart = window.echarts.init(el.value, null, { renderer: 'canvas' });
      }
      chart.setOption(props.option || {}, true);
    }
    onMounted(() => {
      render();
      if (typeof ResizeObserver !== 'undefined') {
        ro = new ResizeObserver(() => { if (chart) chart.resize(); });
        ro.observe(el.value);
      }
      window.addEventListener('resize', resize);
    });
    function resize() { if (chart) chart.resize(); }
    watch(() => props.option, render, { deep: true });
    onBeforeUnmount(() => {
      if (ro) ro.disconnect();
      window.removeEventListener('resize', resize);
      if (chart) chart.dispose();
      chart = null;
    });
    return { el };
  },
  template: `<div ref="el" style="width:100%;height:100%"></div>`,
};

function axisBase(theme) {
  return {
    axisLine: { lineStyle: { color: theme.axis } },
    axisTick: { show: false },
    axisLabel: { color: theme.muted, fontSize: 10.5, fontFamily: 'Microsoft YaHei, sans-serif' },
    splitLine: { lineStyle: { color: theme.grid, type: 'solid' } },
  };
}

export function spectrumOption(series, unit = 'nm') {
  const list = (series || []).slice(0, 5);
  const showLegend = list.length > 1;
  const maxBands = Math.max(1, ...list.map((s) => (s.values || []).length));
  const fmtX = (v) => {
    const n = Number(v);
    return unit === 'nm' ? `${Number.isInteger(n) ? n : n.toFixed(0)} nm` : `波段 ${Math.round(n)}`;
  };
  return {
    backgroundColor: 'transparent',
    animationDuration: 320,
    textStyle: { fontFamily: 'Microsoft YaHei, sans-serif' },
    grid: { left: 54, right: 16, top: showLegend ? 42 : 22, bottom: 44 },
    legend: showLegend ? {
      top: 2,
      left: 0,
      type: 'scroll',
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 14,
      textStyle: { color: '#c3d2c7', fontSize: 10.5 },
      pageTextStyle: { color: DARK.muted },
      pageIconColor: '#8fae98',
      pageIconInactiveColor: '#4b5f52',
    } : { show: false },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: DARK.tooltipBg,
      borderColor: DARK.tooltipBorder,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: DARK.ink, fontSize: 11 },
      axisPointer: {
        type: 'line',
        lineStyle: { color: '#5b7a63', type: 'dashed', width: 1 },
      },
      formatter(params) {
        if (!params || !params.length) return '';
        const x = params[0].axisValue;
        const head = unit === 'nm' ? `波长 ${Math.round(x)} nm` : `波段 ${Math.round(x)}`;
        const rows = params.map((p) => {
          const value = Array.isArray(p.value) ? p.value[1] : p.value;
          return `<div style="display:flex;justify-content:space-between;gap:18px;line-height:1.7"><span>${p.marker}${p.seriesName}</span><span style="font-variant-numeric:tabular-nums">${Number(value).toFixed(4)}</span></div>`;
        }).join('');
        return `<div style="font-weight:600;margin-bottom:3px">${head}</div>${rows}`;
      },
    },
    xAxis: {
      type: 'value',
      name: unit === 'nm' ? '波长 (nm)' : '波段序号',
      nameLocation: 'middle',
      nameGap: 27,
      nameTextStyle: { color: DARK.muted, fontSize: 10 },
      min: unit === 'nm' ? 'dataMin' : 1,
      max: unit === 'nm' ? 'dataMax' : maxBands,
      scale: unit === 'nm',
      splitNumber: 4,
      ...axisBase(DARK),
      axisLabel: { ...axisBase(DARK).axisLabel, hideOverlap: true, formatter: (v) => Math.round(Number(v)) },
      splitLine: { show: true, lineStyle: { color: DARK.grid } },
    },
    yAxis: {
      type: 'value',
      name: '光谱值',
      nameLocation: 'middle',
      nameRotate: 90,
      nameGap: 40,
      nameTextStyle: { color: DARK.muted, fontSize: 10 },
      scale: true,
      ...axisBase(DARK),
    },
    series: list.map((s, i) => {
      const color = s.color || SERIES_COLORS[i % SERIES_COLORS.length];
      const xs = (s.wavelengths && s.wavelengths.length === s.values.length)
        ? s.wavelengths
        : s.values.map((_, idx) => idx + 1);
      return {
        name: s.label,
        type: 'line',
        data: s.values.map((v, idx) => [xs[idx], v]),
        showSymbol: false,
        symbol: 'circle',
        symbolSize: 5,
        smooth: false,
        connectNulls: false,
        lineStyle: { width: 2, color },
        itemStyle: { color },
        emphasis: { focus: 'series', lineStyle: { width: 2.4 } },
        z: 3,
      };
    }),
  };
}

export function histogramOption(result) {
  const hist = result?.histogram || {};
  const edges = hist.edges || [];
  const counts = hist.counts || [];
  const classes = result?.thresholds || {};
  const unit = resultUnit(result);
  const low = Number(classes.low ?? 22);
  const high = Number(classes.high ?? 28);
  const centers = [];
  for (let i = 0; i < counts.length; i++) centers.push((edges[i] + edges[i + 1]) / 2);
  const labels = centers.map((v) => (v >= 100 ? v.toFixed(0) : v.toFixed(1)));
  const mean = Number(hist.mean ?? 0);
  const total = counts.reduce((a, b) => a + b, 0) || 1;
  const nearestIndex = (value) => {
    let best = 0;
    let diff = Infinity;
    centers.forEach((c, i) => {
      const d = Math.abs(c - value);
      if (d < diff) { diff = d; best = i; }
    });
    return best;
  };
  const meanIdx = nearestIndex(mean);
  const lowIdx = nearestIndex(low);
  const highIdx = nearestIndex(high);
  const step = Math.max(1, Math.ceil(counts.length / 7));

  return {
    backgroundColor: 'transparent',
    animationDuration: 320,
    textStyle: { fontFamily: 'Microsoft YaHei, sans-serif' },
    grid: { left: 55, right: 22, top: 22, bottom: 42 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: LIGHT.tooltipBg,
      borderColor: LIGHT.tooltipBorder,
      textStyle: { color: LIGHT.ink, fontSize: 11 },
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(47,125,74,.08)' } },
      formatter(params) {
        const p = params && params[0];
        if (!p) return '';
        const i = p.dataIndex;
        const lo = edges[i];
        const hi = edges[i + 1];
        const value = counts[i];
        return `<div style="font-weight:600;margin-bottom:3px">${lo.toFixed(1)} – ${hi.toFixed(1)} ${unit}</div>
          <div style="display:flex;justify-content:space-between;gap:20px"><span>像元数</span><b>${value.toLocaleString('zh-CN')}</b></div>
          <div style="display:flex;justify-content:space-between;gap:20px;color:#6a7c6f"><span>占比</span><span>${((value / total) * 100).toFixed(2)}%</span></div>`;
      },
    },
    xAxis: {
      type: 'category',
      data: labels,
      name: `Nmass (${unit})`,
      nameLocation: 'middle',
      nameGap: 27,
      nameTextStyle: { color: LIGHT.muted, fontSize: 10.5 },
      ...axisBase(LIGHT),
      axisLabel: { ...axisBase(LIGHT).axisLabel, interval: step - 1 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: '像元数',
      nameLocation: 'middle',
      nameRotate: 90,
      nameGap: 42,
      nameTextStyle: { color: LIGHT.muted, fontSize: 10.5 },
      ...axisBase(LIGHT),
    },
    series: [{
      name: '像元数',
      type: 'bar',
      data: counts,
      barCategoryGap: '24%',
      itemStyle: {
        color: BRAND_GREEN,
        borderRadius: [4, 4, 0, 0],
        borderColor: '#ffffff',
        borderWidth: 1,
      },
      emphasis: { itemStyle: { color: '#3d9960' } },
      markLine: {
        silent: true,
        symbol: 'none',
        label: { show: true, rotate: 0, fontSize: 10, color: LIGHT.ink, backgroundColor: 'rgba(255,255,255,.9)', padding: [2, 4], borderRadius: 3 },
        data: [
          {
            xAxis: meanIdx,
            lineStyle: { color: '#D97706', type: 'dashed', width: 1.5 },
            label: { formatter: `均值 ${mean.toFixed(2)}`, position: 'insideEndTop', rotate: 0, color: '#B45309' },
          },
          {
            xAxis: lowIdx,
            lineStyle: { color: '#8a9a8d', type: 'dotted', width: 1.2 },
            label: { formatter: `低氮 ${low.toFixed(1)}`, position: 'insideStartTop', rotate: 0, color: '#6a7c6f' },
          },
          {
            xAxis: highIdx,
            lineStyle: { color: '#8a9a8d', type: 'dotted', width: 1.2 },
            label: { formatter: `高氮 ${high.toFixed(1)}`, position: 'insideStartTop', rotate: 0, color: '#6a7c6f' },
          },
        ],
      },
    }],
  };
}

export function trendOption(periods) {
  const list = periods || [];
  const units = Array.from(new Set(list.map(resultUnit)));
  const commonUnit = units.length === 1 ? units[0] : '各期单位不一致';
  const labels = list.map((p) => (p.flight_date ? formatDate(p.flight_date).slice(5) : (p.image_name || '').slice(0, 8)));
  const low = Number(list[0]?.thresholds?.low ?? 22);
  const high = Number(list[0]?.thresholds?.high ?? 28);
  const means = list.map((p) => Number(p.stats?.mean ?? 0));
  const stds = list.map((p) => Number(p.stats?.std ?? 0));
  const lows = list.map((p, i) => means[i] - stds[i]);
  const highs = list.map((p, i) => means[i] + stds[i]);
  const rawMin = Math.min(low, ...lows);
  const rawMax = Math.max(high, ...highs);
  const pad = Math.max(0.8, (rawMax - rawMin) * 0.08);
  const yMin = Math.max(0, Math.floor((rawMin - pad) / 2) * 2);
  const yMax = Math.ceil((rawMax + pad) / 2) * 2;
  return {
    backgroundColor: 'transparent',
    animationDuration: 320,
    textStyle: { fontFamily: 'Microsoft YaHei, sans-serif' },
    grid: { left: 55, right: 24, top: 28, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: LIGHT.tooltipBg,
      borderColor: LIGHT.tooltipBorder,
      textStyle: { color: LIGHT.ink, fontSize: 11 },
      axisPointer: { type: 'line', lineStyle: { color: '#b9cbbd', type: 'dashed' } },
      formatter(params) {
        const p = params && params.find((x) => x.seriesType === 'line');
        if (!p) return '';
        const i = p.dataIndex;
        const r = list[i];
        if (!r) return '';
        const st = r.stats || {};
        return `<div style="font-weight:600;margin-bottom:3px">${r.flight_date || ''}　${r.image_name || ''}</div>
          <div style="display:flex;justify-content:space-between;gap:18px"><span>均值</span><b>${Number(st.mean).toFixed(2)} ${resultUnit(r)}</b></div>
          <div style="display:flex;justify-content:space-between;gap:18px;color:#6a7c6f"><span>标准差</span><span>${Number(st.std).toFixed(2)}</span></div>
          <div style="display:flex;justify-content:space-between;gap:18px;color:#6a7c6f"><span>变异系数</span><span>${Number(st.cv).toFixed(1)}%</span></div>`;
      },
    },
    xAxis: {
      type: 'category',
      data: labels,
      boundaryGap: true,
      axisLabel: { color: LIGHT.muted, fontSize: 10.5, interval: 0 },
      axisLine: { lineStyle: { color: LIGHT.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: `Nmass (${commonUnit})`,
      nameTextStyle: { color: LIGHT.muted, fontSize: 10.5 },
      min: Number(yMin.toFixed(2)),
      max: Number(yMax.toFixed(2)),
      ...axisBase(LIGHT),
    },
    series: [
      {
        name: '标准差',
        type: 'custom',
        z: 1,
        silent: true,
        data: list.map((p, i) => [i, Number(p.stats?.mean ?? 0), Number(p.stats?.std ?? 0)]),
        renderItem(params, api) {
          const idx = api.value(0);
          const mean = api.value(1);
          const std = api.value(2);
          const x = api.coord([idx, mean])[0];
          const top = api.coord([idx, mean + std])[1];
          const bottom = api.coord([idx, mean - std])[1];
          const cap = 5;
          const style = { stroke: BRAND_GREEN, lineWidth: 1.2, opacity: 0.62 };
          return {
            type: 'group',
            children: [
              { type: 'line', shape: { x1: x, y1: top, x2: x, y2: bottom }, style },
              { type: 'line', shape: { x1: x - cap, y1: top, x2: x + cap, y2: top }, style },
              { type: 'line', shape: { x1: x - cap, y1: bottom, x2: x + cap, y2: bottom }, style },
            ],
          };
        },
      },
      {
        name: '平均 Nmass',
        type: 'line',
        z: 3,
        data: list.map((p) => Number(p.stats?.mean ?? 0)),
        symbol: 'circle',
        symbolSize: 8,
        showSymbol: true,
        lineStyle: { width: 2, color: BRAND_GREEN },
        itemStyle: { color: BRAND_GREEN, borderColor: '#ffffff', borderWidth: 2 },
        label: {
          show: list.length <= 8,
          position: 'top',
          distance: 8,
          formatter: (p) => Number(p.value).toFixed(1),
          color: LIGHT.ink,
          fontSize: 10,
          fontWeight: 600,
        },
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(0,158,115,.065)' },
          label: { show: true, position: 'insideTop', color: '#7d9984', fontSize: 9, formatter: '适宜区间' },
          data: [[{ yAxis: low }, { yAxis: high }]],
        },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(47,125,74,.10)' },
              { offset: 1, color: 'rgba(47,125,74,0)' },
            ],
          },
        },
      },
    ],
  };
}
