/**
 * 演示账号快速登录配置（仅演示环境）。
 *
 * 账号列表在构建时通过 `VITE_DEMO_ACCOUNTS` 注入（JSON 数组：
 * `[{ "label": "核验演示", "username": "demo_reviewer", "password": "..." }]`），
 * 未配置时返回空数组，登录页不渲染任何体验入口——仓库里不写死演示口令。
 * 解析失败按“未配置”处理，避免一个坏的环境变量把登录页打挂。
 */
export interface DemoAccount {
  label: string
  username: string
  password: string
}

export function parseDemoAccounts(raw: unknown): DemoAccount[] {
  if (typeof raw !== 'string' || !raw.trim()) return []
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    return []
  }
  if (!Array.isArray(data)) return []
  return data
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    .map((item) => ({
      label:
        typeof item.label === 'string' && item.label.trim()
          ? item.label.trim()
          : typeof item.username === 'string'
            ? item.username
            : '',
      username: typeof item.username === 'string' ? item.username : '',
      password: typeof item.password === 'string' ? item.password : ''
    }))
    .filter((item) => item.username && item.password)
}
