/* 首页：项目管理 */
import { store, go, openProject, openDemo, deleteProject, notify } from '../store.js';
import { formatDate, timeAgo, projectStatusMeta } from '../utils.js';
import { AppIcon } from '../components/icons.js';
import { Modal } from '../components/ui.js';

const { ref, computed } = window.Vue;

export const HomePage = {
  name: 'HomePage',
  components: { AppIcon, Modal },
  setup() {
    const demoBusy = ref(false);
    const demoProgress = ref(0);
    const deleteTarget = ref(null);
    const deleting = ref(false);
    const failedCovers = ref(new Set());

    const recent = computed(() => store.projects.slice(0, 3));
    const stats = computed(() => ({
      projects: store.projects.length,
      images: store.projects.reduce((sum, p) => sum + (p.image_count || 0), 0),
      results: store.projects.reduce((sum, p) => sum + (p.inverted_count || 0), 0),
      models: store.models.length,
    }));

    function coverVisible(p) { return p.cover_url && !failedCovers.value.has(p.id); }
    function markCoverFailed(p) {
      const next = new Set(failedCovers.value);
      next.add(p.id);
      failedCovers.value = next;
    }
    function status(p) { return projectStatusMeta(p.status); }
    function stages(p) {
      return [
        { label: '导入', done: (p.image_count || 0) > 0 },
        { label: '预处理', done: (p.preprocessed_count || 0) > 0 },
        { label: '反演', done: (p.inverted_count || 0) > 0 },
      ];
    }
    async function open(p) {
      try {
        await openProject(p.id);
        if (p.status === 'completed' || (p.inverted_count || 0) > 0) go('results');
        else if ((p.image_count || 0) > 0) go('viewer');
        else go('import');
      } catch (e) { /* toast */ }
    }
    function newProject() { store.newProjectOpen = true; }
    async function loadDemo() {
      demoBusy.value = true;
      demoProgress.value = 0;
      try {
        await openDemo({ onUpdate: (job) => { demoProgress.value = job.progress || 0; } });
      } catch (e) { /* toast */ }
      finally {
        demoBusy.value = false;
        demoProgress.value = 0;
      }
    }
    async function doDelete() {
      if (!deleteTarget.value) return;
      deleting.value = true;
      try {
        await deleteProject(deleteTarget.value.id);
        deleteTarget.value = null;
      } catch (e) {
        notify(e.message, 'error');
      } finally {
        deleting.value = false;
      }
    }
    return {
      store, go, recent, stats, demoBusy, demoProgress, deleteTarget, deleting,
      coverVisible, markCoverFailed, status, stages, open, newProject, loadDemo, doDelete,
      openHelp: () => { store.helpOpen = true; },
      timeAgo, formatDate,
    };
  },
  template: `
  <div class="page home-page">
    <section class="home-hero">
      <div class="hero-copy">
        <div class="hero-eyebrow"><span class="dot"></span>Nmass · FARMLAND NITROGEN</div>
        <h1>把光谱影像，<br>变成看得懂的氮素分布。</h1>
        <p class="hero-sub">面向农田管理的植被氮素遥感反演工具。导入无人机或卫星影像，平台自动完成反射率转换、光谱对齐、植被掩膜与零样本反演，输出分级专题图与可追溯的 PDF 报告。</p>
        <div class="hero-actions">
          <button class="btn btn-primary" @click="newProject"><app-icon name="plus" :size="16"/>新建项目</button>
          <button class="btn btn-ghost" @click="loadDemo" :disabled="demoBusy">
            <span v-if="demoBusy" class="spinner"></span>
            <app-icon v-else name="sparkles" :size="16"/>
            {{ demoBusy ? '正在准备示例 ' + demoProgress + '%' : (store.system.demo_exists ? '打开示例项目' : '载入示例项目') }}
          </button>
          <button class="btn btn-text" @click="openHelp">了解使用流程 <app-icon name="arrow-right" :size="15"/></button>
        </div>
        <div class="hero-points">
          <span class="hero-point"><app-icon name="sparkles" :size="14"/>零样本一键反演</span>
          <span class="hero-point"><app-icon name="shield" :size="14"/>数据本地处理</span>
          <span class="hero-point"><app-icon name="file-text" :size="14"/>专题图 + PDF 报告</span>
        </div>
      </div>
      <div class="hero-visual">
        <div class="hero-map-card">
          <div class="hero-map-inner">
            <svg viewBox="0 0 520 330" role="img" aria-label="氮素分级专题图示意">
              <defs>
                <linearGradient id="heroField" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stop-color="#1d5b42"/><stop offset="1" stop-color="#163a2b"/>
                </linearGradient>
                <pattern id="heroGrid" width="26" height="26" patternUnits="userSpaceOnUse">
                  <path d="M26 0H0V26" fill="none" stroke="#ffffff" stroke-opacity=".045" stroke-width="1"/>
                </pattern>
                <clipPath id="heroClip"><polygon points="38,48 484,38 492,292 28,300"/></clipPath>
              </defs>
              <rect width="520" height="330" fill="#0d1411"/>
              <rect width="520" height="330" fill="url(#heroGrid)"/>
              <g clip-path="url(#heroClip)">
                <polygon points="38,48 484,38 492,292 28,300" fill="url(#heroField)"/>
                <path d="M20 210 C110 150 180 170 250 120 C330 62 410 90 500 52 L510 170 C430 190 360 160 300 220 C230 288 120 260 20 320 Z" fill="#0072B2" opacity=".82"/>
                <path d="M30 80 C120 40 210 92 300 60 C380 34 440 70 500 40 L500 150 C420 170 350 120 270 160 C180 206 100 170 20 210 Z" fill="#009E73" opacity=".9"/>
                <path d="M40 265 C120 220 180 230 240 200 C320 160 400 230 500 190 L500 270 C410 300 340 250 260 280 C190 308 100 280 30 310 Z" fill="#D55E00" opacity=".78"/>
                <path d="M300 110 C350 96 390 105 430 88 L430 145 C380 158 340 150 300 165 Z" fill="#D55E00" opacity=".55"/>
                <ellipse cx="156" cy="142" rx="24" ry="15" fill="#dfe6df" opacity=".85"/>
                <ellipse cx="352" cy="228" rx="28" ry="17" fill="#dfe6df" opacity=".8"/>
                <g fill="none" stroke="#ffffff" stroke-opacity=".16" stroke-width="1">
                  <path d="M38 190 C140 150 230 200 330 160 S450 120 500 140"/>
                  <path d="M40 240 C150 205 230 250 330 215 S450 185 500 205"/>
                  <path d="M120 48 C110 120 130 200 110 295"/>
                  <path d="M260 42 C250 120 280 210 258 296"/>
                </g>
              </g>
              <polygon points="38,48 484,38 492,292 28,300" fill="none" stroke="#ffffff" stroke-opacity=".14"/>
            </svg>
            <span class="hero-map-label">{{ store.project ? store.project.name : 'Nmass 分级专题图 · 示意' }}</span>
            <div class="hero-map-legend">
              <span><i style="background:#D55E00"></i>低氮</span>
              <span><i style="background:#009E73"></i>适宜</span>
              <span><i style="background:#0072B2"></i>高氮</span>
            </div>
          </div>
          <div class="hero-map-meta"><span>氮素反演 · Nmass</span><span><strong>mg/g</strong> · 分级色带</span></div>
        </div>
      </div>
    </section>

    <div class="stat-strip">
      <div class="stat-tile"><div class="st-label">项目</div><div class="st-value">{{ stats.projects }}<small>个</small></div></div>
      <div class="stat-tile"><div class="st-label">已导入影像</div><div class="st-value">{{ stats.images }}<small>幅</small></div></div>
      <div class="stat-tile"><div class="st-label">已完成反演</div><div class="st-value">{{ stats.results }}<small>幅</small></div></div>
      <div class="stat-tile"><div class="st-label">可用模型</div><div class="st-value">{{ stats.models }}<small>个</small></div></div>
    </div>

    <section class="home-section" v-if="recent.length">
      <div class="section-head">
        <div>
          <h2>最近项目</h2>
          <div class="section-sub">继续上次的田块监测工作</div>
        </div>
        <div class="section-actions">
          <button class="btn btn-sm btn-ghost" @click="newProject"><app-icon name="plus" :size="14"/>新建</button>
        </div>
      </div>
      <div class="recent-row">
        <button v-for="p in recent" :key="p.id" class="recent-card" @click="open(p)">
          <img v-if="coverVisible(p)" :src="p.cover_url" class="recent-thumb" alt="" @error="markCoverFailed(p)">
          <div v-else class="recent-thumb" style="display:grid;place-items:center;color:#9ec4a5"><app-icon name="leaf" :size="24"/></div>
          <div class="recent-body">
            <div class="rn">{{ p.name }}</div>
            <div class="rm">{{ p.image_count }} 幅影像 · {{ p.status_label }} · {{ timeAgo(p.updated_at) }}</div>
          </div>
          <app-icon name="chevron-right" :size="16" class="recent-arrow"/>
        </button>
      </div>
    </section>

    <section class="home-section">
      <div class="section-head">
        <div>
          <h2>全部项目</h2>
          <div class="section-sub">按最近更新排序</div>
        </div>
        <div class="section-actions">
          <span class="tag">{{ store.projects.length }} 个项目</span>
          <button class="btn btn-sm btn-ghost" @click="openHelp"><app-icon name="help" :size="14"/>帮助</button>
        </div>
      </div>

      <div v-if="store.projects.length" class="project-grid">
        <article v-for="p in store.projects" :key="p.id" class="project-card">
          <div class="project-cover" @click="open(p)">
            <img v-if="coverVisible(p)" :src="p.cover_url" alt="" @error="markCoverFailed(p)">
            <div v-else class="cover-fallback"><app-icon name="map" :size="46"/></div>
            <span class="cover-tag">{{ p.crop || '未分类' }}{{ p.field ? ' · ' + p.field : '' }}</span>
            <span class="status-badge" :class="status(p).cls"><i class="dot"></i>{{ status(p).label }}</span>
            <span class="cover-date">{{ p.last_image_name || '尚未导入影像' }}</span>
          </div>
          <div class="project-card-body" @click="open(p)">
            <div class="project-card-title"><span class="name">{{ p.name }}</span></div>
            <div class="project-card-meta">
              <span><app-icon name="image" :size="12"/>{{ p.image_count }} 幅影像</span>
              <span><app-icon name="clock" :size="12"/>{{ timeAgo(p.updated_at) }}</span>
            </div>
            <div class="project-mini-track">
              <div v-for="s in stages(p)" :key="s.label" class="project-mini-step" :class="{ done: s.done }">
                <div class="bar"><i></i></div><span>{{ s.label }}</span>
              </div>
            </div>
          </div>
          <div class="project-card-foot">
            <span class="foot-note">{{ p.description || '本地项目 · 数据不上传' }}</span>
            <div class="foot-actions">
              <button class="btn btn-sm btn-text" @click.stop="open(p)">打开</button>
              <button class="icon-btn" @click.stop="deleteTarget = p" title="删除项目"><app-icon name="trash" :size="15"/></button>
            </div>
          </div>
        </article>
      </div>

      <div v-else class="card">
        <div class="empty">
          <div class="empty-icon"><app-icon name="leaf" :size="28"/></div>
          <h3>还没有项目</h3>
          <p>新建一个项目开始监测，或直接打开示例项目体验从影像到报告的全流程。</p>
          <div class="row" style="gap:8px">
            <button class="btn btn-primary" @click="newProject"><app-icon name="plus" :size="16"/>新建项目</button>
            <button class="btn btn-ghost" @click="loadDemo" :disabled="demoBusy"><app-icon name="sparkles" :size="16"/>载入示例项目</button>
          </div>
        </div>
      </div>
    </section>

    <section class="home-section">
      <div class="feature-grid">
        <div class="feature-card">
          <div class="feature-icon"><app-icon name="layers" :size="20"/></div>
          <div><h4>多尺度影像支持</h4><p>无人机多光谱、高光谱与卫星影像，自动解析波段波长并按模型标准对齐。</p></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><app-icon name="cpu" :size="20"/></div>
          <div><h4>零样本反演</h4><p>内置预训练模型，无需实测样本即可出图；也可导入自己的 PLSR / SVR / 随机森林模型。</p></div>
        </div>
        <div class="feature-card">
          <div class="feature-icon"><app-icon name="file-text" :size="20"/></div>
          <div><h4>可交付的报告</h4><p>带图例、比例尺的专题图与包含统计、分级面积和参数信息的 PDF 报告，一键导出。</p></div>
        </div>
      </div>
    </section>

    <Modal :open="!!deleteTarget" title="删除项目" icon="trash" @close="deleteTarget = null">
      <p style="font-size:13px;line-height:1.7">确定要删除项目 <b>「{{ deleteTarget && deleteTarget.name }}」</b> 吗？</p>
      <div class="hint-box danger" style="margin-top:12px">
        <app-icon name="alert-triangle" :size="15"/>
        <span>项目内的影像、预处理结果、反演结果与日志将一并删除，且无法恢复。</span>
      </div>
      <template #footer>
        <button class="btn btn-ghost" @click="deleteTarget = null">取消</button>
        <button class="btn btn-danger" @click="doDelete" :disabled="deleting">
          <span v-if="deleting" class="spinner"></span>{{ deleting ? '正在删除…' : '确认删除' }}
        </button>
      </template>
    </Modal>
  </div>`,
};
