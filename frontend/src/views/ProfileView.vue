<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { authState, clearAuth, setAuth } from '../stores/auth'
import { notifyState, loadNotifications, clearNotifications, markAllRead, markNoticeRead } from '../stores/notifications'
import { TYPE_LABELS, formatDate, roleLabel } from '../utils/format'

const router = useRouter()
const stats = ref(null)
const statsError = ref('')
const passwordError = ref('')
const passwordBusy = ref(false)
const password = reactive({ old: '', next: '', again: '' })
const user = computed(() => authState.user)

async function load() {
  try {
    const me = await api.get('/auth/me')
    if (authState.token) setAuth(authState.token, me)
  } catch {
    /* 保留登录时记下的资料 */
  }
  await loadNotifications()
  statsError.value = ''
  try {
    stats.value = await api.get('/admin/stats/own')
  } catch (e) {
    stats.value = null
    statsError.value = e.message || '我的发布加载失败'
  }
}

async function openNotice(n) {
  if (!n.read) await markNoticeRead(n)
  if (!n.link || !n.link.startsWith('/') || n.link.startsWith('//')) return
  router.push(n.link)
}

async function readAll() {
  try {
    await markAllRead()
  } catch (e) {
    notifyState.error = e.message || '全部已读失败'
  }
}

async function changePassword() {
  passwordError.value = ''
  if (!password.old || !password.next) {
    passwordError.value = '请填写原密码和新密码'
    return
  }
  if (password.next.length < 6) {
    passwordError.value = '新密码至少 6 位'
    return
  }
  if (password.next !== password.again) {
    passwordError.value = '两次输入的新密码不一致'
    return
  }
  passwordBusy.value = true
  try {
    await api.post('/auth/change-password', {
      old_password: password.old,
      new_password: password.next
    })
    clearAuth()
    clearNotifications()
    router.push('/login')
  } catch (e) {
    passwordError.value = e.message || '修改失败'
  } finally {
    passwordBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="profile-grid">
    <div>
      <div class="panel">
        <h2>个人信息</h2>
        <table class="table">
          <tbody>
            <tr><td class="muted" style="width: 120px">用户名</td><td>{{ user?.username || '—' }}</td></tr>
            <tr><td class="muted">姓名</td><td>{{ user?.name || '—' }}</td></tr>
            <tr v-if="user?.work_id"><td class="muted">工号</td><td>{{ user.work_id }}</td></tr>
            <tr><td class="muted">邮箱</td><td>{{ user?.email || '—' }}</td></tr>
            <tr v-if="user?.phone"><td class="muted">手机</td><td>{{ user.phone }}</td></tr>
            <tr><td class="muted">角色</td><td>{{ roleLabel(user?.role) }}</td></tr>
            <tr><td class="muted">部门</td><td>{{ user?.department || '—' }}</td></tr>
            <tr><td class="muted">注册时间</td><td>{{ formatDate(user?.created_at) }}</td></tr>
          </tbody>
        </table>
      </div>

      <div class="panel mt-24">
        <h2>登录密码</h2>
        <p class="muted" style="font-size: 13px; margin: 0 0 12px">
          用企业统一登录的账号，密码在统一登录里改。这里只改本站密码，改完需要重新登录。
        </p>
        <div class="field">
          <label>原密码</label>
          <input v-model="password.old" class="input" type="password" autocomplete="current-password" />
        </div>
        <div class="field mt-12">
          <label>新密码</label>
          <input v-model="password.next" class="input" type="password" autocomplete="new-password" />
        </div>
        <div class="field mt-12">
          <label>再输入一次</label>
          <input v-model="password.again" class="input" type="password" autocomplete="new-password" />
        </div>
        <div v-if="passwordError" class="alert alert-error mt-12">{{ passwordError }}</div>
        <button class="btn btn-primary mt-12" type="button" :disabled="passwordBusy" @click="changePassword">
          {{ passwordBusy ? '保存中…' : '修改密码' }}
        </button>
      </div>

      <div class="panel mt-24">
        <h2>共享凭据</h2>
        <p class="muted" style="font-size: 13px; margin: 0 0 12px">
          这里管的是多个能力都能用的凭据。某一个能力专用的，到那个能力的详情里配。
        </p>
        <router-link to="/my/secrets" class="btn btn-sm">管理共享凭据</router-link>
      </div>

      <div class="panel mt-24">
        <h2>我的发布</h2>
        <div v-if="statsError" class="alert alert-error">{{ statsError }}</div>
        <template v-else-if="stats">
          <div class="grid grid-4 mt-16">
            <div class="stat-box"><div class="stat-num">{{ stats.total_capabilities }}</div><div class="muted">我的能力</div></div>
            <div class="stat-box"><div class="stat-num">{{ stats.published_count }}</div><div class="muted">已上架</div></div>
            <div class="stat-box"><div class="stat-num">{{ stats.reviewing_count }}</div><div class="muted">审核中</div></div>
            <div class="stat-box"><div class="stat-num">{{ stats.total_usage }}</div><div class="muted">使用次数</div></div>
          </div>
          <p v-if="!stats.total_capabilities" class="muted mt-16" style="font-size: 13px">还没有发布过能力。</p>
          <template v-else>
            <h3 class="mt-24">类型数量</h3>
            <div v-for="t in Object.keys(stats.type_breakdown)" :key="t" class="type-bar">
              <span style="width: 130px">{{ TYPE_LABELS[t] || t }}</span>
              <div class="bar"><div class="bar-fill" :style="{ width: Math.min(100, stats.type_breakdown[t] / stats.total_capabilities * 100) + '%' }"></div></div>
              <span class="muted">{{ stats.type_breakdown[t] }}</span>
            </div>
            <template v-if="(stats.top_used || []).length">
              <h3 class="mt-24">用得最多</h3>
              <div v-for="item in stats.top_used" :key="item.id" class="top-item">
                <router-link :to="`/capabilities/${item.id}`">{{ item.display_name || item.name }}</router-link>
                <span class="badge">{{ TYPE_LABELS[item.type] || item.type }}</span>
                <span class="muted">{{ item.count }} 次</span>
              </div>
            </template>
          </template>
        </template>
      </div>
    </div>

    <div class="panel">
      <div class="flex-between">
        <h2>通知</h2>
        <button v-if="notifyState.items.some((n) => !n.read)" class="btn btn-sm" type="button" @click="readAll">全部已读</button>
      </div>
      <div v-if="notifyState.error" class="alert alert-error">{{ notifyState.error }}</div>
      <div v-else-if="notifyState.items.length === 0" class="empty" style="padding: 24px 0">暂无通知</div>
      <button
        v-for="n in notifyState.items"
        :key="n.id"
        type="button"
        class="notify-item"
        :class="{ unread: !n.read, link: !!n.link }"
        @click="openNotice(n)"
      >
        <div>{{ n.title }}</div>
        <div class="muted" style="font-size: 12px">{{ n.body }}</div>
        <div class="muted" style="font-size: 11px">{{ formatDate(n.created_at) }}</div>
      </button>
    </div>
  </div>
</template>

<style scoped>
h2 { margin-top: 0; }
.profile-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; align-items: start; }
.stat-box { text-align: center; padding: 16px; background: var(--panel-2); border-radius: 10px; }
.stat-num { font-size: 26px; font-weight: 700; }
.type-bar { display: flex; align-items: center; gap: 12px; margin: 8px 0; font-size: 13px; }
.bar { flex: 1; height: 8px; background: var(--panel-2); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--primary); border-radius: 4px; }
.top-item { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.notify-item { display: block; width: 100%; text-align: left; padding: 10px 0; border: none; border-bottom: 1px solid var(--border); background: transparent; color: inherit; }
.notify-item.link { cursor: pointer; }
.notify-item.link:hover { color: var(--primary); }
.notify-item:last-child { border-bottom: none; }
.notify-item.unread { font-weight: 650; border-left: 3px solid var(--primary); padding-left: 8px; }
@media (max-width: 800px) {
  .profile-grid { grid-template-columns: 1fr; }
}
</style>
