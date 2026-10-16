/**
 * 宽容解包（T11 · 方案 B）。
 *
 * 背景：成功路径不使用全局信封，历史混有三种形态（口径见 types/common.ts）：
 *   ① 裸结构        —— 响应体即业务体（如 GET /notifications/）
 *   ② {data}        —— 至多一层 data（如 GET /documents/{id}）
 *   ③ {data:{data}} —— 外皮 + 分页体（如 GET /api/v1/knowledge/）
 *
 * 本工具把任意一种形态归一为「业务体」，供各调用点统一消费；不认识/无信封的
 * 输入原样返回（宽容，绝不抛错）。
 *
 * 解包规则（至多剥 2 层）：
 *   对象含 data 键，且满足任一条件即视为一层信封，剥去其 data：
 *     a) 出现信封标记字段（success / code —— 错误信封与 knowledge 列表外皮）；
 *     b) 该对象仅剩 data 一个键（{data} 与 {data:{data}} 的最小形态，
 *        也是本仓既有测试 mock 的手法）。
 *   其余（裸业务体、{data, total, …} 多键分页体、message-only 回执）保持不动。
 *
 * 输入兼容 axios 响应对象（凭 status:number + headers/statusText/config 常规字段
 * 识别，剥其 .data 后按上表继续）与裸载荷；{data: body} 这类最小假响应也能被
 * 识别（走 b 规则多剥一层）。所有未知形状原样透传。
 *
 * 用法：
 *   apiUnwrap<NotificationListResponse>(res)   // 裸形态 → 原样
 *   apiUnwrap<DocumentDetail>(res)             // {data} → 业务体
 *   apiUnwrap<KnowledgeBaseListResponse>(res)  // 双层 → 分页体（total 等保留）
 */
const MAX_UNWRAP_DEPTH = 2

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false
  const proto = Object.getPrototypeOf(value)
  return proto === Object.prototype || proto === null
}

/** axios 响应对象的保守识别：data + 数值 status + 常规响应字段之一。 */
function isAxiosLike(input: unknown): input is { data: unknown } {
  if (!isPlainObject(input) || !('data' in input)) return false
  return typeof input.status === 'number' && ('headers' in input || 'statusText' in input || 'config' in input)
}

export function apiUnwrap<T = unknown>(input: unknown): T {
  let value: unknown = input
  if (isAxiosLike(input)) value = input.data

  for (let depth = 0; depth < MAX_UNWRAP_DEPTH; depth += 1) {
    if (!isPlainObject(value) || !('data' in value)) break
    const hasMarker = value.success !== undefined || value.code !== undefined
    const soleDataKey = Object.keys(value).length === 1
    if (!hasMarker && !soleDataKey) break
    value = value.data
  }

  return value as T
}
