import { authState } from '../stores/auth'

const BASE = '/api'

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function request(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  if (authState.token) {
    headers.Authorization = `Bearer ${authState.token}`
  }
  const res = await fetch(BASE + path, { ...options, headers })
  let body = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : '请求失败'
    throw new ApiError(res.status, message, detail)
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
