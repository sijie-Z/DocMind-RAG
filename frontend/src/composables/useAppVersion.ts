/**
 * 应用版本号（ISS-2 数字对账）：后端 `APP_VERSION` 是唯一真源。
 *
 * 经 GET /health 读取并做模块级缓存——一次前端会话内多处使用（关于页、聊天
 * 侧栏）只发一次请求；失败（后端不可达/未代理）保持空串，由展示层兜底
 * （宁可空态也不显示假版本），下次调用自动重试。
 */
import { ref } from 'vue'
import type { Ref } from 'vue'
import { getHealth } from '@/api/health'

const appVersion = ref('')
let inFlight: Promise<void> | null = null

export function useAppVersion(): Ref<string> {
  if (!appVersion.value && !inFlight) {
    inFlight = getHealth()
      .then((health) => {
        appVersion.value = health.version || ''
      })
      .catch(() => {
        /* 静默：版本展示失败不影响主流程 */
      })
      .finally(() => {
        inFlight = null
      })
  }
  return appVersion
}
