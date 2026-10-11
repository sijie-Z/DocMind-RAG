/**
 * 运行成本估算（体验第四波）——纯函数、零依赖、可测。
 *
 * 诚实口径：agent 运行只记录 total_tokens（无 input/output 拆分），因此估算
 * 采用「输入/输出单价的加权平均」按总量折算；结果一律带"估算"标注（UI 侧）。
 * 价目表：内置默认 DeepSeek 单价（美元 / 百万 token），可通过 localStorage
 * `docmind.agent.costTable`（JSON 对象）整体覆盖合并——可配置，无需改代码。
 */

export interface ModelPrice {
  inputPerM: number
  outputPerM: number
}

/** 默认价目表（美元 / 百万 token，估算用；DeepSeek 公开单价口径）。 */
export const DEFAULT_PRICE_TABLE: Record<string, ModelPrice> = {
  'deepseek-v4-flash': { inputPerM: 0.28, outputPerM: 0.42 },
  'deepseek-chat': { inputPerM: 0.27, outputPerM: 1.1 },
  'deepseek-reasoner': { inputPerM: 0.55, outputPerM: 2.19 },
  // 未收录模型的兜底单价（与默认模型同档，避免静默按 0 计）
  default: { inputPerM: 0.28, outputPerM: 0.42 },
}

export const COST_TABLE_STORAGE_KEY = 'docmind.agent.costTable'

/** 读取价目表：默认表 ⊕ localStorage 覆盖（非法 JSON / 坏形状 → 忽略覆盖）。
 *  F5（评审）：数值校验用 `Number.isFinite` + 非负——`typeof === 'number'`
 *  会放过 NaN/Infinity（JSON 溢出如 1e999 即得 Infinity），使估算显示 ≈$0.0000
 *  而不落 default 档；负单价则会让成本被静默隐藏。 */
export function getPriceTable(): Record<string, ModelPrice> {
  const table: Record<string, ModelPrice> = { ...DEFAULT_PRICE_TABLE }
  try {
    const raw = localStorage.getItem(COST_TABLE_STORAGE_KEY)
    if (!raw) return table
    const parsed = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return table
    for (const [model, price] of Object.entries(parsed)) {
      const inP = (price as Partial<ModelPrice>)?.inputPerM
      const outP = (price as Partial<ModelPrice>)?.outputPerM
      if (
        typeof inP === 'number' && typeof outP === 'number'
        && Number.isFinite(inP) && Number.isFinite(outP)
        && inP >= 0 && outP >= 0
      ) {
        table[model] = { inputPerM: inP, outputPerM: outP }
      }
    }
  } catch {
    // localStorage 不可用 / JSON 非法：用默认表（估算不因配置坏形状失效）
  }
  return table
}

/**
 * 估算一次运行的成本（美元）。totalTokens 缺失/非有限/≤0 → 0。
 * 未收录模型 → 兜底 `default` 档（不返回 null，估算不静默消失）。
 */
export function estimateCostUsd(model: string, totalTokens: number | undefined | null): number {
  if (typeof totalTokens !== 'number' || !Number.isFinite(totalTokens) || totalTokens <= 0) return 0
  const table = getPriceTable()
  const price = table[model] || table.default
  if (!price) return 0
  const blendedPerM = (price.inputPerM + price.outputPerM) / 2
  return (totalTokens / 1_000_000) * blendedPerM
}

/** 格式化成本：四位小数，极小值给下界提示（`<$0.0001`）。 */
export function formatCostUsd(usd: number): string {
  if (!Number.isFinite(usd) || usd <= 0) return '$0.0000'
  if (usd < 0.0001) return '<$0.0001'
  return `$${usd.toFixed(4)}`
}

/** 格式化时长（mm:ss，超 1 小时 h:mm:ss）。 */
export function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000))
  const s = totalSec % 60
  const m = Math.floor(totalSec / 60) % 60
  const h = Math.floor(totalSec / 3600)
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}
