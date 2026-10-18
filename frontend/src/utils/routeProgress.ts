/**
 * 全局路由进度条状态（首导航与后续导航共用）。
 * 由 router 守卫驱动；App.vue 在 router-view 之外自绘顶部细条消费——
 * Layout 尚未挂载时进度条已可见，首屏校验（/users/me）期间即有加载反馈。
 */
import { ref } from 'vue'

/** 最小可见时长（毫秒）：极快导航也保留一次可感知的反馈 */
const MIN_VISIBLE_MS = 300

export const routeProgressActive = ref(false)
let startedAt = 0
let hideTimer: ReturnType<typeof setTimeout> | null = null

export function startRouteProgress(): void {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
  if (routeProgressActive.value) return
  startedAt = Date.now()
  routeProgressActive.value = true
}

export function finishRouteProgress(): void {
  if (!routeProgressActive.value) return
  const remain = Math.max(0, MIN_VISIBLE_MS - (Date.now() - startedAt))
  hideTimer = setTimeout(() => {
    routeProgressActive.value = false
    hideTimer = null
  }, remain)
}
