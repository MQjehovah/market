export const TYPE_LABELS = {
  agent: 'Agent',
  tool: '工具',
  skill: '技能',
  mcp: 'MCP',
  workflow: '工作流'
}

export const STATUS_LABELS = {
  draft: '草稿',
  reviewing: '待审',
  published: '正式版',
  deprecated: '已弃用',
  archived: '已归档',
  rejected: '已驳回',
  returned: '已打回'
}

export const STATUS_BADGE = {
  draft: 'badge',
  reviewing: 'badge-warning',
  published: 'badge-success',
  deprecated: 'badge-warning',
  archived: 'badge',
  rejected: 'badge-danger',
  returned: 'badge-danger'
}

export const VISIBILITY_LABELS = {
  private: '私有',
  team: '团队',
  internal: '内部',
  public: '公开'
}

export const TYPE_CATEGORIES = {
  agent: ['开发助手类', '运维管理类', '业务分析类', '客服支持类', '通用助手类'],
  tool: ['文件操作', '数据查询', 'API调用', '代码分析', '文档处理', '消息通知', '系统管理', '安全审计'],
  skill: ['开发流程', '测试', '文档', '数据分析', '通用效率', '沟通协作'],
  mcp: ['数据库连接', 'DevOps工具', '项目管理', '消息通知', '数据分析', '内部系统']
}

export function formatDate(value) {
  if (!value) return '-'
  const d = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function stars(score) {
  return '★'.repeat(Math.round(score || 0)) + '☆'.repeat(5 - Math.round(score || 0))
}
