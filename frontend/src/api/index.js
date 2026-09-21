import { authState, clearAuth } from '../stores/auth'

const AUTH_NO_REDIRECT = new Set(['/auth/login', '/auth/register', '/auth/change-password'])
let sessionRedirecting = false

async function redirectToLogin() {
  if (sessionRedirecting) return
  sessionRedirecting = true
  clearAuth()
  try {
    const { default: router } = await import('../router')
    const current = router.currentRoute.value
    if (current.path === '/login') return
    const redirect = current.fullPath && current.fullPath !== '/login' ? current.fullPath : undefined
    await router.replace({ path: '/login', query: redirect ? { redirect } : {} })
  } finally {
    sessionRedirecting = false
  }
}

const BASE = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/api'

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function errorMessage(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== 'body').join('.') : ''
        return loc ? `${loc}: ${d.msg}` : d.msg
      })
      .filter(Boolean)
      .join('; ')
  }
  return '请求失败'
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (authState.token) {
    headers.Authorization = `Bearer ${authState.token}`
  }
  const isForm = typeof FormData !== 'undefined' && options.body instanceof FormData
  if (!isForm && !headers['Content-Type'] && !headers['content-type']) {
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(BASE + path, { ...options, headers })
  let body = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) {
    if (res.status === 401 && !AUTH_NO_REDIRECT.has(path)) {
      await redirectToLogin()
    }
    throw new ApiError(res.status, errorMessage(body?.detail), body?.detail)
  }
  return body
}

export const api = {
  get: (path) => request(path),
  post: (path, data) => request(path, { method: 'POST', body: JSON.stringify(data) }),
  put: (path, data) => request(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: (path, data) => request(path, { method: 'PATCH', body: JSON.stringify(data) }),
  delete: (path) => request(path, { method: 'DELETE' }),
  upload: (path, file, extra = {}) => {
    const form = new FormData()
    form.append('file', file)
    Object.entries(extra).forEach(([k, v]) => form.append(k, v))
    return request(path, { method: 'POST', headers: {}, body: form })
  }
}
