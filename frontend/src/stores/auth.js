import { reactive } from 'vue'

const savedToken = localStorage.getItem('mk_token')
const savedUser = localStorage.getItem('mk_user')

export const authState = reactive({
  token: savedToken || '',
  user: savedUser ? JSON.parse(savedUser) : null
})

export function setAuth(token, user) {
  authState.token = token
  authState.user = user
  localStorage.setItem('mk_token', token)
  localStorage.setItem('mk_user', JSON.stringify(user))
}

export function clearAuth() {
  authState.token = ''
  authState.user = null
  localStorage.removeItem('mk_token')
  localStorage.removeItem('mk_user')
}

export function isTokenExpired(token = authState.token) {
  if (!token) return true
  try {
    const part = token.split('.')[1]
    if (!part) return true
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'))
    const payload = JSON.parse(json)
    if (!payload.exp) return false
    return payload.exp * 1000 <= Date.now()
  } catch {
    return true
  }
}
