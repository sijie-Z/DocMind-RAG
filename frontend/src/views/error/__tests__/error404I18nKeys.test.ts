/**
 * 404 页 i18n 引用四语完整性（照 contentI18nKeys / usersI18nKeys 先例）。
 *
 * 背景：真 404 页（views/error/404.vue）从历史恢复时，其依赖的 error.404.* /
 * error.back* 键曾随页面一起被清删；本测试钉住四语键存在且非键名本身，
 * 防止某语静默回落中文（fallbackLocale=zh）或直接显示原始键名。
 *
 * 引用集 = views/error/404.vue 内静态 t('…')（grep 全集）+ router 中
 * NotFound 路由 meta.title 的键。
 *
 * 变异点：删任一键 / 改任一引用键名 → 对应用例精确红（missing 列表点名）。
 */
import { describe, it, expect } from 'vitest'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

const NOT_FOUND_REFS = [
  'error.404.title',
  'error.404.subtitle',
  'error.backToHome',
  'error.backToWorkbench',
  'error.back',
  'router.notFound',
]

const LOCALES: Array<[string, Record<string, unknown>]> = [
  ['zh', zh],
  ['en', en],
  ['ja', ja],
  ['fr', fr],
]

function resolve(messages: Record<string, unknown>, path: string): unknown {
  let node: unknown = messages
  for (const seg of path.split('.')) {
    if (node && typeof node === 'object' && seg in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[seg]
    } else {
      return undefined
    }
  }
  return node
}

describe('404 页 i18n 引用四语完整性', () => {
  for (const [name, messages] of LOCALES) {
    it(`${name}: 全部 ${NOT_FOUND_REFS.length} 个引用键可解析（非空、非键名本身）`, () => {
      const missing: string[] = []
      for (const ref of NOT_FOUND_REFS) {
        const value = resolve(messages, ref)
        if (typeof value !== 'string' || value.length === 0 || value === ref) {
          missing.push(ref)
        }
      }
      expect(missing.join(', '), `${name} 缺失键`).toBe('')
    })
  }
})
