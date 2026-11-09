import dayjs from 'dayjs'
import i18n from '@/locales'

// 后端 MySQL DATETIME 以 UTC 存储，JSON 里是「无时区 ISO」（如 2026-11-08T19:15:33）。
// ECMAScript 会把这类字符串按**本地时间**解析——UTC+8 下整体早 8 小时。
// 仅当缺时区且带时间部分时补 Z；带 Z/offset 的字符串、纯日期（按规范即 UTC）不动。
// 支持的格式：后端 Python isoformat() 的 T 分隔（秒/微秒可选）与空格分隔变体；
// 小写 t/UTC 后缀等非后端产物交给原生解析（与既有行为一致）。
const NAIVE_ISO_WITH_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$/

export const parseApiDate = (value: string | Date | undefined | null): Date | null => {
  if (!value) return null
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (!text) return null
  const normalized = NAIVE_ISO_WITH_TIME.test(text)
    ? `${text.replace(' ', 'T')}Z`
    : text
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

type TParams = Record<string, string | number>
const t = (key: string, params: TParams = {}): string =>
  (i18n.global.t as (k: string, p?: TParams) => string)(key, params)

const DATE_LOCALE_TAGS: Record<string, string> = {
  zh: 'zh-CN',
  en: 'en-US',
  ja: 'ja-JP',
  fr: 'fr-FR',
}

export const formatDate = (dateString: string | Date | undefined | null): string => {
  if (!dateString) return '-'
  
  const date = parseApiDate(dateString)
  
  if (!date) {
    return '-'
  }
  
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const diffMinutes = Math.floor(diff / (1000 * 60))
  const diffHours = Math.floor(diff / (1000 * 60 * 60))
  const diffDays = Math.floor(diff / (1000 * 60 * 60 * 24))
  
  if (diff < 0) {
    // 未来时间：显示具体日期时间，而非"刚刚"
    return dayjs(date).format('YYYY-MM-DD HH:mm')
  } else if (diffMinutes < 1) {
    return t('common.relative.justNow')
  } else if (diffMinutes < 60) {
    return t('common.relative.minutesAgo', { count: diffMinutes })
  } else if (diffHours < 24) {
    return t('common.relative.hoursAgo', { count: diffHours })
  } else if (diffDays < 7) {
    return t('common.relative.daysAgo', { count: diffDays })
  } else if (diffDays < 30) {
    const weeks = Math.floor(diffDays / 7)
    return t('common.relative.weeksAgo', { count: weeks })
  } else {
    const locale = String(i18n.global.locale.value || 'zh')
    return date.toLocaleString(DATE_LOCALE_TAGS[locale] || 'en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  }
}

export const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export const formatDuration = (seconds: number): string => {
  if (seconds < 60) {
    return t('common.duration.seconds', { seconds })
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return t('common.duration.minutesSeconds', { minutes, seconds: remainingSeconds })
  } else {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return t('common.duration.hoursMinutes', { hours, minutes })
  }
}

export const truncateText = (text: string, maxLength: number): string => {
  if (text.length <= maxLength) return text
  return text.substring(0, maxLength) + '...'
}

/**
 * 绝对日期时间（跟随界面语言）。用于审计/管理列表：相对时间不会自动刷新，
 * 且共享的 formatDate 文案固定中文，英文/日文界面会回退。
 */
export const formatDateTime = (
  dateString: string | Date | undefined | null,
  locale = 'zh'
): string => {
  if (!dateString) return '-'
  const date = parseApiDate(dateString)
  if (!date) return '-'
  try {
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    }).format(date)
  } catch {
    return date.toLocaleString()
  }
}

export const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch (err) {
    // 降级方案
    const textArea = document.createElement('textarea')
    textArea.value = text
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    try {
      document.execCommand('copy')
      document.body.removeChild(textArea)
      return true
    } catch (err) {
      document.body.removeChild(textArea)
      return false
    }
  }
}
