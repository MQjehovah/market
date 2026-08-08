<script setup>
import { computed } from 'vue'
import { TYPE_LABELS, VISIBILITY_LABELS, stars } from '../utils/format'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({ cap: { type: Object, required: true } })

const icon = computed(() => ({ agent: '🤖', tool: '🛠️', skill: '📚', mcp: '🔌' }[props.cap.type]))
</script>

<template>
  <router-link :to="`/capabilities/${cap.id}`" class="cap-card panel">
    <div class="card-top">
      <span class="type-icon">{{ icon }}</span>
      <StatusBadge :status="cap.status" />
    </div>
    <h3 class="cap-name">{{ cap.name }}</h3>
    <p class="cap-desc">{{ cap.description || '暂无描述' }}</p>
    <div class="cap-tags">
      <span v-if="cap.category" class="badge">{{ cap.category }}</span>
      <span v-for="t in (cap.tags || []).slice(0, 3)" :key="t" class="badge">{{ t }}</span>
    </div>
    <div class="cap-meta">
      <span>{{ TYPE_LABELS[cap.type] }} · v{{ cap.version }}</span>
      <span class="rating">{{ stars(cap.avg_rating) }} <em>{{ cap.avg_rating || '暂无' }}</em></span>
    </div>
    <div class="cap-foot muted">
      <span>{{ VISIBILITY_LABELS[cap.visibility] }} · {{ cap.usage_count }} 次使用</span>
      <span v-if="cap.latest" class="badge badge-primary">最新</span>
    </div>
  </router-link>
</template>

<style scoped>
.cap-card { display: block; color: var(--text); transition: transform .15s ease, border-color .15s ease; }
.cap-card:hover { transform: translateY(-2px); border-color: var(--primary); }
.card-top { display: flex; justify-content: space-between; align-items: center; }
.type-icon { font-size: 22px; }
.cap-name { margin: 10px 0 6px; font-size: 16px; }
.cap-desc {
  color: var(--muted); font-size: 13px; margin: 0 0 12px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-height: 38px;
}
.cap-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; min-height: 24px; }
.cap-meta { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); }
.cap-meta em { font-style: normal; color: var(--warning); }
.cap-foot { display: flex; justify-content: space-between; margin-top: 10px; font-size: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
</style>
