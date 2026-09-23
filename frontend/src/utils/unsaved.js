import { onBeforeUnmount, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

/** 编辑页离开前提示。isDirty 在路由切换和关闭页签时各读一次。 */
export function bindUnsavedGuard(isDirty) {
  function onBeforeUnload(event) {
    if (!isDirty()) return
    event.preventDefault()
    event.returnValue = ''
  }
  onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))
  onBeforeRouteLeave(() => {
    if (!isDirty()) return true
    return window.confirm('有未保存的修改，确定离开？')
  })
}
