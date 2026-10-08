import { reactive } from 'vue'

let seq = 0
export const toasts = reactive([])

export function dismissToast(id) {
  const i = toasts.findIndex((t) => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

export function toast(message, type = 'success', ms = 2800) {
  const text = String(message || '').trim()
  if (!text) return 0
  const id = ++seq
  toasts.push({ id, message: text, type })
  if (ms > 0) setTimeout(() => dismissToast(id), ms)
  return id
}

toast.success = (message, ms) => toast(message, 'success', ms)
toast.error = (message, ms) => toast(message, 'error', ms ?? 4000)
