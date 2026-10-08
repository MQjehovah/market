import { reactive } from 'vue'
import { api } from '../api'

export const notifyState = reactive({
  items: [],
  error: ''
})

export async function loadNotifications() {
  try {
    notifyState.items = (await api.get('/notifications')) || []
    notifyState.error = ''
  } catch (e) {
    notifyState.error = e.message || '通知加载失败'
  }
}

export function clearNotifications() {
  notifyState.items = []
  notifyState.error = ''
}

export async function markNoticeRead(n) {
  if (!n || n.read) return
  n.read = true
  try {
    await api.post(`/notifications/${n.id}/read`)
  } catch {
    n.read = false
  }
}

export async function markAllRead() {
  await api.post('/notifications/read-all')
  notifyState.items.forEach((n) => {
    n.read = true
  })
}
