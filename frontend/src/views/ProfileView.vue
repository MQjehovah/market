<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { authState } from '../stores/auth'
import { TYPE_LABELS, formatDate } from '../utils/format'

const stats = ref(null)
const notifications = ref([])
const user = authState.user

async function load() {
  notifications.value = await api.get('/notifications')
  if (['admin', 'publisher'].includes(user?.role)) {
    try {
      stats.value = await api.get(user?.role === 'admin' ? '/admin/stats' : '/admin/stats/own')
    } catch {
      stats.value = null
    }
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="grid" style="grid-template-columns: 1.2fr 1fr">
      <div>
        <div class="panel">
          <h2>个人信息</h2>
          <table class="table">
            <tbody>
              <tr><td class="muted" style="width: 120px">用户名</td><td>{{ user?.username }}</td></tr>
              <tr><td class="muted">显示名</td><td>{{ user?.display_name }}</td></tr>
              <tr><td class="muted">邮箱</td><td>{{ user?.email }}</td></tr>
              <tr><td class="muted">角色</td><td>{{ user?.role === 'admin' ? '市场管理员' : user?.role === 'publisher' ? '能力发布者' : '普通用户' }}</td></tr>
              <tr><td class="muted">组织 / 团队</td><td>{{ user?.organization || '-' }} / {{ user?.team || '-' }}</td></tr>
              <tr><td class="muted">注册时间</td><td>{{ formatDate(user?.created_at) }}</td></tr>
            </tbody>
          </table>
        </div>

        <div v-if="stats" class="panel mt-24">
          <h2>我的统计</h2>
          <div class="grid grid-4 mt-16">
            <div class="stat-box"><div class="stat-num">{{ stats.total_capabilities }}</div><div class="muted">能力总数</div></div>
            <div class="stat-box"><div class="stat-num" style="color: var(--success)">{{ stats.published_count }}</div><div class="muted">已上架</div></div>
            <div class="stat-box"><div class="stat-num" style="color: var(--warning)">{{ stats.reviewing_count }}</div><div class="muted">待审核</div></div>
            <div class="stat-box"><div class="stat-num" style="color: var(--primary)">{{ stats.total_usage }}</div><div class="muted">总使用量</div></div>
          </div>
          <h3 class="mt-24">使用量分布</h3>
          <div v-for="t in Object.keys(stats.type_breakdown)" :key="t" class="type-bar">
            <span style="width: 130px">{{ TYPE_LABELS[t] }}</span>
            <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, stats.type_breakdown[t] / stats.total_capabilities * 100) + '%' }"></div></div>
            <span class="muted">{{ stats.type_breakdown[t] }}</span>
          </div>
          <h3 class="mt-24">热门能力</h3>
          <div v-for="item in stats.top_used" :key="item.id" class="top-item">
            <router-link :to="`/capabilities/${item.id}`">{{ item.name }}</router-link>
            <span class="badge">{{ item.type }}</span>
            <span class="muted">{{ item.count }} 次</span>
          </div>
        </div>
      </div>

      <div class="panel">
        <h2>通知</h2>
        <div v-if="notifications.length === 0" class="empty" style="padding: 24px 0">暂无通知</div>
        <div v-for="n in notifications" :key="n.id" class="notify-item" :class="{ unread: !n.read }">
          <div>{{ n.title }}</div>
          <div class="muted" style="font-size: 12px">{{ n.body }}</div>
          <div class="muted" style="font-size: 11px">{{ formatDate(n.created_at) }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
h2 { margin-top: 0; }
.stat-box { text-align: center; padding: 16px; background: var(--panel-2); border-radius: 10px; }
.stat-num { font-size: 26px; font-weight: 700; }
.type-bar { display: flex; align-items: center; gap: 12px; margin: 8px 0; font-size: 13px; }
.bar { flex: 1; height: 8px; background: var(--panel-2); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, var(--primary), #7a5cff); border-radius: 4px; }
.top-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.notify-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.notify-item:last-child { border-bottom: none; }
.notify-item.unread { color: var(--text); }
</style>
