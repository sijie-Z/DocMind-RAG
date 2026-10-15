/**
 * 根级健康检查（GET /health，非 /api/v1 前缀）。
 *
 * 用途：关于页/聊天侧栏的版本号动态读取（ISS-2 数字对账）——后端
 * `APP_VERSION` 是唯一版本真源，/health 的 version 字段是它的运行时可读出口。
 *
 * 说明：共享 axios 实例的 baseURL 是 /api/v1，拼根级路径会错；且 /health 被
 * 响应中间件排除、不走统一响应壳——所以这里直接用 fetch（也不触发全局
 * loading/鉴权头）。dev 与生产分别经 vite proxy / nginx `location = /health`
 * 转发到后端。
 */
export interface HealthResponse {
  status: string
  version: string
}

export const getHealth = async (): Promise<HealthResponse> => {
  const res = await fetch('/health')
  if (!res.ok) throw new Error(`GET /health failed: ${res.status}`)
  return (await res.json()) as HealthResponse
}
