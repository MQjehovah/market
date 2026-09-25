<script setup>
import { computed, ref, watch } from 'vue'
import {
  DISTRIBUTION_LABELS,
  INSTALL_POLICY_LABELS,
  TYPE_COLORS,
  TYPE_LABELS,
  TYPE_LETTER,
  shelfLabel,
  stars
} from '../utils/format'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  cap: { type: Object, required: true },
  inMy: { type: Boolean, default: false },
  showJoin: { type: Boolean, default: true }
})
const emit = defineEmits(['add'])

const letter = computed(() => TYPE_LETTER[props.cap.type] || '?')
const color = computed(() => TYPE_COLORS[props.cap.type] || 'var(--primary)')
const shelf = computed(() => shelfLabel(props.cap.type))
const policy = computed(() => props.cap.install_policy || 'optional')
const canRemove = computed(() => props.cap.removable !== false && policy.value !== 'required')
/** 已上架是浏览页常态，不必每卡都标；非上架状态才提示 */
const showStatus = computed(() => !['published'].includes(props.cap.status))

/** 展示名: 中文 display_name 优先(name 为标准机器名/内部标识) */
const displayName = computed(() => (props.cap.display_name || '').trim() || props.cap.name)
const isImported = computed(() => (props.cap.provenance?.origin || '') === 'mcp-registry')
const provenanceTitle = computed(() => {
  const p = props.cap.provenance || {}
  return p.registry_name ? `外部导入 · 来源 ${p.registry_name}` : '外部导入'
})
const requiresBinary = computed(() => (props.cap.requires?.binary || '').trim())

function isGarbageLabel(value) {
  if (!value || typeof value !== 'string') return true
  const t = value.trim()
  return !t || /^\?+$/.test(t)
}
const displayCategory = computed(() => (isGarbageLabel(props.cap.category) ? '' : props.cap.category))
const displayTags = computed(() =>
  (props.cap.tags || []).filter((t) => t !== 'plugin-component' && !isGarbageLabel(t)).slice(0, 3)
)

/** 部署方式（清晰中文） */
const deploy = computed(() => {
  switch (props.cap.distribution) {
    case 'remote':
      return { label: '云端部署', cls: 'badge-primary' }
    case 'local':
      return { label: '本地部署', cls: 'badge' }
    default:
      return { label: '云端 + 本地', cls: 'badge-success' }
  }
})

/** 质量分级（由评分推导，一眼扫出优质能力） */
const grade = computed(() => {
  const count = Number(props.cap.rating_count || 0)
  const avg = Number(props.cap.avg_rating || 0)
  if (!count) return { label: '新品', cls: 'badge' }
  if (avg >= 4.5) return { label: 'A · 优质', cls: 'badge-success' }
  if (avg >= 4) return { label: 'B · 良好', cls: 'badge-primary' }
  if (avg >= 3) return { label: 'C · 合格', cls: 'badge-warning' }
  return { label: '待评估', cls: 'badge' }
})

function fmtDate(v) {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`
}
const dateStr = computed(() => fmtDate(props.cap.updated_at || props.cap.created_at))
const usage = computed(() => Number(props.cap.usage_count || 0))
const ratingCount = computed(() => Number(props.cap.rating_count || 0))
const subline = computed(() => {
  const parts = [TYPE_LABELS[props.cap.type] || props.cap.type]
  if (props.cap.version) parts.push(`v${props.cap.version}`)
  return parts.join(' · ')
})

const iconFailed = ref(false)
watch(
  () => props.cap.icon_url,
  () => {
    iconFailed.value = false
  }
)
</script>

<template>
  <div class="cap-card">
    <span v-if="cap.latest" class="cap-ribbon" title="最新正式版本">最新</span>
    <router-link :to="`/capabilities/${cap.id}`" class="cap-body">
      <div class="card-head">
        <img
          v-if="cap.icon_url && !iconFailed"
          class="type-icon icon-img"
          :src="cap.icon_url"
          :alt="displayName"
          @error="iconFailed = true"
        />
        <span v-else class="type-icon" :style="{ color, borderColor: color + '55', background: color + '14' }">{{ letter }}</span>
        <div class="head-text">
          <h3 class="cap-name" :title="cap.slug ? `${displayName}（${cap.slug}）` : displayName">{{ displayName }}</h3>
          <div class="head-sub">{{ subline }}<span v-if="shelf"> · {{ shelf }}</span></div>
        </div>
        <StatusBadge v-if="showStatus" :status="cap.status" />
      </div>
      <p class="cap-desc">{{ cap.description || '暂无描述' }}</p>
      <div class="cap-tags">
        <span class="badge" :class="grade.cls">{{ grade.label }}</span>
        <span class="badge" :class="deploy.cls" :title="`分发方式：${DISTRIBUTION_LABELS[cap.distribution] || cap.distribution}`">{{ deploy.label }}</span>
        <span v-if="cap.verified" class="badge badge-success" title="管理员认证">认证</span>
        <span v-if="isImported" class="badge badge-primary" :title="provenanceTitle">外部导入</span>
        <span v-if="requiresBinary" class="badge" :title="`依赖本机 CLI：${requiresBinary}`">需 {{ requiresBinary }}</span>
        <span v-if="displayCategory" class="badge">{{ displayCategory }}</span>
        <span v-if="policy !== 'optional'" class="badge badge-warning">{{ INSTALL_POLICY_LABELS[policy] || policy }}</span>
        <span v-for="t in displayTags" :key="t" class="badge">{{ t }}</span>
      </div>
    </router-link>
    <div class="cap-foot">
      <span class="muted cap-foot-meta">
        <span v-if="dateStr">{{ dateStr }}</span>
        <span v-if="dateStr"> · </span>
        <span class="rating" :title="ratingCount ? `${ratingCount} 人评分` : '暂无评分'">{{ stars(cap.avg_rating) }} {{ cap.avg_rating || '暂无' }}<em v-if="ratingCount">（{{ ratingCount }}）</em></span>
        <span v-if="usage"> · {{ usage }} 次使用</span>
      </span>
      <div class="flex" style="gap: 8px">
        <span v-if="inMy && !canRemove" class="badge badge-warning" title="必装能力不可移除">必装</span>
        <span v-else-if="inMy" class="badge badge-success">已加入</span>
        <button
          v-else-if="showJoin"
          class="btn btn-sm btn-primary"
          type="button"
          @click="emit('add', cap)"
        >
          加入
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cap-card {
  position: relative;
  display: flex; flex-direction: column; color: var(--text);
  background: #fff; border: 1px solid var(--border); border-radius: 14px;
  box-shadow: var(--shadow); transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
  overflow: hidden;
}
.cap-card:hover {
  transform: translateY(-3px);
  border-color: #c9d8ff;
  box-shadow: var(--shadow-lg);
}
.cap-ribbon {
  position: absolute; top: 0; right: 0; z-index: 2;
  padding: 3px 10px; border-bottom-left-radius: 10px;
  font-size: 11px; font-weight: 650;
  color: #fff; background: linear-gradient(135deg, #2f6bff, #7c3aed);
}
.cap-body { color: var(--text); padding: 16px 16px 0; }
/* 图标 + 标题并排（对齐参考卡片，标题在右、不再压到下方） */
.card-head { display: flex; align-items: center; gap: 12px; }
.head-text { min-width: 0; flex: 1; }
.type-icon {
  width: 44px; height: 44px; border-radius: 12px; border: 1px solid; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 16px; font-weight: 700;
}
.icon-img { object-fit: cover; background: var(--panel-2); }
.cap-name {
  margin: 0; font-size: 16px; font-weight: 650; letter-spacing: -0.01em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.head-sub { margin-top: 3px; font-size: 12px; color: var(--muted); }
.cap-desc {
  color: var(--muted); font-size: 13px; margin: 12px 0;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 38px;
}
.cap-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; min-height: 24px; }
.cap-foot {
  display: flex; justify-content: space-between; align-items: center; gap: 8px;
  margin-top: auto; font-size: 12px; padding: 12px 16px;
  border-top: 1px solid var(--border); background: var(--panel-2);
}
.cap-foot-meta { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cap-foot .rating { color: var(--warning); }
.cap-foot .rating em { font-style: normal; color: var(--muted); margin-left: 2px; }
</style>
