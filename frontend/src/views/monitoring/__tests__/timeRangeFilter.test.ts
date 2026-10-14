/**
 * T8 监控「时间范围选择器」前端过滤判别用例（CPU/内存趋势侧）。
 *
 * 复核证据：选择器切换 1h/7d 发出的请求响应体逐字节相同（后端忽略
 * time_range）；CPU/内存趋势来自 dashboard.trends.system，不经 /metrics，
 * 需要按窗口在前端裁剪，选择器才对四张趋势图都真实生效。
 * 变异点：窗口表数值错 / 过滤方向反 → 对应用例红。
 */
import { describe, it, expect } from 'vitest'
import { filterPointsByTimeRange, RANGE_WINDOW_MS } from '../timeRange'

const NOW = 1_800_000_000_000 // 固定基准毫秒

function pt(msAgo: number, v = 1) {
  return { timestamp: Math.floor((NOW - msAgo) / 1000), value: v }
}

describe('filterPointsByTimeRange · 窗口裁剪', () => {
  it('1h：保留 1 小时内、剔除 2 小时前', () => {
    const points = [pt(60_000, 1), pt(30 * 60_000, 2), pt(2 * 3600_000, 3)]
    const out = filterPointsByTimeRange(points, '1h', NOW)
    expect(out.map((p) => p.value)).toEqual([1, 2])
  })

  it('24h 保留 2 天前的剔除 / 7d 全量保留', () => {
    const points = [pt(60_000, 1), pt(2 * 3600_000, 2), pt(20 * 3600_000, 3), pt(2 * 86400_000, 4)]
    expect(filterPointsByTimeRange(points, '24h', NOW).map((p) => p.value)).toEqual([1, 2, 3])
    expect(filterPointsByTimeRange(points, '7d', NOW).map((p) => p.value)).toEqual([1, 2, 3, 4])
  })

  it('未知取值/空数组：不裁剪，返回原样', () => {
    const points = [pt(100 * 86400_000, 1)]
    expect(filterPointsByTimeRange(points, '1y', NOW)).toEqual(points)
    expect(filterPointsByTimeRange([], '1h', NOW)).toEqual([])
  })

  it('窗口表与页面选择器取值一一对应', () => {
    expect(Object.keys(RANGE_WINDOW_MS).sort()).toEqual(['12h', '1h', '24h', '6h', '7d'])
    expect(RANGE_WINDOW_MS['1h']).toBe(3600_000)
    expect(RANGE_WINDOW_MS['7d']).toBe(7 * 86400_000)
  })
})
