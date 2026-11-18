/* 通用工具函数 */

export function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

export function shortName(name, max = 30) {
  const s = String(name || '');
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

export function formatDate(iso, withTime = false) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  const pad = (n) => String(n).padStart(2, '0');
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  if (!withTime) return date;
  return `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function dateFieldLabel(image) {
  const text = `${image?.sensor || ''} ${image?.sensor_name || ''} ${image?.image_type || ''} ${image?.format || ''}`.toLowerCase();
  return /satellite|卫星|planetscope|sentinel|landsat/.test(text) ? '采集日期' : '飞行日期';
}

export function timeAgo(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const diff = Date.now() - d.getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  const day = Math.floor(hour / 24);
  if (day < 30) return `${day} 天前`;
  return formatDate(iso);
}

export function formatNumber(value, digits = 2, dash = '—') {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return dash;
  const n = Number(value);
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatCompact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  if (Math.abs(n) >= 10000) return (n / 10000).toFixed(1) + ' 万';
  return n.toLocaleString('zh-CN');
}

export function pct(ratio, digits = 1) {
  if (ratio === null || ratio === undefined || Number.isNaN(Number(ratio))) return '—';
  return (Number(ratio) * 100).toFixed(digits) + '%';
}

export function percentValue(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(digits) + '%';
}

export function formatFileSize(bytes) {
  if (!bytes && bytes !== 0) return '—';
  const n = Number(bytes);
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}

export function projectStatusMeta(status) {
  const map = {
    completed: { label: '已完成', cls: 'completed' },
    processing: { label: '处理中', cls: 'processing' },
    imported: { label: '待反演', cls: 'imported' },
    empty: { label: '待导入', cls: 'empty' },
  };
  return map[status] || { label: status || '未知', cls: 'empty' };
}

export function imageStatusMeta(status, hasResult = false) {
  if (hasResult || status === 'inverted') return { label: '已反演', cls: 'completed' };
  if (status === 'preprocessed') return { label: '已预处理', cls: 'processing' };
  return { label: '待处理', cls: 'imported' };
}

export function nearestBand(wavelengths, target, tolerance = Infinity) {
  if (!Array.isArray(wavelengths) || !wavelengths.length) return null;
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < wavelengths.length; i++) {
    const diff = Math.abs(Number(wavelengths[i]) - target);
    if (diff < bestDiff) { bestDiff = diff; best = i; }
  }
  if (bestDiff > tolerance) return null;
  return best;
}

export function pickViewerBands(image, kind = 'rgb') {
  const wl = image?.wavelengths || [];
  const n = Number(image?.bands) || 1;
  const fallback = (idx) => Math.min(Math.max(idx, 0), n - 1) + 1;
  if (wl.length === n && n >= 3) {
    const r = nearestBand(wl, 660, 260);
    const g = nearestBand(wl, 550, 260);
    const b = nearestBand(wl, 460, 260);
    const nir = nearestBand(wl, 840, 260);
    if (kind === 'false') {
      return [fallback(nir ?? n - 1), fallback(r ?? 2), fallback(g ?? 1)];
    }
    return [fallback(r ?? 2), fallback(g ?? 1), fallback(b ?? 0)];
  }
  if (kind === 'false') return [fallback(n - 1), fallback(n >= 3 ? n - 3 : 0), fallback(1)];
  return [fallback(n >= 3 ? 2 : 0), fallback(n >= 2 ? 1 : 0), fallback(0)];
}

export function bandLabel(image, index1) {
  const wl = image?.wavelengths || [];
  const i = index1 - 1;
  if (wl.length === Number(image?.bands) && wl[i] !== undefined && wl[i] !== null) {
    const unit = image?.wavelength_unit || 'nm';
    const val = Number(wl[i]);
    return `波段 ${index1} · ${Number.isInteger(val) ? val : val.toFixed(1)} ${unit}`;
  }
  return `波段 ${index1}`;
}

export function bandShort(image, index1) {
  const wl = image?.wavelengths || [];
  const i = index1 - 1;
  if (wl.length === Number(image?.bands) && wl[i] !== undefined) return `${Math.round(Number(wl[i]))}nm`;
  return `B${index1}`;
}

export function resolutionText(image) {
  const nx = Number(image?.res_x);
  const ny = Number(image?.res_y);
  if (!nx || !ny) return '—';
  const fmt = (v) => (v < 1 ? v.toFixed(2) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, ''));
  return `${fmt(nx)} × ${fmt(ny)} m`;
}

export function classColorFromResult(result, key) {
  const item = (result?.classes || []).find((c) => c.key === key)
    || (result?.class_stats || []).find((c) => c.key === key);
  return item?.color || '#6a7c6f';
}

export function classMetaFromResult(result, key) {
  return (result?.classes || []).find((c) => c.key === key)
    || (result?.class_stats || []).find((c) => c.key === key)
    || null;
}

export function downloadUrl(url) {
  const a = document.createElement('a');
  a.href = url;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function parseNumberList(text) {
  if (Array.isArray(text)) return text.map(Number).filter((v) => Number.isFinite(v));
  return String(text || '')
    .replace(/[，；;]/g, ',')
    .split(/[\s,]+/)
    .map((s) => Number(s))
    .filter((v) => Number.isFinite(v));
}

export function parseRanges(text) {
  const ranges = [];
  String(text || '').split(/[，,;；]/).forEach((part) => {
    const nums = part.match(/-?\d+(?:\.\d+)?/g);
    if (nums && nums.length >= 2) {
      const a = Number(nums[0]);
      const b = Number(nums[1]);
      if (Number.isFinite(a) && Number.isFinite(b) && b >= a) ranges.push([a, b]);
    }
  });
  return ranges;
}

export function rangesToText(ranges) {
  if (!Array.isArray(ranges)) return '';
  return ranges.map((r) => (Array.isArray(r) ? `${r[0]}-${r[1]}` : '')).filter(Boolean).join(', ');
}

export function debounce(fn, delay = 250) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

export function todayISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function isDateString(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(String(value || ''));
}

export function jobStatusMeta(status) {
  const map = {
    queued: { label: '排队中', cls: 'gray' },
    running: { label: '处理中', cls: 'amber' },
    completed: { label: '已完成', cls: 'green' },
    failed: { label: '失败', cls: 'red' },
  };
  return map[status] || { label: status || '未知', cls: 'gray' };
}

export function jobTypeLabel(type) {
  return {
    preprocess: '数据预处理',
    inversion: '模型反演',
    batch_inversion: '批量反演',
    demo: '示例项目',
  }[type] || '处理任务';
}
