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
import { authState } from '../stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: BrowseView, meta: { title: '能力市场' } },
    { path: '/agents/:name/edit', component: AgentEditView, meta: { title: '编辑 Agent', publisher: true } },
    { path: '/skills/:name/edit', component: SkillEditView, meta: { title: '编辑技能', auth: true } },
    { path: '/tools/:name/edit', component: ToolEditView, meta: { title: '编辑工具', auth: true } },
    { path: '/mcp/:name/edit', component: McpEditView, meta: { title: '编辑 MCP', auth: true } },
    { path: '/workflows/new', component: WorkflowEditorView, meta: { title: '新建工作流', auth: true, full: true } },
    { path: '/workflows/:id/edit', component: WorkflowEditorView, props: true, meta: { title: '工作流编辑器', auth: true, full: true } },
    { path: '/login', component: LoginView, meta: { title: '登录' } },
    { path: '/register', component: RegisterView, meta: { title: '注册' } },
    { path: '/capabilities/:id', component: CapabilityDetailView, props: true, meta: { title: '能力详情' } },
    { path: '/my', component: MyCapabilitiesView, meta: { title: '我的能力', auth: true } },
    { path: '/profile', component: ProfileView, meta: { title: '个人中心', auth: true } },
    { path: '/admin', component: AdminView, meta: { title: '管理后台', auth: true, admin: true } }
  ]
})

router.beforeEach((to) => {
  if (to.meta.auth && !authState.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.publisher && authState.user && !['admin', 'publisher'].includes(authState.user.role)) {
    return '/'
  }
  if (to.meta.admin && authState.user && authState.user.role !== 'admin') {
    return '/'
  }
  document.title = `${to.meta.title || '能力层'} · AI 能力公共市场`
})

export default router
