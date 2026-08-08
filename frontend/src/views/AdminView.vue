<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { TYPE_LABELS, formatDate, formatSize } from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'

const tab = ref('review')
const reviewQueue = ref([])
const allCaps = ref([])
const users = ref([])
const stats = ref(null)
const error = ref('')
const reviewComment = ref('')

async function load() {
  error.value = ''
  try {
    const [queue, caps, userList, stat] = await Promise.all([
      api.get('/admin/capabilities?status_filter=reviewing'),
      api.get('/admin/capabilities'),
      api.get('/admin/users'),
      api.get('/admin/stats')
    ])
    reviewQueue.value = queue
    allCaps.value = caps
    users.value = userList
    stats.value = stat
  } catch (e) {
    error.value = e.message
  }
}

async function review(cap, action) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/review`, { action, comment: reviewComment.value })
    reviewComment.value = ''
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function statusAction(cap, action) {
  try {
    await api.post(`/admin/capabilities/${cap.id}/${action}`)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function updateUser(user, patch) {
  try {
    await api.patch(`/admin/users/${user.id}`, patch)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2 style="margin-top: 0">管理后台</h2>
    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'review' }" @click="tab = 'review'">
        审核队列 <span v-if="reviewQueue.length" class="tab-count">{{ reviewQueue.length }}</span>
      </button>
      <button class="tab" :class="{ active: tab === 'caps' }" @click="tab = 'caps'">能力管理</button>
      <button class="tab" :class="{ active: tab === 'users' }" @click="tab = 'users'">用户管理</button>
      <button class="tab" :class="{ active: tab === 'stats' }" @click="tab = 'stats'">平台统计</button>
    </div>

    <div v-if="error" class="alert alert-error mt-16">{{ error }}</div>

    <div v-if="tab === 'review'" class="panel mt-16">
      <div v-if="reviewQueue.length === 0" class="empty">没有待审核的能力</div>
      <div v-for="cap in reviewQueue" :key="cap.id" class="review-card">
        <div class="flex-between flex-wrap">
          <div>
            <router-link :to="`/capabilities/${cap.id}`" class="review-name">{{ cap.name }}</router-link>
            <span class="badge ml-8">{{ TYPE_LABELS[cap.type] }}</span>
            <span class="badge">v{{ cap.version }}</span>
          </div>
          <span class="muted">作者 {{ cap.author_name }} · {{ cap.organization }}</span>
        </div>
        <p class="muted">{{ cap.description || '无描述' }}</p>
        <div class="flex flex-wrap">
          <span v-for="t in cap.tags" :key="t" class="badge">{{ t }}</span>
          <span class="badge badge-primary">{{ cap.visibility }}</span>
        </div>
        <div class="flex mt-16">
          <input v-model="reviewComment" class="input" style="max-width: 360px" placeholder="审核意见（可选）" />
          <button class="btn btn-success btn-sm" @click="review(cap, 'approve')">通过并发布</button>
          <button class="btn btn-danger btn-sm" @click="review(cap, 'reject')">驳回</button>
          <button class="btn btn-sm" @click="review(cap, 'return')">打回修改</button>
        </div>
      </div>
    </div>

    <div v-if="tab === 'caps'" class="panel mt-16">
      <table class="table">
        <thead>
          <tr><th>能力</th><th>市场</th><th>版本</th><th>状态</th><th>作者</th><th>使用量</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="cap in allCaps" :key="cap.id">
            <td><router-link :to="`/capabilities/${cap.id}`">{{ cap.name }}</router-link></td>
            <td>{{ TYPE_LABELS[cap.type] }}</td>
            <td>v{{ cap.version }}</td>
            <td><StatusBadge :status="cap.status" /></td>
            <td>{{ cap.author_name }}</td>
            <td>{{ cap.usage_count }}</td>
            <td>
              <div class="flex">
                <button v-if="cap.status === 'published'" class="btn btn-sm" @click="statusAction(cap, 'deprecate')">弃用</button>
                <button v-if="['published', 'deprecated', 'rejected', 'returned'].includes(cap.status)" class="btn btn-sm btn-danger" @click="statusAction(cap, 'archive')">归档</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab === 'users'" class="panel mt-16">
      <table class="table">
        <thead>
          <tr><th>用户</th><th>邮箱</th><th>组织 / 团队</th><th>角色</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td>{{ u.display_name || u.username }} <span class="muted">@{{ u.username }}</span></td>
            <td>{{ u.email }}</td>
            <td>{{ u.organization || '-' }} / {{ u.team || '-' }}</td>
            <td>
              <select :value="u.role" class="select" style="width: auto; padding: 4px 8px" @change="updateUser(u, { role: $event.target.value })">
                <option value="admin">管理员</option>
                <option value="publisher">发布者</option>
                <option value="user">普通用户</option>
              </select>
            </td>
            <td>
              <span class="badge" :class="u.is_active ? 'badge-success' : 'badge-danger'">{{ u.is_active ? '正常' : '禁用' }}</span>
            </td>
            <td>
              <button class="btn btn-sm" @click="updateUser(u, { is_active: !u.is_active })">{{ u.is_active ? '禁用' : '启用' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="tab === 'stats' && stats" class="mt-16">
      <div class="grid grid-4">
        <div class="panel stat-box"><div class="stat-num">{{ stats.total_capabilities }}</div><div class="muted">能力总数</div></div>
        <div class="panel stat-box"><div class="stat-num" style="color: var(--success)">{{ stats.published_count }}</div><div class="muted">已上架</div></div>
        <div class="panel stat-box"><div class="stat-num" style="color: var(--warning)">{{ stats.reviewing_count }}</div><div class="muted">待审核</div></div>
        <div class="panel stat-box"><div class="stat-num" style="color: var(--primary)">{{ stats.total_usage }}</div><div class="muted">总使用量</div></div>
      </div>
      <div class="panel mt-16">
        <h3>市场分布</h3>
        <div v-for="t in Object.keys(stats.type_breakdown)" :key="t" class="type-bar">
          <span style="width: 130px">{{ TYPE_LABELS[t] }}</span>
          <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, stats.type_breakdown[t] / stats.total_capabilities * 100) + '%' }"></div></div>
          <span class="muted">{{ stats.type_breakdown[t] }}</span>
        </div>
      </div>
      <div class="grid mt-16" style="grid-template-columns: 1fr 1fr">
        <div class="panel">
          <h3>热门能力 TOP5</h3>
          <div v-for="item in stats.top_used" :key="item.id" class="top-item">
            <router-link :to="`/capabilities/${item.id}`">{{ item.name }}</router-link>
            <span class="badge">{{ item.type }}</span>
            <span class="muted">{{ item.count }} 次</span>
          </div>
        </div>
        <div class="panel">
          <h3>最近使用</h3>
          <div v-for="(item, i) in stats.recent_usage" :key="i" class="recent-item">
            <div>{{ item.capability }}</div>
            <div class="muted" style="font-size: 12px">{{ item.action }} · {{ formatDate(item.at) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); }
.tab {
  background: none; border: none; color: var(--muted); padding: 10px 16px; cursor: pointer;
  border-bottom: 2px solid transparent; font-size: 14px;
}
.tab.active { color: var(--primary); border-bottom-color: var(--primary); }
.tab-count {
  display: inline-flex; min-width: 18px; height: 18px; border-radius: 9px; align-items: center;
  justify-content: center; background: var(--danger); color: #fff; font-size: 11px; margin-left: 4px;
}
.review-card { padding: 16px 0; border-bottom: 1px solid var(--border); }
.review-card:last-child { border-bottom: none; }
.review-name { font-size: 16px; font-weight: 600; margin-right: 8px; }
.ml-8 { margin-left: 8px; }
.stat-box { text-align: center; }
.stat-num { font-size: 28px; font-weight: 700; }
.type-bar { display: flex; align-items: center; gap: 12px; margin: 10px 0; font-size: 13px; }
.bar { flex: 1; height: 8px; background: var(--panel-2); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #7a5cff); border-radius: 4px; }
.top-item, .recent-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
h3 { margin: 0 0 12px; }
</style>
