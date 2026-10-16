/**
 * monitoring.* i18n 四语完整性判别用例（UI 验收 round2 · C_polish §2.2 + A_sweep A4）。
 *
 * 真 UI 现场：页面裸显 monitoring.timeRange / kbStatus / activeAlerts / normal /
 * cpuTrend 等键名（19+ 处）——视图消费的 37 个叶子键中 26 个在 zh/en/ja/fr
 * 四语全缺；因 fallbackLocale='zh' 且 zh 也缺，UI 直接上屏 "monitoring.activeAlerts"。
 *
 * 变异点：删任一补入键（或改回只留 11 键）→ 对应用例精确红。
 */
import { describe, it, expect } from 'vitest'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

// 视图 src/views/monitoring/index.vue 实际消费的 monitoring.* 叶子键（平铺部分）
// G 波：title 退役（h1 归 menu.monitoring，T9），本清单同步移除
const FLAT_KEYS = [
  'refresh', 'systemStatus', 'cpu', 'memory', 'appStatus',
  'activeConnections', 'totalRequests', 'errors', 'avgResponseTime',
  'kbStatus', 'totalDocs', 'totalChunks', 'indexSize', 'searchRequests',
  'alertStatus', 'activeAlerts', 'critical', 'warning', 'normal', 'info',
  'cpuTrend', 'memoryTrend', 'requestTrend', 'responseTrend', 'recentAlerts',
  'noData',
]

const NESTED: Array<[string, string[]]> = [
  ['timeRange', ['hour1', 'hour6', 'hour12', 'day1', 'day7']],
  ['alert', ['level', 'message', 'time', 'status']],
]

const LOCALES: Array<[string, Record<string, unknown>]> = [
  ['zh', zh],
  ['en', en],
  ['ja', ja],
  ['fr', fr],
]

describe('monitoring.* i18n 四语完整性（裸键直显修复）', () => {
  for (const [name, messages] of LOCALES) {
    it(`${name}: 视图消费的全部 monitoring.* 键存在且非空`, () => {
      const m = (messages as { monitoring?: Record<string, unknown> }).monitoring
      expect(m, `${name}.monitoring 段应存在`).toBeTruthy()

      for (const key of FLAT_KEYS) {
        const value = m![key]
        expect(typeof value, `${name}.monitoring.${key} 应为非空字符串`).toBe('string')
        expect((value as string).length).toBeGreaterThan(0)
        // 反例哨兵：值不应是键名本身（裸键直显现场）
        expect(value as string).not.toContain('monitoring.')
      }

      for (const [parent, keys] of NESTED) {
        const sub = m![parent] as Record<string, unknown> | undefined
        expect(sub, `${name}.monitoring.${parent} 段应存在`).toBeTruthy()
        for (const key of keys) {
          expect(typeof sub![key], `${name}.monitoring.${parent}.${key}`).toBe('string')
          expect((sub![key] as string).length).toBeGreaterThan(0)
        }
      }
    })
  }
})
