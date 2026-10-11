/**
 * cron 表达式 → 人话预览。
 *
 * 对齐后端手写解析器（backend/app/services/scheduler/cron.py）的**有界子集**：
 * 5 字段（分 时 日 月 周）；支持 星号 / 单值 / 范围 / 步进（星号斜杠 n） / 逗号列表。
 * 子集之外的合法表达式（如范围+步进、分钟/小时列表、小时步进）**不硬译**——
 * 返回 null，由调用方回落展示原始表达式（诚实优先于猜测）。
 *
 * 文案走 vue-i18n 的 t（scheduler.cron.*），四语齐；返回的字符串可直接渲染。
 */

/** 单个字段的解析结果；other 表示 grammar 内但不在人话子集里（不硬译）。 */
type FieldValue =
  | { kind: 'star' }
  | { kind: 'step'; step: number }
  | { kind: 'single'; value: number }
  | { kind: 'list'; values: number[] }
  | { kind: 'range'; from: number; to: number }
  | { kind: 'other' }

type Translate = (key: string, params?: Record<string, unknown>) => string

const FIELD_BOUNDS: Array<[number, number]> = [
  [0, 59], // minute
  [0, 23], // hour
  [1, 31], // day of month
  [1, 12], // month
  [0, 7], // day of week（7 = 周日，按 Vixie 语义）
]

function parseItem(item: string, min: number, max: number): FieldValue | null {
  if (item === '*') return { kind: 'star' }
  const stepMatch = /^\*\/(\d+)$/.exec(item)
  if (stepMatch) {
    const step = Number(stepMatch[1])
    return step >= 1 && step <= max ? { kind: 'step', step } : null
  }
  const rangeStep = /^(\d+)-(\d+)\/\d+$/.exec(item)
  if (rangeStep) return { kind: 'other' } // 合法但不硬译
  const range = /^(\d+)-(\d+)$/.exec(item)
  if (range) {
    const from = Number(range[1])
    const to = Number(range[2])
    if (from < min || to > max || from > to) return null
    return { kind: 'range', from, to }
  }
  if (/^\d+$/.test(item)) {
    const value = Number(item)
    if (value < min || value > max) return null
    return { kind: 'single', value }
  }
  return null
}

function parseField(raw: string, index: number): FieldValue | null {
  const [min, max] = FIELD_BOUNDS[index]
  const items = raw.split(',')
  if (items.length === 0) return null
  const parsed: FieldValue[] = []
  for (const item of items) {
    const one = parseItem(item.trim(), min, max)
    if (one === null) return null
    parsed.push(one)
  }
  if (parsed.length === 1) return parsed[0]
  // 逗号列表：只接单值列表（混型/范围列表 → 不硬译）
  if (parsed.every((p) => p.kind === 'single')) {
    return { kind: 'list', values: parsed.map((p) => (p as { kind: 'single'; value: number }).value) }
  }
  return { kind: 'other' }
}

const isStar = (f: FieldValue) => f.kind === 'star'
const isSingle = (f: FieldValue, value: number) => f.kind === 'single' && f.value === value

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 周内数字集合 → 排序去重（7 归一到 0=周日）。 */
function normalizeDow(values: number[]): number[] {
  return [...new Set(values.map((v) => (v === 7 ? 0 : v)))].sort((a, b) => a - b)
}

function weekdayName(t: Translate, value: number): string {
  return t(`scheduler.cron.weekdays.${value}`)
}

/** 数字集合 → "周一至周五" / "周一、周三"（连续段折成范围）。 */
function describeDow(t: Translate, values: number[]): string | null {
  const normalized = normalizeDow(values)
  if (normalized.length === 0) return null
  if (normalized.length === 1) return weekdayName(t, normalized[0])
  const consecutive = normalized.every((v, i) => i === 0 || v === normalized[i - 1] + 1)
  if (consecutive) {
    return t('scheduler.cron.weekdayRange', {
      from: weekdayName(t, normalized[0]),
      to: weekdayName(t, normalized[normalized.length - 1]),
    })
  }
  return normalized.map((v) => weekdayName(t, v)).join(t('scheduler.cron.listSep'))
}

/** 日集合（月内日期）→ "1至5" / "1、5"；越界返回 null。 */
function describeDom(t: Translate, dom: FieldValue, min = 1, max = 31): string | null {
  if (dom.kind === 'single') return String(dom.value)
  if (dom.kind === 'range') {
    return t('scheduler.cron.dayRange', { from: String(dom.from), to: String(dom.to) })
  }
  if (dom.kind === 'list') {
    const sorted = [...new Set(dom.values)].sort((a, b) => a - b)
    const consecutive = sorted.every((v, i) => i === 0 || v === sorted[i - 1] + 1)
    if (consecutive && sorted.length > 1) {
      return t('scheduler.cron.dayRange', {
        from: String(sorted[0]),
        to: String(sorted[sorted.length - 1]),
      })
    }
    if (sorted.some((v) => v < min || v > max)) return null
    return sorted.map(String).join(t('scheduler.cron.listSep'))
  }
  return null
}

/**
 * 主入口：能诚实翻译 → 返回人话；不能（语法不合法 / 超子集 / 不常见组合）→ null。
 */
export function cronToHuman(expr: string, t: Translate): string | null {
  const fields = expr.trim().split(/\s+/)
  if (fields.length !== 5) return null
  const parsed = fields.map((f, i) => parseField(f, i))
  if (parsed.some((p) => p === null)) return null
  const [minute, hour, dom, month, dow] = parsed as FieldValue[]
  if (parsed.some((p) => (p as FieldValue).kind === 'other')) return null

  // 每分钟
  if ([minute, hour, dom, month, dow].every(isStar)) return t('scheduler.cron.everyMinute')

  // 每 N 分钟
  if (minute.kind === 'step' && [hour, dom, month, dow].every(isStar)) {
    if (minute.step === 1) return t('scheduler.cron.everyMinute')
    return t('scheduler.cron.everyNMinutes', { n: minute.step })
  }

  // 每小时第 m 分：分=单值、时=*、其余全 *
  if (minute.kind === 'single' && isStar(hour) && [dom, month, dow].every(isStar)) {
    return t('scheduler.cron.hourlyAtMinute', { m: minute.value })
  }

  // 以下都要求 分、时为单值
  if (minute.kind !== 'single' || hour.kind !== 'single') return null
  const time = `${pad2(hour.value)}:${pad2(minute.value)}`

  // 每天：日/月/周全 *
  if ([dom, month, dow].every(isStar)) {
    return t('scheduler.cron.dailyAt', { time })
  }

  // 每周：日=*、月=*、周限制
  if (isStar(dom) && isStar(month) && !isStar(dow)) {
    const values =
      dow.kind === 'list'
        ? dow.values
        : dow.kind === 'range'
          ? Array.from({ length: dow.to - dow.from + 1 }, (_, i) => dow.from + i)
          : dow.kind === 'single'
            ? [dow.value]
            : []
    const days = describeDow(t, values)
    if (days === null) return null
    return t('scheduler.cron.weeklyAt', { days, time })
  }

  // 每月：月=*、日限制、周=*
  if (isStar(month) && isStar(dow) && !isStar(dom)) {
    const days = describeDom(t, dom)
    if (days === null) return null
    return t('scheduler.cron.monthlyAt', { days, time })
  }

  // 每年：日=单值、月=单值、周=*
  if (dom.kind === 'single' && month.kind === 'single' && isStar(dow)) {
    return t('scheduler.cron.yearlyAt', {
      month: t(`scheduler.cron.months.${month.value}`),
      day: dom.value,
      time,
    })
  }

  return null
}
