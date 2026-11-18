/* Canvas 影像视图：缩放/漫游/像素查询/框选，以及卷帘对比 */
import { AppIcon } from './icons.js';
import { clamp } from '../utils.js';

const { ref, reactive, watch, onMounted, onBeforeUnmount, nextTick } = window.Vue;

function attachResize(el, cb) {
  if (!el || typeof ResizeObserver === 'undefined') return null;
  const ro = new ResizeObserver(() => cb());
  ro.observe(el);
  return ro;
}

export const ImageCanvas = {
  name: 'ImageCanvas',
  components: { AppIcon },
  props: {
    src: { type: String, default: '' },
    naturalWidth: { type: Number, default: 0 },
    naturalHeight: { type: Number, default: 0 },
    mode: { type: String, default: 'point' }, // point | region
    fitPadding: { type: Number, default: 0.97 },
    emptyText: { type: String, default: '暂无可显示的影像' },
    accent: { type: String, default: '#7dd08d' },
  },
  emits: ['pixel', 'region', 'loaded', 'error'],
  setup(props, { emit, expose }) {
    const wrap = ref(null);
    const canvas = ref(null);
    const state = reactive({ loading: false, failed: false, ready: false, zoom: 1 });

    let img = null;
    let imgW = 0;
    let imgH = 0;
    let fitScale = 1;
    let scale = 1;
    let ox = 0;
    let oy = 0;
    let cw = 0;
    let ch = 0;
    let dpr = 1;
    let dragging = false;
    let dragMode = null;
    let startX = 0;
    let startY = 0;
    let lastX = 0;
    let lastY = 0;
    let moved = false;
    let box = null;
    let marker = null;
    let raf = null;
    let ro = null;

    function ctx() { return canvas.value ? canvas.value.getContext('2d') : null; }

    function scheduleDraw() {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = null; draw(); });
    }

    function fitToView() {
      if (!cw || !ch || !imgW || !imgH) return;
      fitScale = Math.min(cw / imgW, ch / imgH) * props.fitPadding;
      scale = fitScale;
      ox = (cw - imgW * scale) / 2;
      oy = (ch - imgH * scale) / 2;
      state.zoom = 1;
      scheduleDraw();
    }

    function draw() {
      const c = ctx();
      if (!c) return;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, cw, ch);
      c.fillStyle = '#0d1411';
      c.fillRect(0, 0, cw, ch);
      if (img && state.ready) {
        c.imageSmoothingEnabled = scale < fitScale * 1.4;
        c.imageSmoothingQuality = 'high';
        c.drawImage(img, ox, oy, imgW * scale, imgH * scale);
        c.strokeStyle = 'rgba(255,255,255,.08)';
        c.lineWidth = 1;
        c.strokeRect(ox + 0.5, oy + 0.5, imgW * scale - 1, imgH * scale - 1);
      }
      if (box) {
        const x = Math.min(box.x0, box.x1);
        const y = Math.min(box.y0, box.y1);
        const w = Math.abs(box.x1 - box.x0);
        const h = Math.abs(box.y1 - box.y0);
        c.fillStyle = 'rgba(125,208,141,.14)';
        c.strokeStyle = props.accent;
        c.lineWidth = 1.5;
        c.setLineDash([5, 4]);
        c.fillRect(x, y, w, h);
        c.strokeRect(x, y, w, h);
        c.setLineDash([]);
      }
      if (marker && state.ready) {
        const mx = ox + marker.x * scale;
        const my = oy + marker.y * scale;
        c.strokeStyle = props.accent;
        c.lineWidth = 1.4;
        c.beginPath();
        c.moveTo(mx - 7, my); c.lineTo(mx + 7, my);
        c.moveTo(mx, my - 7); c.lineTo(mx, my + 7);
        c.stroke();
        c.beginPath();
        c.arc(mx, my, 4, 0, Math.PI * 2);
        c.stroke();
      }
    }

    function resize() {
      if (!wrap.value || !canvas.value) return;
      const rect = wrap.value.getBoundingClientRect();
      cw = Math.max(1, Math.floor(rect.width));
      ch = Math.max(1, Math.floor(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.value.width = Math.floor(cw * dpr);
      canvas.value.height = Math.floor(ch * dpr);
      canvas.value.style.width = cw + 'px';
      canvas.value.style.height = ch + 'px';
      if (img && imgW && imgH) {
        const prevFit = fitScale;
        fitToView();
        if (prevFit && scale !== prevFit) { /* 重新适配 */ }
      } else {
        draw();
      }
    }

    function loadImage(src) {
      state.failed = false;
      marker = null;
      box = null;
      if (!src) {
        img = null;
        state.ready = false;
        state.loading = false;
        draw();
        return;
      }
      state.loading = true;
      state.ready = false;
      const im = new Image();
      im.onload = () => {
        img = im;
        imgW = props.naturalWidth || im.naturalWidth;
        imgH = props.naturalHeight || im.naturalHeight;
        state.loading = false;
        state.ready = true;
        fitToView();
        emit('loaded', { width: imgW, height: imgH });
      };
      im.onerror = () => {
        state.loading = false;
        state.failed = true;
        state.ready = false;
        emit('error');
        draw();
      };
      im.src = src;
    }

    function pointerPos(e) {
      const rect = canvas.value.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    function imagePoint(px, py) {
      const x = Math.floor((px - ox) / scale);
      const y = Math.floor((py - oy) / scale);
      if (x < 0 || y < 0 || x >= imgW || y >= imgH) return null;
      return { x, y };
    }

    function emitPixel(p) {
      const pt = imagePoint(p.x, p.y);
      if (!pt) return;
      marker = pt;
      scheduleDraw();
      emit('pixel', pt);
    }

    function onDown(e) {
      if (!state.ready) return;
      if (e.button !== undefined && e.button !== 0) return;
      const p = pointerPos(e);
      try { canvas.value.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      dragging = true;
      moved = false;
      startX = lastX = p.x;
      startY = lastY = p.y;
      if (props.mode === 'region') {
        dragMode = 'box';
        box = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
      } else {
        dragMode = 'pan';
      }
      scheduleDraw();
    }

    function onMove(e) {
      if (!dragging) return;
      const p = pointerPos(e);
      const dx = p.x - lastX;
      const dy = p.y - lastY;
      if (Math.abs(p.x - startX) + Math.abs(p.y - startY) > 4) moved = true;
      if (dragMode === 'pan') {
        ox += dx;
        oy += dy;
      } else if (dragMode === 'box') {
        box = { x0: startX, y0: startY, x1: p.x, y1: p.y };
      }
      lastX = p.x;
      lastY = p.y;
      scheduleDraw();
    }

    function onUp(e) {
      if (!dragging) return;
      dragging = false;
      const p = pointerPos(e);
      try { canvas.value.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      if (dragMode === 'box' && box) {
        const x0c = Math.min(box.x0, box.x1);
        const y0c = Math.min(box.y0, box.y1);
        const x1c = Math.max(box.x0, box.x1);
        const y1c = Math.max(box.y0, box.y1);
        const x0 = Math.floor((x0c - ox) / scale);
        const y0 = Math.floor((y0c - oy) / scale);
        const x1 = Math.ceil((x1c - ox) / scale);
        const y1 = Math.ceil((y1c - oy) / scale);
        const w = clamp(x1 - x0, 0, imgW);
        const h = clamp(y1 - y0, 0, imgH);
        box = null;
        if (moved && w >= 2 && h >= 2) {
          const region = {
            x: Math.round(clamp(x0, 0, imgW - 1) + w / 2),
            y: Math.round(clamp(y0, 0, imgH - 1) + h / 2),
            w,
            h,
          };
          marker = { x: region.x, y: region.y };
          emit('region', region);
        } else if (!moved) {
          emitPixel(p);
        }
      } else if (dragMode === 'pan' && !moved) {
        emitPixel(p);
      }
      dragMode = null;
      scheduleDraw();
    }

    function onWheel(e) {
      if (!state.ready) return;
      const p = pointerPos(e);
      const factor = e.deltaY < 0 ? 1.14 : 0.878;
      const next = clamp(scale * factor, fitScale * 0.55, fitScale * 22);
      const k = next / scale;
      ox = p.x - (p.x - ox) * k;
      oy = p.y - (p.y - oy) * k;
      scale = next;
      state.zoom = fitScale ? scale / fitScale : 1;
      scheduleDraw();
    }

    function zoomBy(factor) {
      if (!state.ready) return;
      const px = cw / 2;
      const py = ch / 2;
      const next = clamp(scale * factor, fitScale * 0.55, fitScale * 22);
      const k = next / scale;
      ox = px - (px - ox) * k;
      oy = py - (py - oy) * k;
      scale = next;
      state.zoom = fitScale ? scale / fitScale : 1;
      scheduleDraw();
    }

    function zoomIn() { zoomBy(1.25); }
    function zoomOut() { zoomBy(0.8); }
    function reset() { fitToView(); marker = null; box = null; scheduleDraw(); }
    function clearMarks() { marker = null; box = null; scheduleDraw(); }
    function markAt(x, y) { marker = { x, y }; scheduleDraw(); }

    watch(() => props.src, loadImage, { immediate: true });
    watch(() => [props.naturalWidth, props.naturalHeight], () => {
      if (img) {
        imgW = props.naturalWidth || img.naturalWidth;
        imgH = props.naturalHeight || img.naturalHeight;
        fitToView();
      }
    });
    onMounted(() => {
      resize();
      ro = attachResize(wrap.value, resize);
      window.addEventListener('resize', resize);
    });
    onBeforeUnmount(() => {
      if (ro) ro.disconnect();
      window.removeEventListener('resize', resize);
      if (raf) cancelAnimationFrame(raf);
    });

    expose({ zoomIn, zoomOut, reset, clearMarks, markAt });

    return {
      wrap, canvas, state, onDown, onMove, onUp, onWheel, reset,
    };
  },
  template: `
  <div class="image-canvas-wrap" :class="{ 'is-region': mode === 'region' }" ref="wrap"
       @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
       @pointerleave="dragging && onUp($event)" @wheel.prevent="onWheel" @dblclick="reset">
    <canvas ref="canvas"></canvas>
    <div class="canvas-loading" v-if="state.loading"><span class="spinner light"></span>正在载入影像…</div>
    <div class="canvas-empty" v-if="!src"><app-icon name="image" :size="34"/><span>{{ emptyText }}</span></div>
    <div class="canvas-empty" v-if="state.failed"><app-icon name="alert-circle" :size="34"/><span>影像载入失败，请稍后重试</span></div>
  </div>`,
};

export const CompareCanvas = {
  name: 'CompareCanvas',
  components: { AppIcon },
  props: {
    srcA: { type: String, default: '' },
    srcB: { type: String, default: '' },
    labelA: { type: String, default: '影像 A' },
    labelB: { type: String, default: '影像 B' },
    fitPadding: { type: Number, default: 0.97 },
  },
  emits: ['loaded'],
  setup(props, { emit, expose }) {
    const wrap = ref(null);
    const canvas = ref(null);
    const state = reactive({ loading: false, failed: false, ready: false, zoom: 1 });
    let imgA = null;
    let imgB = null;
    let imgW = 0;
    let imgH = 0;
    let fitScale = 1;
    let scale = 1;
    let ox = 0;
    let oy = 0;
    let cw = 0;
    let ch = 0;
    let dpr = 1;
    let dividerRatio = 0.5;
    let dragging = false;
    let dragMode = null;
    let lastX = 0;
    let lastY = 0;
    let raf = null;
    let ro = null;

    const ctx = () => (canvas.value ? canvas.value.getContext('2d') : null);
    function scheduleDraw() {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = null; draw(); });
    }
    function fitToView() {
      if (!cw || !ch || !imgW || !imgH) return;
      fitScale = Math.min(cw / imgW, ch / imgH) * props.fitPadding;
      scale = fitScale;
      ox = (cw - imgW * scale) / 2;
      oy = (ch - imgH * scale) / 2;
      state.zoom = 1;
      scheduleDraw();
    }
    function draw() {
      const c = ctx();
      if (!c) return;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, cw, ch);
      c.fillStyle = '#0d1411';
      c.fillRect(0, 0, cw, ch);
      if (state.ready && imgA) {
        c.imageSmoothingEnabled = true;
        c.drawImage(imgA, ox, oy, imgW * scale, imgH * scale);
      }
      const dx = Math.round(cw * dividerRatio);
      if (state.ready && imgB) {
        c.save();
        c.beginPath();
        c.rect(dx, 0, cw - dx, ch);
        c.clip();
        c.drawImage(imgB, ox, oy, imgW * scale, imgH * scale);
        c.restore();
      }
      if (state.ready) {
        c.strokeStyle = 'rgba(255,255,255,.9)';
        c.lineWidth = 2;
        c.beginPath();
        c.moveTo(dx, 0);
        c.lineTo(dx, ch);
        c.stroke();
        c.fillStyle = '#ffffff';
        c.beginPath();
        c.arc(dx, ch / 2, 14, 0, Math.PI * 2);
        c.fill();
        c.strokeStyle = '#1c2a21';
        c.lineWidth = 1.6;
        c.beginPath();
        c.moveTo(dx - 5, ch / 2 - 4); c.lineTo(dx - 9, ch / 2); c.lineTo(dx - 5, ch / 2 + 4);
        c.moveTo(dx + 5, ch / 2 - 4); c.lineTo(dx + 9, ch / 2); c.lineTo(dx + 5, ch / 2 + 4);
        c.stroke();
      }
    }
    function resize() {
      if (!wrap.value || !canvas.value) return;
      const rect = wrap.value.getBoundingClientRect();
      cw = Math.max(1, Math.floor(rect.width));
      ch = Math.max(1, Math.floor(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.value.width = Math.floor(cw * dpr);
      canvas.value.height = Math.floor(ch * dpr);
      canvas.value.style.width = cw + 'px';
      canvas.value.style.height = ch + 'px';
      if (imgA && imgB) fitToView(); else draw();
    }
    function loadOne(src) {
      return new Promise((resolve, reject) => {
        if (!src) { resolve(null); return; }
        const im = new Image();
        im.onload = () => resolve(im);
        im.onerror = () => reject(new Error('load failed'));
        im.src = src;
      });
    }
    async function loadAll() {
      state.failed = false;
      if (!props.srcA || !props.srcB) {
        imgA = imgB = null;
        state.ready = false;
        state.loading = false;
        draw();
        return;
      }
      state.loading = true;
      state.ready = false;
      try {
        const [a, b] = await Promise.all([loadOne(props.srcA), loadOne(props.srcB)]);
        imgA = a;
        imgB = b;
        if (a && b) {
          imgW = a.naturalWidth;
          imgH = a.naturalHeight;
          state.ready = true;
          state.loading = false;
          fitToView();
          emit('loaded');
        }
      } catch (err) {
        state.loading = false;
        state.failed = true;
        draw();
      }
    }
    function pointerPos(e) {
      const rect = canvas.value.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }
    function onDown(e) {
      if (!state.ready) return;
      const p = pointerPos(e);
      try { canvas.value.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      dragging = true;
      const dx = cw * dividerRatio;
      dragMode = Math.abs(p.x - dx) <= 16 ? 'divider' : 'pan';
      lastX = p.x;
      lastY = p.y;
    }
    function onMove(e) {
      if (!state.ready) return;
      const p = pointerPos(e);
      if (!dragging) {
        const near = Math.abs(p.x - cw * dividerRatio) <= 16;
        canvas.value.style.cursor = near ? 'col-resize' : 'grab';
        return;
      }
      if (dragMode === 'divider') {
        dividerRatio = clamp(p.x / cw, 0.04, 0.96);
      } else {
        ox += p.x - lastX;
        oy += p.y - lastY;
      }
      lastX = p.x;
      lastY = p.y;
      scheduleDraw();
    }
    function onUp(e) {
      if (!dragging) return;
      dragging = false;
      try { canvas.value.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      dragMode = null;
      canvas.value.style.cursor = 'grab';
    }
    function onWheel(e) {
      if (!state.ready) return;
      const p = pointerPos(e);
      const factor = e.deltaY < 0 ? 1.14 : 0.878;
      const next = clamp(scale * factor, fitScale * 0.55, fitScale * 22);
      const k = next / scale;
      ox = p.x - (p.x - ox) * k;
      oy = p.y - (p.y - oy) * k;
      scale = next;
      state.zoom = fitScale ? scale / fitScale : 1;
      scheduleDraw();
    }
    function zoomBy(factor) {
      if (!state.ready) return;
      const px = cw / 2;
      const py = ch / 2;
      const next = clamp(scale * factor, fitScale * 0.55, fitScale * 22);
      const k = next / scale;
      ox = px - (px - ox) * k;
      oy = py - (py - oy) * k;
      scale = next;
      state.zoom = fitScale ? scale / fitScale : 1;
      scheduleDraw();
    }
    function reset() { fitToView(); dividerRatio = 0.5; scheduleDraw(); }
    watch(() => [props.srcA, props.srcB], loadAll, { immediate: true });
    onMounted(() => {
      resize();
      ro = attachResize(wrap.value, resize);
      window.addEventListener('resize', resize);
    });
    onBeforeUnmount(() => {
      if (ro) ro.disconnect();
      window.removeEventListener('resize', resize);
      if (raf) cancelAnimationFrame(raf);
    });
    expose({ zoomIn() { zoomBy(1.25); }, zoomOut() { zoomBy(0.8); }, reset });

    return {
      wrap, canvas, state, onDown, onMove, onUp, onWheel, reset,
    };
  },
  template: `
  <div class="compare-wrap" ref="wrap" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp"
       @pointercancel="onUp" @wheel.prevent="onWheel" @dblclick="reset">
    <canvas ref="canvas"></canvas>
    <span class="compare-label a">{{ labelA }}</span>
    <span class="compare-label b">{{ labelB }}</span>
    <div class="canvas-loading" v-if="state.loading"><span class="spinner light"></span>正在载入对比影像…</div>
    <div class="canvas-empty" v-if="!state.loading && (!srcA || !srcB)"><app-icon name="columns" :size="34"/><span>请选择两幅影像进行对比</span></div>
    <div class="canvas-empty" v-if="state.failed"><app-icon name="alert-circle" :size="34"/><span>对比影像载入失败</span></div>
  </div>`,
};
