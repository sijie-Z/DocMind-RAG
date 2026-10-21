/**
 * 监控页「时间范围」窗口（T8 · R3-A「时间范围选择器无效」修复的 CPU/内存侧）。
 *
 * 请求/响应趋势由后端 /monitoring/metrics/{type} 按 time_range 裁剪（T8 后端
 * 同步修复）；CPU/内存趋势来自 dashboard.trends.system，不经 /metrics ——
 * 需要在前端按同一窗口裁剪，选择器才对四张趋势图都真实生效。
 */

export const RANGE_WINDOW_MS: Record<string, number> = {
  '1h': 3600_000,
  '6h': 6 * 3600_000,
  '12h': 12 * 3600_000,
  '24h': 24 * 3600_000,
  '7d': 7 * 86400_000,
}

interface TimestampedPoint {
  timestamp: number
}

/** 按窗口裁剪采样点（timestamp 为秒级，与后端快照口径一致）；未知取值不裁剪。 */
export function filterPointsByTimeRange<T extends TimestampedPoint>(
  points: T[],
  timeRange: string,
  nowMs: number = Date.now()
): T[] {
  const windowMs = RANGE_WINDOW_MS[timeRange]
  if (!windowMs) return points
  const cutoffMs = nowMs - windowMs
  return points.filter((p) => p.timestamp * 1000 >= cutoffMs)
}
