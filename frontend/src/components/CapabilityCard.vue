<script setup>
import { computed } from 'vue'
import {
  INSTALL_POLICY_LABELS,
  TYPE_COLORS,
  TYPE_LABELS,
  TYPE_LETTER,
  VISIBILITY_LABELS,
  DISTRIBUTION_LABELS,
  RISK_DEFAULT_LABELS,
  DISTRIBUTION_BADGE,
  RISK_DEFAULT_BADGE,
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

function isGarbageLabel(value) {
  if (!value || typeof value !== 'string') return true
  const t = value.trim()
  if (!t) return true
  if (/^\?+$/.test(t)) return true
  return false
}

const displayCategory = computed(() => {
  const c = props.cap.category
  return isGarbageLabel(c) ? '' : c
})

const displayTags = computed(() =>
  (props.cap.tags || []).filter((t) => t !== 'plugin-component' && !isGarbageLabel(t)).slice(0, 3)
)

const fromPlugin = computed(() => (props.cap.tags || []).includes('plugin-component'))
</script>

<template>
  <div class="cap-card">
    <router-link :to="`/capabilities/${cap.id}`" class="cap-body">
      <div class="card-top">
        <span class="type-icon" :style="{ color, borderColor: color + '55', background: color + '14' }">{{ letter }}</span>
        <StatusBadge :status="cap.status" />
      </div>
      <h3 class="cap-name">{{ cap.name }}</h3>
      <p class="cap-desc">{{ cap.description || '暂无描述' }}</p>
      <div class="cap-tags">
        <span
          v-if="cap.distribution"
          class="badge"
          :class="DISTRIBUTION_BADGE[cap.distribution]"
          :title="`分发方式：${DISTRIBUTION_LABELS[cap.distribution] || cap.distribution}`"
        >{{ DISTRIBUTION_LABELS[cap.distribution] || cap.distribution }}</span>
        <span
          v-if="cap.risk_default"
          class="badge"
          :class="RISK_DEFAULT_BADGE[cap.risk_default]"
          :title="`默认风险：${RISK_DEFAULT_LABELS[cap.risk_default] || cap.risk_default}`"
        >{{ RISK_DEFAULT_LABELS[cap.risk_default] || cap.risk_default }}</span>
        <span v-if="displayCategory" class="badge">{{ displayCategory }}</span>
        <span v-if="fromPlugin" class="badge badge-primary">来自能力包</span>
        <span v-if="policy !== 'optional'" class="badge badge-warning">{{ INSTALL_POLICY_LABELS[policy] || policy }}</span>
        <span v-for="t in displayTags" :key="t" class="badge">{{ t }}</span>
      </div>
      <div class="cap-meta">
        <span>
          <span v-if="shelf" class="badge badge-primary" style="margin-right: 6px">{{ shelf }}</span>
          {{ TYPE_LABELS[cap.type] }} · v{{ cap.version }}
        </span>
        <span class="rating">{{ stars(cap.avg_rating) }} <em>{{ cap.avg_rating || '暂无' }}</em></span>
      </div>
    </router-link>
    <div class="cap-foot">
      <span class="muted">{{ VISIBILITY_LABELS[cap.visibility] }} · {{ cap.usage_count }} 次使用</span>
      <div class="flex" style="gap: 8px">
        <span v-if="cap.latest" class="badge badge-primary">最新</span>
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
  display: flex; flex-direction: column; color: var(--text);
  background: #fff; border: 1px solid var(--border); border-radius: 14px;
  box-shadow: var(--shadow); transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
}
.cap-card:hover {
  transform: translateY(-3px);
  border-color: #c9d8ff;
  box-shadow: var(--shadow-lg);
}
.cap-body { color: var(--text); padding: 16px 16px 0; }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; }
.type-icon {
  width: 36px; height: 36px; border-radius: 10px; border: 1px solid;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700;
}
.cap-name { margin: 12px 0 6px; font-size: 16px; font-weight: 650; letter-spacing: -0.01em; }
.cap-desc {
  color: var(--muted); font-size: 13px; margin: 0 0 12px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 38px;
}
.cap-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; min-height: 24px; }
.cap-meta { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); padding-bottom: 12px; }
.cap-meta em { font-style: normal; color: var(--warning); }
.cap-foot {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: auto; font-size: 12px; padding: 12px 16px;
  border-top: 1px solid var(--border); background: var(--panel-2); border-radius: 0 0 14px 14px;
}
</style>
