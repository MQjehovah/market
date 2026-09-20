import { createRouter, createWebHistory } from 'vue-router'

import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'
import BrowseView from '../views/BrowseView.vue'
import CapabilityDetailView from '../views/CapabilityDetailView.vue'
import AgentEditView from '../views/AgentEditView.vue'
import SkillEditView from '../views/SkillEditView.vue'
import ToolEditView from '../views/ToolEditView.vue'
import McpEditView from '../views/McpEditView.vue'
import WorkflowEditorView from '../views/WorkflowEditorView.vue'
import MyCapabilitiesView from '../views/MyCapabilitiesView.vue'
import ProfileView from '../views/ProfileView.vue'
import AdminView from '../views/AdminView.vue'
import { authState, clearAuth, isTokenExpired } from '../stores/auth'

const ADMIN_TITLES = {
  review: '审核',
  listed: '上架治理',
  users: '用户管理',
  gateway: 'MCP 网关'
}

function adminLanding(to) {
  const map = {
    desk: 'review',
    review: 'review',
    caps: 'listed',
    listed: 'listed',
    users: 'users',
    gateway: 'gateway',
    stats: 'review',
    debug: 'review',
    roles: 'users'
  }
  const section = map[String(to.query.tab || '')] || 'review'
  const query = { ...to.query }
  delete query.tab
  if (String(to.query.tab) === 'debug') query.trial = '1'
  return { path: `/admin/${section}`, query }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: BrowseView, meta: { title: '能力目录' } },
    { path: '/agents/:name/edit', component: AgentEditView, meta: { title: '编辑助手', auth: true } },
    { path: '/skills/:name/edit', component: SkillEditView, meta: { title: '编辑技能', auth: true } },
    { path: '/tools/:name/edit', component: ToolEditView, meta: { title: '编辑编排函数', auth: true } },
    { path: '/mcp/:name/edit', component: McpEditView, meta: { title: '编辑连接器', auth: true } },
    { path: '/workflows/new', component: WorkflowEditorView, meta: { title: '新建能力编排', auth: true, full: true } },
    { path: '/workflows/:id/edit', component: WorkflowEditorView, props: true, meta: { title: '能力编排', auth: true, full: true } },
    { path: '/login', component: LoginView, meta: { title: '登录', blank: true } },
    { path: '/register', component: RegisterView, meta: { title: '注册', blank: true } },
    { path: '/capabilities/:id', component: CapabilityDetailView, props: true, meta: { title: '能力详情' } },
    { path: '/my', component: MyCapabilitiesView, meta: { title: '我的能力', auth: true } },
    { path: '/profile', component: ProfileView, meta: { title: '个人中心', auth: true } },
    { path: '/admin', redirect: adminLanding, meta: { title: '治理后台', auth: true, admin: true } },
    {
      path: '/admin/:section',
      component: AdminView,
      meta: { title: '治理后台', auth: true, admin: true }
    }
  ]
})

router.beforeEach((to) => {
  if (authState.token && isTokenExpired()) {
    clearAuth()
    if (to.path !== '/login') {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
  }
  // SSO 回调会带 sso_token 落到 /login，即使本地还有旧会话也要先吃掉新 token
  if (to.path === '/login' && typeof to.query.sso_token === 'string' && to.query.sso_token) {
    return true
  }
  if (authState.token && (to.path === '/login' || to.path === '/register')) {
    return typeof to.query.redirect === 'string' ? to.query.redirect : '/'
  }
  if (to.meta.auth && !authState.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.admin && authState.user && authState.user.role !== 'admin') {
    return '/'
  }
  const title = to.path.startsWith('/admin')
    ? ADMIN_TITLES[to.params.section] || '治理后台'
    : to.meta.title || '能力目录'
  document.title = `${title} · AI 能力目录`
})

export default router
